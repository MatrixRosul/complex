"""
Кнопки адмінки, які ставлять Celery-задачі, — ЧИ ВОНИ СПРАВДІ ЇХ СТАВЛЯТЬ.

Передісторія (17.07.2026). Адмінку писали до того, як з'явились `*/tasks.py`, тому кнопки
ходили в задачі через «розумний» диспетчер за dotted-path:

    def _dispatch_task(dotted_path, **kwargs):
        try:
            task = getattr(importlib.import_module(module_path), task_name)
        except (ImportError, AttributeError):
            return False          # ← «задачі ще немає, це нормально»
        task.delay(**kwargs)

Задачі з'явились — і диспетчер тихо став пасткою, бо перевіряв лише НАЯВНІСТЬ ІМЕНІ:

  · «Відкотити прогін» звала `sync.tasks.rollback_run`. Задача зветься `rollback_sync_run`,
    а `rollback_run` у тому модулі — звичайна функція, імпортована з services. getattr її
    ЗНАХОДИВ, except не спрацьовував, і `.delay` падав AttributeError у лице менеджеру.
  · «Оновити зараз» передавала `price_source_id`, а задача приймає `source_id`.
  · «Перекласти заново» звала неіснуючу `translate_entries` — тут except спрацьовував, тож
    кнопка МОВЧКИ не робила нічого, вже перевівши записи в PENDING. Зіпсовані дані, нуль роботи.

Тому тут перевіряється не «мок викликався», а ДВІ речі, на яких воно горіло:
  1. кнопка ставить САМЕ ту задачу (імпорт зверху — впаде на старті, якщо ім'я зникне);
  2. іменовані аргументи РЕАЛЬНО існують у сигнатурі задачі — просто мок проковтнув би
     `price_source_id=` і тест був би зелений, як і був.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from catalog.admin import ProductAdmin
from catalog.enums import Availability
from catalog.models import Brand, Category, Product
from feeds.admin import FeedArtifactAdmin
from feeds.models import FeedArtifact
from feeds.tasks import generate_hotline_feed
from sync.admin import SyncRunAdmin
from sync.models import PriceSource, ProductPriceSnapshot, SyncRun
from sync.tasks import rebuild_product_denorm, rollback_sync_run, sync_prices
from translation.admin import TranslationEntryAdmin
from translation.models import TranslationEntry, TranslationKind, TranslationStatus
from translation.tasks import translate_pending_task

pytestmark = pytest.mark.django_db


def spy_delay(monkeypatch: pytest.MonkeyPatch, task: Any) -> list[dict]:
    """
    Підмінити `task.delay`, записуючи виклики — і ЗВІРЯЮЧИ їх із сигнатурою самої задачі.

    Саме звірка ловить баг класу «price_source_id vs source_id»: звичайний Mock прийняв би
    будь-що і тест лишився б зеленим, поки прод падає. `bind()` — не проковтне.

    `self` з сигнатури прибираємо: у задач із `bind=True` (translate_pending_task) його
    підставляє сам Celery, і адмінка його, звісно, не передає.
    """
    params = [p for name, p in inspect.signature(task.run).parameters.items() if name != "self"]
    signature = inspect.Signature(params)
    calls: list[dict] = []

    def _delay(*args: Any, **kwargs: Any) -> None:
        try:
            bound = signature.bind(*args, **kwargs)
        except TypeError as exc:
            pytest.fail(f"{task.name}: адмінка викликає задачу неправильно — {exc}")
        calls.append(dict(bound.arguments))

    monkeypatch.setattr(task, "delay", _delay)
    return calls


@pytest.fixture
def request_with_messages(admin_user):
    """Реквест із користувачем і сховищем повідомлень — message_user без нього падає."""

    def _make():
        request = RequestFactory().post("/")
        request.user = admin_user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    return _make


@pytest.fixture
def price_source() -> PriceSource:
    return PriceSource.objects.create(
        code="complex-test",
        name="Complex (тест)",
        is_primary=True,
        is_active=True,
        priority=100,
        spreadsheet_id="test-price",
        default_availability=Availability.IN_STOCK,
    )


@pytest.fixture
def product() -> Product:
    category = Category.objects.create(name="Посудомийні машини", slug="dishwashers")
    brand = Brand.objects.create(name="Bosch", slug="bosch")
    return Product.objects.create(
        sku="TEST-1",
        name="Посудомийна машина Bosch",
        slug="test-1",
        category=category,
        brand=brand,
        base_price=Decimal("1000"),
        price=Decimal("1000"),
    )


# --- Синхронізація ----------------------------------------------------------


def test_sync_now_dispatches_sync_prices(monkeypatch, request_with_messages, price_source):
    calls = spy_delay(monkeypatch, sync_prices)
    request = request_with_messages()

    SyncRunAdmin(SyncRun, AdminSite()).sync_now(request)

    assert len(calls) == 1
    assert calls[0]["source_id"] == price_source.pk
    assert calls[0]["trigger"] == SyncRun.Trigger.MANUAL
    assert calls[0]["started_by"] == request.user.pk


def test_sync_now_does_not_create_phantom_run(monkeypatch, request_with_messages, price_source):
    """
    Прогін заводить `run_price_sync()` усередині advisory-локу — адмінка не має права
    створювати свій. Інакше в журналі назавжди лишається RUNNING, якого ніхто не закриє.
    """
    spy_delay(monkeypatch, sync_prices)

    SyncRunAdmin(SyncRun, AdminSite()).sync_now(request_with_messages())

    assert SyncRun.objects.count() == 0


def test_rollback_dispatches_rollback_sync_run(
    monkeypatch, request_with_messages, price_source, product
):
    calls = spy_delay(monkeypatch, rollback_sync_run)
    run = SyncRun.objects.create(
        kind=SyncRun.Kind.SHEETS_PRICES,
        price_source=price_source,
        trigger=SyncRun.Trigger.MANUAL,
        status=SyncRun.Status.SUCCESS,
    )
    ProductPriceSnapshot.objects.create(run=run, product=product, old_price=Decimal("900"))
    request = request_with_messages()

    SyncRunAdmin(SyncRun, AdminSite()).rollback_run(request, SyncRun.objects.filter(pk=run.pk))

    assert len(calls) == 1
    assert calls[0]["run_id"] == str(run.pk)
    assert calls[0]["user_id"] == request.user.pk


def test_rollback_without_snapshot_dispatches_nothing(
    monkeypatch, request_with_messages, price_source
):
    """Немає знімка — немає чого відкочувати. Задачу ставити не можна."""
    calls = spy_delay(monkeypatch, rollback_sync_run)
    run = SyncRun.objects.create(
        kind=SyncRun.Kind.SHEETS_PRICES,
        price_source=price_source,
        trigger=SyncRun.Trigger.MANUAL,
        status=SyncRun.Status.SUCCESS,
    )

    SyncRunAdmin(SyncRun, AdminSite()).rollback_run(
        request_with_messages(), SyncRun.objects.filter(pk=run.pk)
    )

    assert calls == []


# --- Переклад ---------------------------------------------------------------


def test_retranslate_dispatches_after_commit(
    monkeypatch, request_with_messages, django_capture_on_commit_callbacks
):
    """
    Задача вибирає з БД усе, що чекає, — тому статуси мусять бути ЗАКОМІЧЕНІ до її запуску.
    Звідси on_commit: інакше воркер прочитає рядки до коміту й не побачить нічого.
    """
    calls = spy_delay(monkeypatch, translate_pending_task)
    category = Category.objects.create(name="Пральні машини", slug="washers")
    entry = TranslationEntry.objects.create(
        content_type=ContentType.objects.get_for_model(Category),
        object_id=category.pk,
        field="name",
        kind=TranslationKind.CATEGORY_NAME,
        source_text="Пральні машини",
        source_hash="hash-1",
        status=TranslationStatus.FAILED,
        validation_errors=["щось пішло не так"],
    )

    with django_capture_on_commit_callbacks(execute=True):
        TranslationEntryAdmin(TranslationEntry, AdminSite()).retranslate_selected(
            request_with_messages(), TranslationEntry.objects.filter(pk=entry.pk)
        )

    entry.refresh_from_db()
    assert entry.status == TranslationStatus.PENDING
    assert entry.validation_errors == []
    assert len(calls) == 1


# --- Каталог ----------------------------------------------------------------


def test_rebuild_denorm_dispatches_with_ids(
    monkeypatch, request_with_messages, product, django_capture_on_commit_callbacks
):
    calls = spy_delay(monkeypatch, rebuild_product_denorm)

    with django_capture_on_commit_callbacks(execute=True):
        ProductAdmin(Product, AdminSite()).act_rebuild_denorm(
            request_with_messages(), Product.objects.filter(pk=product.pk)
        )

    product.refresh_from_db()
    assert product.denorm_dirty is True
    assert calls == [{"product_ids": [product.pk]}]


# --- Фіди -------------------------------------------------------------------


def test_generate_feed_dispatches_task(monkeypatch, request_with_messages):
    calls = spy_delay(monkeypatch, generate_hotline_feed)
    request = request_with_messages()

    FeedArtifactAdmin(FeedArtifact, AdminSite()).generate_now(request)

    assert len(calls) == 1
    assert calls[0]["trigger"] == SyncRun.Trigger.MANUAL
    assert calls[0]["user_id"] == request.user.pk
