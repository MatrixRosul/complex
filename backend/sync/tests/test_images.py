"""
Фото товару: прибирання ЗАСТАРІЛИХ фото після синку (SYNC.md §7).

Сценарій, заради якого існує цей файл: замовник замінив у прайсі мертве посилання на нове.
Синк качає нове фото — а старе лишається в галереї, з меншим `position`, і далі показується
головним. Людина змінила фото, на сайті не змінилось НІЧОГО.

Мережі тут немає: `httpx.Client` ходить у `MockTransport`, а SSRF-guard (він резолвить DNS
по-справжньому) підмінений — його перевіряє test_tasks.py.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

import httpx
import pytest
from django.test import TestCase
from PIL import Image

from catalog.enums import ProductSource
from catalog.models import Product, ProductImage
from sync import tasks
from sync.models import SyncLogEntry, SyncRun
from sync.services import rebuild_denorm, url_hash
from sync.tests.conftest import MemorySheetsClient
from sync.tests.test_sync import make_client, row, sync

pytestmark = pytest.mark.django_db

OLD = "https://cdn.example.com/old.jpg"
NEW = "https://cdn.example.com/new.jpg"
SECOND = "https://cdn.example.com/second.jpg"


def _png(color: tuple[int, int, int]) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (24, 24), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def cdn(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """CDN у пам'яті. `cdn["dead"].add(url)` → 404 (мертве посилання в прайсі)."""
    dead: set[str] = set()
    palette = {OLD: (10, 10, 10), NEW: (200, 30, 30), SECOND: (30, 200, 30)}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in dead:
            return httpx.Response(404)
        body = _png(palette.get(url, (99, 99, 99)))
        return httpx.Response(200, content=body, headers={"content-type": "image/png"})

    real_client = httpx.Client

    def factory(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(tasks.httpx, "Client", factory)
    # SSRF-guard резолвить DNS по-справжньому — у тестах мережі не буде взагалі.
    monkeypatch.setattr(tasks, "assert_safe_url", lambda url: None)
    # `.delay` пішла б у брокер; воркера черги `default` грає прямий виклик денормалізації —
    # інакше тест не побачив би головного, що є в цій історії: що на сайті ЗМІНИЛОСЬ фото.
    monkeypatch.setattr(tasks.rebuild_product_denorm, "delay", rebuild_denorm)
    return {"dead": dead}


def price(photo: str, *, name: str = "Холодильник Gorenje NRK", price_uah: str = "1000") -> Any:
    return row(
        name=name,
        price=price_uah,
        qty="5",
        currency="UAH",
        cat="5609731",
        brand="Gorenje",
        country="Словенія",
        photo=photo,
        sku="1",
    )


def client_with(photo: str) -> MemorySheetsClient:
    return make_client(uah=[price(photo)])


def drain(run: SyncRun, *, commit: bool = True) -> None:
    """Імітує воркер черги `images`.

    У тестах транзакція не комітиться, тому `on_commit` сам не спрацює: задачу викликаємо
    напряму (рівно з тими аргументами, з якими її ставить синк), а `on_commit`-колбеки
    доганяємо вручну. `commit=False` — зупинитись ДО коміту: рівно так перевіряється, що
    файли зі сховища видаляються ПОЗА транзакцією.
    """

    def _download() -> None:
        ids = list(
            ProductImage.objects.filter(last_seen_run=run.id, downloaded_at__isnull=True)
            .order_by("position", "id")
            .values_list("id", flat=True)
        )
        for image_id in ids:
            tasks.download_product_image(image_id=image_id, run_id=str(run.id))

    if not commit:
        _download()
        return
    with TestCase.captureOnCommitCallbacks(execute=True):
        _download()


def gallery(product: Product) -> list[tuple[str, int, bool]]:
    return [
        (i.source_url, i.position, i.is_main)
        for i in product.images.all().order_by("position", "id")
    ]


# ---------------------------------------------------------------------------
# ⚡ Головний регресійний тест: постачальник ЗАМІНИВ URL
# ---------------------------------------------------------------------------


def test_replaced_url_deletes_old_image_and_new_one_becomes_main(
    source: Any, site_settings: Any, catalog_refs: Any, cdn: dict[str, Any]
) -> None:
    """Замовник замінив мертве посилання на нове → в галереї МАЄ лишитись рівно нове фото.

    Без прибирання: старе фото лишається назавжди, з меншим `position` далі є головним —
    і на сайті не змінюється НІЧОГО.
    """
    run1 = sync(source, client_with(OLD))
    drain(run1)
    product = Product.objects.get(sku="1")
    assert gallery(product) == [(OLD, 0, True)]

    run2 = sync(source, client_with(NEW))
    drain(run2)

    assert gallery(product) == [(NEW, 0, True)]
    assert not ProductImage.objects.filter(source_url_hash=url_hash(OLD)).exists()

    product.refresh_from_db()
    assert product.main_image_url  # головне фото є і в денормалізації


def test_purge_is_logged_to_sync_log(
    source: Any, site_settings: Any, catalog_refs: Any, cdn: dict[str, Any]
) -> None:
    """Прибирання видно в журналі синхронізацій — інакше видалення даних відбувається наосліп."""
    run1 = sync(source, client_with(OLD))
    drain(run1)

    run2 = sync(source, client_with(NEW))
    drain(run2)

    entry = SyncLogEntry.objects.get(action=SyncLogEntry.Action.IMAGE_PURGED)
    assert entry.level == SyncLogEntry.Level.INFO
    assert entry.run_id == run2.id
    assert entry.sku == "1"
    assert OLD in entry.payload["urls"]


def test_purge_deletes_files_from_storage_outside_transaction(
    source: Any,
    site_settings: Any,
    catalog_refs: Any,
    cdn: dict[str, Any],
    django_capture_on_commit_callbacks: Any,
) -> None:
    """Файли зі сховища теж прибираються — але ПІСЛЯ коміту (R2 не вміє rollback)."""
    from django.core.files.storage import default_storage

    run1 = sync(source, client_with(OLD))
    drain(run1)
    old = ProductImage.objects.get(source_url_hash=url_hash(OLD))
    paths = [old.file.name, old.file_card.name, old.file_thumb.name, old.file_large.name]
    assert all(default_storage.exists(p) for p in paths)  # оригінал + 3 деривативи

    run2 = sync(source, client_with(NEW))
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        drain(run2, commit=False)

    # Рядок у БД уже немає, а файли — ще на місці: у транзакції сховище не чіпаємо.
    assert not ProductImage.objects.filter(source_url_hash=url_hash(OLD)).exists()
    assert all(default_storage.exists(p) for p in paths)

    for callback in callbacks:  # COMMIT
        callback()
    assert not any(default_storage.exists(p) for p in paths)


# ---------------------------------------------------------------------------
# Друге фото, порядок, головне фото
# ---------------------------------------------------------------------------


def test_second_photo_added_keeps_price_order(
    source: Any, site_settings: Any, catalog_refs: Any, cdn: dict[str, Any]
) -> None:
    """Постачальник додав друге фото → два фото, порядок за прайсом, головне — перше."""
    run1 = sync(source, client_with(OLD))
    drain(run1)

    run2 = sync(source, make_client(uah=[price(f"{OLD}, {SECOND}")]))
    drain(run2)

    product = Product.objects.get(sku="1")
    assert gallery(product) == [(OLD, 0, True), (SECOND, 1, False)]


def test_photo_prepended_reorders_sheet_images_by_price(
    source: Any, site_settings: Any, catalog_refs: Any, cdn: dict[str, Any]
) -> None:
    """Постачальник поставив нове фото ПЕРШИМ → позиції беруться з прайсу, а не з дати створення.

    Без оновлення `position` наявного фото обидва мали б `position=0`, порядок вирішувався б
    за `id` — і «перше фото з прайсу» опинилось би другим у галереї.
    """
    run1 = sync(source, client_with(SECOND))
    drain(run1)

    run2 = sync(source, make_client(uah=[price(f"{NEW}, {SECOND}")]))
    drain(run2)

    product = Product.objects.get(sku="1")
    assert [url for url, _, _ in gallery(product)] == [NEW, SECOND]


def test_product_never_ends_up_without_main_image(
    source: Any, site_settings: Any, catalog_refs: Any, cdn: dict[str, Any]
) -> None:
    """Видалили головне фото → головним стає перше з тих, що лишились. Фото є — головне є."""
    run1 = sync(source, make_client(uah=[price(f"{OLD}, {SECOND}")]))
    drain(run1)
    product = Product.objects.get(sku="1")
    assert product.images.get(is_main=True).source_url == OLD

    run2 = sync(source, client_with(SECOND))  # прайс лишив ТІЛЬКИ друге фото
    drain(run2)

    assert gallery(product) == [(SECOND, 0, True)]


def test_dead_new_url_keeps_the_old_photo(
    source: Any, site_settings: Any, catalog_refs: Any, cdn: dict[str, Any]
) -> None:
    """Нове посилання мертве (404) → СТАРЕ фото лишається.

    Краще старе фото, ніж порожня картка: прибирати можна лише те, чому вже є заміна НА ДИСКУ.
    """
    run1 = sync(source, client_with(OLD))
    drain(run1)

    cdn["dead"].add(NEW)
    run2 = sync(source, client_with(NEW))
    drain(run2)

    product = Product.objects.get(sku="1")
    urls = {i.source_url for i in product.images.all()}
    assert OLD in urls
    assert product.images.get(is_main=True).source_url == OLD


# ---------------------------------------------------------------------------
# Недоторканне: ручні фото і порожня комірка «Фото»
# ---------------------------------------------------------------------------


def test_manual_image_is_never_deleted_by_sync(
    source: Any, site_settings: Any, catalog_refs: Any, cdn: dict[str, Any]
) -> None:
    """Ручне фото (source=manual) не видаляється синком НІКОЛИ — його додала людина."""
    run1 = sync(source, client_with(OLD))
    drain(run1)
    product = Product.objects.get(sku="1")

    manual = ProductImage.objects.create(
        product=product,
        source=ProductSource.MANUAL,
        position=5,
        alt="Знято на складі",
        downloaded_at=None,
    )

    run2 = sync(source, client_with(NEW))
    drain(run2)

    manual.refresh_from_db()  # живий
    assert manual.source == ProductSource.MANUAL
    assert manual.position == 5  # позицію ручного фото синк теж не чіпає
    assert not ProductImage.objects.filter(source_url_hash=url_hash(OLD)).exists()
    assert set(product.images.values_list("source", flat=True)) == {
        ProductSource.SHEET,
        ProductSource.MANUAL,
    }


def test_empty_photo_cell_does_not_touch_existing_images(
    source: Any, site_settings: Any, catalog_refs: Any, cdn: dict[str, Any]
) -> None:
    """Порожня комірка «Фото» ≠ «видали всі фото»: наявні фото лишаються недоторканими."""
    run1 = sync(source, client_with(OLD))
    drain(run1)
    product = Product.objects.get(sku="1")

    run2 = sync(source, client_with(""))
    drain(run2)

    assert gallery(product) == [(OLD, 0, True)]
    assert not SyncLogEntry.objects.filter(action=SyncLogEntry.Action.IMAGE_PURGED).exists()


def test_product_absent_from_this_run_keeps_its_images(
    source: Any, site_settings: Any, catalog_refs: Any, cdn: dict[str, Any]
) -> None:
    """Товар зник з прайсу (деактивація) → фото НЕ чіпаємо: він може повернутись завтра."""
    run1 = sync(source, client_with(OLD))
    drain(run1)
    product = Product.objects.get(sku="1")

    other = row(
        name="Телевізор Bosch T",
        price="2000",
        qty="3",
        currency="UAH",
        cat="5609711",
        brand="Bosch",
        country="Німеччина",
        photo=SECOND,
        sku="2",
    )
    run2 = sync(source, make_client(uah=[other]), force=True)
    drain(run2)

    assert gallery(product) == [(OLD, 0, True)]
