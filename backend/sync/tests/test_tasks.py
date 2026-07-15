"""Тести задач: SSRF-guard фото, дедуп, reap, denorm, heal."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from catalog.enums import Availability
from catalog.models import Product, ProductImage
from sync.models import SyncRun
from sync.services import rebuild_denorm, url_hash
from sync.tasks import (
    UnsafeImageURL,
    assert_safe_url,
    download_product_image,
    heal_denorm,
    reap_stale_runs,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def product(catalog_refs) -> Product:
    cat = catalog_refs["categories"]["5609731"]
    p = Product(
        sku="P1",
        name="Холодильник Gorenje X",
        slug="p1",
        category=cat,
        base_price=Decimal("100"),
        price=Decimal("100"),
        availability=Availability.IN_STOCK,
    )
    if hasattr(p, "name_uk"):
        p.name_uk, p.slug_uk = p.name, p.slug
    p.save()
    return p


# ---------------------------------------------------------------------------
# SSRF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/a.jpg",  # не https
        "https://127.0.0.1/a.jpg",  # loopback
        "https://10.0.0.5/a.jpg",  # private
        "https://169.254.169.254/latest/meta-data/",  # ← метадані хмари
        "https://192.168.1.1/a.jpg",
        "https://[::1]/a.jpg",
    ],
)
def test_ssrf_private_ip_blocked(url: str) -> None:
    """URL у колонці «Фото» пише людина. `169.254.169.254` — стандартний спосіб винести
    креденшели з хмари, а не гіпотетика."""
    with pytest.raises(UnsafeImageURL):
        assert_safe_url(url)


def test_ssrf_blocked_url_marks_image_failed_without_retry(product: Product) -> None:
    image = ProductImage.objects.create(
        product=product,
        source="sheet",
        source_url="https://127.0.0.1/evil.jpg",
        source_url_hash=url_hash("https://127.0.0.1/evil.jpg"),
    )
    assert download_product_image(image_id=image.pk) == "unsafe"

    image.refresh_from_db()
    assert image.failed_reason.startswith("ssrf:")
    assert image.next_retry_at is None  # невідновна помилка — БЕЗ ретраїв
    assert image.downloaded_at is None


def test_url_hash_is_stable() -> None:
    assert url_hash("https://a/b.jpg") == url_hash(" https://a/b.jpg ")
    assert url_hash("https://a/b.jpg") != url_hash("https://a/c.jpg")


# ---------------------------------------------------------------------------
# ⚡ Регресія: мертве фото НЕ МОЖЕ забрати з собою характеристики товару
# ---------------------------------------------------------------------------


def test_failed_image_still_rebuilds_denorm(
    product: Product,
    catalog_refs,
    django_capture_on_commit_callbacks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Фото не завантажилось → характеристики товару все одно мають побудуватись.

    БУЛО: `rebuild_product_denorm` ставився ЄДИНОГО разу — в успішному фіналі
    download_product_image(). Тобто specs_json був побічним ефектом вдало завантаженої
    КАРТИНКИ. Одне мертве посилання (404 / SSL / SSRF / таймаут) — і товар назавжди
    лишався з порожніми характеристиками: «Характеристики уточнюються» в картці,
    порожня колонка в порівнянні, товар не знаходиться фільтрами.
    У реальній базі це дало 100% збіг: усі 29 товарів з failed_reason<>'' мали specs_json = [].
    """
    from catalog.models import Attribute, ProductAttributeValue, Unit
    from sync import tasks

    # `.delay` пішла б у брокер; воркера черги `default` грає прямий виклик денормалізації.
    monkeypatch.setattr(tasks.rebuild_product_denorm, "delay", rebuild_denorm)

    attr = Attribute.objects.create(
        code="obiem",
        name="Загальний об'єм",
        group=catalog_refs["group"],
        value_type=Attribute.ValueType.NUMBER,
        unit=Unit.objects.get(code="l"),
    )
    ProductAttributeValue.objects.create(
        product=product, attribute=attr, value_number=Decimal("331")
    )
    Product.objects.filter(pk=product.pk).update(denorm_dirty=True, specs_json={})

    # Мертве посилання: SSRF-guard відхиляє loopback, як і будь-яку іншу невідновну помилку.
    image = ProductImage.objects.create(
        product=product,
        source="sheet",
        source_url="https://127.0.0.1/dead.jpg",
        source_url_hash=url_hash("https://127.0.0.1/dead.jpg"),
    )

    with django_capture_on_commit_callbacks(execute=True):
        assert download_product_image(image_id=image.pk) == "unsafe"

    image.refresh_from_db()
    assert image.failed_reason  # фото справді впало…

    product.refresh_from_db()
    # …але характеристики побудовані: денормалізація від фото НЕ ЗАЛЕЖИТЬ.
    assert product.specs_json, "мертве фото не має лишати товар без характеристик"
    assert product.specs_num == {"obiem": 331.0}
    assert product.denorm_dirty is False


# ---------------------------------------------------------------------------
# reap / heal
# ---------------------------------------------------------------------------


def test_reap_stale_run_marks_failed() -> None:
    """OOM-kill воркера: advisory-lock звільнився сам, а рядок лишився «Виконується» назавжди."""
    run = SyncRun.objects.create(kind=SyncRun.Kind.SHEETS_PRICES, status=SyncRun.Status.RUNNING)
    SyncRun.objects.filter(pk=run.pk).update(started_at=timezone.now() - timedelta(hours=2))

    assert reap_stale_runs(older_than_minutes=20) == 1
    run.refresh_from_db()
    assert run.status == SyncRun.Status.FAILED
    assert "reaped" in run.traceback
    assert run.finished_at is not None


def test_reap_does_not_touch_fresh_runs() -> None:
    SyncRun.objects.create(kind=SyncRun.Kind.SHEETS_PRICES, status=SyncRun.Status.RUNNING)
    assert reap_stale_runs(older_than_minutes=20) == 0


def test_heal_denorm_picks_up_dirty_products(product: Product) -> None:
    """Задача загубилась між COMMIT і .delay() → denorm_dirty уже в БД → товар оживає сам."""
    Product.objects.filter(pk=product.pk).update(denorm_dirty=True, denorm_at=None)

    assert heal_denorm(limit=10) == 1
    product.refresh_from_db()
    assert product.denorm_dirty is False
    assert product.denorm_at is not None
    assert f"avail:{Availability.IN_STOCK}" in product.filter_tokens


def test_rebuild_denorm_builds_tokens_and_specs(product: Product, catalog_refs) -> None:
    from catalog.models import Attribute, AttributeOption, ProductAttributeValue, Unit

    attr = Attribute.objects.create(
        code="obiem",
        name="Загальний об'єм",
        group=catalog_refs["group"],
        value_type=Attribute.ValueType.NUMBER,
        unit=Unit.objects.get(code="l"),
    )
    color = Attribute.objects.create(
        code="color", name="Колір", group=catalog_refs["group"], value_type="option"
    )
    option = AttributeOption.objects.create(attribute=color, value="Чорний", slug="chornyi")
    ProductAttributeValue.objects.create(
        product=product, attribute=attr, value_number=Decimal("331")
    )
    ProductAttributeValue.objects.create(product=product, attribute=color, option=option)

    assert rebuild_denorm([product.pk]) == 1
    product.refresh_from_db()

    assert product.specs_num == {"obiem": 331.0}
    assert "color:chornyi" in product.filter_tokens
    # ⚠️ Одиниця клеїться до ЗНАЧЕННЯ, а не до назви: «Загальний об'єм» → «331 л»
    spec = next(s for s in product.specs_json if s["code"] == "obiem")
    assert spec["n"] == "Загальний об'єм"
    assert spec["u"] == "л"
    assert spec["v"] == "331"
    assert product.denorm_dirty is False
