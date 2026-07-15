"""
Тести двигуна синхронізації (обов'язковий список — SYNC.md §11).

Жодного мережевого виклику: сітки або в пам'яті (`MemorySheetsClient`), або з реальних
CSV-фікстур `sync/fixtures/` — тих самих, на яких працює `manage.py sync_prices`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from catalog.enums import Availability, ProductSource
from catalog.models import Brand, Category, Product, ProductImage
from core.models import SiteSettings
from sync.models import (
    DataIssue,
    PriceSheet,
    PriceSource,
    SupplierOffer,
    SyncLogEntry,
    SyncRun,
)
from sync.services import (
    RunLog,
    norm_category_id,
    norm_sku,
    recalc_prices,
    rollback_run,
    run_price_sync,
)
from sync.tests.conftest import (
    SPEC_SPREADSHEET,
    SPREADSHEET,
    WORK_HEADERS,
    MemorySheetsClient,
    price_grid,
)

pytestmark = pytest.mark.django_db

#: РЕАЛЬНІ заголовки «Основної»: колонка H (8-ма) — БРЕНД під заголовком-сміттям («2401579»).
SPEC_HEADERS = [
    "Найменування",
    "Країна виробництва",
    "Розміри в упакуванні (см)",
    "Зображення",
    "Тест",
    "Характеристики",
    "Артикул",
    "2401579",
    "Категорія",
] + ["Назва_Характеристики", "Одиниця_виміру_Характеристики", "Значення_Характеристики"] * 5


def row(name="", price="", qty="", currency="", cat="", brand="", country="", photo="", sku=""):
    """⚠️ `photo` за замовчуванням ПОРОЖНЄ → товар вважається НЕПОВНИМ і ховається з сайту
    (SYNC.md §4.3). Тестам, яким потрібен ВИДИМИЙ товар, фото треба передати явно —
    саме тому нижче є `full_row()`.
    """
    return [name, price, qty, currency, cat, brand, country, photo, sku]


def full_row(name="", price="", qty="", currency="", cat="", brand="", country="", sku=""):
    """Повний товар: є і артикул, і фото → потрапляє на сайт (is_active=True)."""
    photo = f"https://cdn.example.com/img/{(sku or name).strip().lower()}.jpg"
    return [name, price, qty, currency, cat, brand, country, photo, sku]


def spec_row(name="", sku="", dims_col="", triples=(), brand=""):
    base = [name, "", dims_col, "", "", "", sku, brand, ""]
    for t in triples:
        base.extend(t)
    return base


def make_client(uah=(), usd=(), specs=(), drop_rows=0, headers=None):
    headers = headers or WORK_HEADERS
    return MemorySheetsClient(
        {
            (SPREADSHEET, "UAH"): price_grid(list(uah), headers=headers),
            (SPREADSHEET, "USD"): price_grid(list(usd), headers=headers),
            (SPEC_SPREADSHEET, "Основна"): [SPEC_HEADERS, *specs],
        },
        drop_rows=drop_rows,
    )


def sync(source, client, **kw):
    return run_price_sync(source, client=client, **kw)


# ===========================================================================
# Нормалізатори
# ===========================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2400042", "2400042"),
        ("2400042.0", "2400042"),  # Sheets віддав ЧИСЛОМ при UNFORMATTED_VALUE
        (2400042.0, "2400042"),
        (" cmpx-1 ", "CMPX-1"),
        ("24 000 42", "2400042"),
    ],
)
def test_norm_sku(raw: Any, expected: str) -> None:
    assert norm_sku(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("c50549829", "50549829"),
        ("с50549829", "50549829"),  # КИРИЛИЧНА «с» — візуальний двійник
        ("50549829.0", "50549829"),
        ("Уточнюється", ""),
    ],
)
def test_norm_category_id(raw: str, expected: str) -> None:
    assert norm_category_id(raw) == expected


# ===========================================================================
# Пастки парсера (INPUTS §3.2)
# ===========================================================================


def test_section_row_is_skipped_silently(source, site_settings, catalog_refs):
    """Рядок-секція «Аксесуари до техніки» — НЕ товар, НЕ помилка і НЕ WARN."""
    client = make_client(
        uah=[
            row(name="Аксесуари до техніки"),
            row(name="Холодильник Gorenje X", price="10 000,00", cat="c5609731", sku="A1"),
        ]
    )
    run = sync(source, client)

    assert run.status == SyncRun.Status.SUCCESS
    assert run.stats["rows_section"] == 1
    assert Product.objects.count() == 1
    assert not run.entries.filter(level=SyncLogEntry.Level.ERROR).exists()
    # Секція не має лишити ЖОДНОГО сліду в журналі — інакше WARN стануть шумом.
    assert not run.entries.filter(message__icontains="Аксесуари").exists()


def test_row_without_sku_warns_but_run_is_not_failed(source, site_settings, catalog_refs):
    """Рядок без артикула — це ЧЕРНЕТКА замовника, а не помилка (SYNC.md §4.3).

    Товар СТВОРЮЄТЬСЯ (з сурогатним ключем), але СХОВАНИЙ з сайту. Раніше він просто
    зникав — на реальному прайсі це означало тихо втратити 237 рядків з 836.
    """
    client = make_client(
        uah=[
            row(name="Запчастина для духовки TEKA 83340602", price="1 250,00", qty="2"),
            full_row(name="Холодильник Gorenje X", price="10 000,00", cat="c5609731", sku="A1"),
        ]
    )
    run = sync(source, client)

    assert run.status == SyncRun.Status.SUCCESS  # НЕ PARTIAL: це не битий рядок
    assert run.stats["rows_no_sku"] == 1
    assert run.stats["rows_surrogate_sku"] == 1
    warn = run.entries.get(payload__code="ROW_NO_SKU")
    assert warn.level == SyncLogEntry.Level.WARN
    assert "TEKA 83340602" in warn.message

    # Товар СТВОРЕНО — але схований, з сурогатним ключем.
    assert Product.objects.count() == 2
    draft = Product.objects.get(name__startswith="Запчастина")
    assert draft.sku.startswith("AUTO-")
    assert draft.sku_is_surrogate is True
    assert draft.is_incomplete is True
    assert draft.is_active is False  # ← на сайті його НЕМАЄ
    assert draft.base_price == Decimal("1250.00")  # ← але дані збережені

    # Повний товар видно.
    assert Product.objects.get(sku="A1").is_active is True

    # Замовник побачить це у звіті data_issues, а не в логах.
    issue = DataIssue.objects.get(run=run, kind=DataIssue.Kind.MISSING_SKU)
    assert issue.sheet == "UAH"
    assert issue.row_number == 4  # ← номер рядка В ЙОГО таблиці
    assert "TEKA 83340602" in issue.name


def test_placeholder_is_empty_not_a_value(source, site_settings, catalog_refs):
    """«Уточнюється» — це «ще не знаю», а не значення.

    Без цього синк створює бренд «Уточнюється» на 200 товарів і 200 разів довбиться в DNS
    з «URL» `Уточнюється` — тихо і з зеленим SUCCESS.
    """
    client = make_client(
        uah=[
            row(
                name="Холодильник Gorenje X",
                price="10 000,00",
                cat="Уточнюється",
                brand="Уточнюється",
                country="Уточнюється",
                photo="Уточнюється",
                sku="A1",
            )
        ]
    )
    run = sync(source, client)
    product = Product.objects.get(sku="A1")

    assert run.status == SyncRun.Status.SUCCESS
    assert not Brand.objects.filter(name__icontains="Уточнюється").exists()
    assert product.brand_id is None
    assert product.country_id is None
    assert product.source_category_ext_id == ""
    assert ProductImage.objects.count() == 0
    assert product.category.external_id == Category.UNMAPPED_EXTERNAL_ID


def test_bad_rows_are_rejected_before_db(source, site_settings, catalog_refs):
    """Від'ємна ціна, з'їхала кома, артикул без назви — усе відсіюється В ПАРСЕРІ.

    CheckConstraint — ДРУГА лінія оборони: один рядок з ціною -1 у bulk_create(500)
    поклав би весь батч IntegrityError-ом, а з ним і всю транзакцію прогону.
    """
    client = make_client(
        uah=[
            row(name="Мінус", price="-1", cat="c5609731", sku="BAD1"),
            row(name="Кома з'їхала", price="2744500000", cat="c5609731", sku="BAD2"),
            row(name="", price="5000", cat="c5609731", sku="BAD3"),
            row(name="Норм", price="10 000,00", cat="c5609731", sku="OK1"),
        ]
    )
    run = sync(source, client)

    assert run.status == SyncRun.Status.PARTIAL
    assert run.rows_failed == 3
    assert list(Product.objects.values_list("sku", flat=True)) == ["OK1"]


# ===========================================================================
# Дедуплікація (крок 5)
# ===========================================================================


def test_duplicate_sku_in_one_sheet(source, site_settings, catalog_refs):
    """Два рядки з тим самим sku → 1 товар, WARN, прогін НЕ FAILED.

    Без дедупу PostgreSQL кидає «ON CONFLICT DO UPDATE cannot affect row a second time»
    і валить увесь батч.
    """
    client = make_client(
        uah=[
            row(name="Перший", price="100,00", cat="c5609731", sku="DUP"),
            row(name="Другий", price="200,00", cat="c5609731", sku="DUP"),
        ]
    )
    run = sync(source, client)

    assert run.status == SyncRun.Status.SUCCESS
    assert run.rows_duplicated == 1
    assert Product.objects.filter(sku="DUP").count() == 1
    assert Product.objects.get(sku="DUP").base_price == Decimal("100.00")  # виграв ПЕРШИЙ
    assert run.entries.filter(action=SyncLogEntry.Action.DUPLICATE_SKU).count() == 1


def test_duplicate_sku_between_sheets_lower_sort_order_wins(source, site_settings, catalog_refs):
    """Дубль МІЖ листами: виграє лист з меншим sort_order (UAH=0)."""
    client = make_client(
        uah=[row(name="З UAH", price="100,00", cat="c5609731", sku="DUP")],
        usd=[row(name="З USD", price="500,00", cat="c5609731", sku="DUP")],
    )
    run = sync(source, client)
    offer = SupplierOffer.objects.get(sku="DUP")

    assert run.rows_duplicated == 1
    assert offer.sheet == "UAH"
    assert offer.source_currency == "UAH"
    assert offer.base_price == Decimal("100.00")


# ===========================================================================
# Наявність (крок 9)
# ===========================================================================


def test_qty_empty_uses_source_default_not_out_of_stock(source, site_settings, catalog_refs):
    """Порожня «К-сть» → дефолт джерела. `None` і `0` — ПРОТИЛЕЖНІ речі."""
    client = make_client(uah=[row(name="Товар", price="100,00", qty="", cat="c5609731", sku="A1")])
    sync(source, client)

    product = Product.objects.get(sku="A1")
    assert product.availability == Availability.IN_STOCK
    assert product.stock_qty is None


def test_qty_empty_with_on_order_source(source, site_settings, catalog_refs):
    """Постачальник «під замовлення» без колонки «К-сть» → ON_ORDER, а не OUT_OF_STOCK."""
    PriceSource.objects.filter(pk=source.pk).update(
        default_availability=Availability.ON_ORDER, default_lead_days=14
    )
    source.refresh_from_db()

    client = make_client(uah=[row(name="Товар", price="100,00", cat="c5609731", sku="A1")])
    sync(source, client)

    product = Product.objects.get(sku="A1")
    assert product.availability == Availability.ON_ORDER
    assert product.order_lead_days == 14


def test_on_order_source_without_lead_days_fails_fast(source, site_settings, catalog_refs):
    """«Під замовлення» без `default_lead_days` не має дожити навіть до читання Sheets.

    ⚠️ Оборона тут ДВОШАРОВА, і обидва шари реальні:
      1. `CheckConstraint src_on_order_needs_lead_days` — така конфігурація фізично не
         зберігається в БД (навіть через `.update()`, повз `clean()`);
      2. перевірка в `run_price_sync` (крок 3.5) — на випадок, якщо constraint колись знімуть;
         без неї КОЖЕН INSERT падав би на `prod_on_order_needs_lead_days` уже посеред прогону,
         а в журналі було б «порушено constraint» замість людського тексту.
    """
    from django.core.exceptions import ValidationError
    from django.db import IntegrityError, transaction

    from sync.services import IssueLog, SyncError, _run_price_sync_inner

    source.default_availability = Availability.ON_ORDER
    source.default_lead_days = None
    with pytest.raises(ValidationError, match="default_lead_days"):
        source.clean()

    with pytest.raises(IntegrityError), transaction.atomic():
        PriceSource.objects.filter(pk=source.pk).update(
            default_availability=Availability.ON_ORDER, default_lead_days=None
        )

    # Шар 2: сам двигун відмовляється працювати з такою конфігурацією (об'єкт у пам'яті).
    run = SyncRun(kind=SyncRun.Kind.SHEETS_PRICES, price_source=source)
    with pytest.raises(SyncError, match="default_lead_days"):
        _run_price_sync_inner(source, run, RunLog(run), IssueLog(run), False, False, None, False)


def test_qty_zero_is_out_of_stock(source, site_settings, catalog_refs):
    client = make_client(uah=[row(name="Товар", price="100,00", qty="0", cat="c5609731", sku="A1")])
    sync(source, client)
    assert Product.objects.get(sku="A1").availability == Availability.OUT_OF_STOCK


def test_disappeared_sku_goes_out_of_stock_but_product_survives(
    source, site_settings, catalog_refs
):
    """Зник з прайсу → OUT_OF_STOCK. Товар НЕ видаляється: на нього ведуть замовлення й посилання."""
    full = [
        row(name="A", price="100,00", cat="c5609731", sku="A1"),
        row(name="B", price="200,00", cat="c5609731", sku="A2"),
        row(name="C", price="300,00", cat="c5609731", sku="A3"),
        row(name="D", price="400,00", cat="c5609731", sku="A4"),
        row(name="E", price="500,00", cat="c5609731", sku="A5"),
    ]
    sync(source, make_client(uah=full))
    run2 = sync(source, make_client(uah=full[:4]))  # A5 зник → 20% (не більше порога)

    assert run2.status == SyncRun.Status.SUCCESS
    gone = Product.objects.get(sku="A5")
    assert gone.availability == Availability.OUT_OF_STOCK
    assert gone.stock_qty == 0
    assert Product.objects.count() == 5  # НЕ видалений
    assert SupplierOffer.objects.get(sku="A5").is_active is False


# ===========================================================================
# Замки (SYNC.md §11 «Замки») — головна регресія v2
# ===========================================================================


def test_price_locked_product_stays_in_stock(source, site_settings, catalog_refs):
    """ГОЛОВНИЙ РЕГРЕСІЙНИЙ ТЕСТ.

    Товар з `price_locked=True`, який Є в прайсі, після синку ЛИШАЄТЬСЯ в наявності.
    У v1 він ставав OUT_OF_STOCK на ПЕРШОМУ ж прогоні (не отримував last_seen_run
    → деактивувався), тобто «зафіксував ціну» = «товар зник з продажу».
    """
    rows = [full_row(name="Товар", price="100,00", qty="5", cat="c5609731", sku="LOCK")]
    sync(source, make_client(uah=rows))

    Product.objects.filter(sku="LOCK").update(
        price_locked=True, price=Decimal("999.00"), base_price=Decimal("999.00")
    )
    run2 = sync(source, make_client(uah=rows))

    product = Product.objects.get(sku="LOCK")
    assert product.availability == Availability.IN_STOCK  # ← регресія
    assert product.is_active is True
    assert product.last_seen_run == run2.id  # службові поля оновились ЗАВЖДИ
    assert product.price == Decimal("999.00")  # ← а ціну не чіпали
    assert product.base_price == Decimal("999.00")
    assert product.stock_qty == 5  # наявність — оновилась


def test_price_locked_availability_is_still_updated(source, site_settings, catalog_refs):
    """price_locked блокує ЛИШЕ ціну: зник з прайсу → все одно OUT_OF_STOCK."""
    sync(source, make_client(uah=[row(name="X", price="100,00", cat="c5609731", sku="LOCK")]))
    Product.objects.filter(sku="LOCK").update(price_locked=True)

    sync(
        source,
        make_client(uah=[row(name="Y", price="100,00", cat="c5609731", sku="OTHER")]),
        force=True,
    )
    assert Product.objects.get(sku="LOCK").availability == Availability.OUT_OF_STOCK


def test_availability_locked_keeps_manual_on_order(source, site_settings, catalog_refs):
    """Менеджер поставив «Під замовлення» руками → синк не перезаписує."""
    rows = [row(name="X", price="100,00", qty="0", cat="c5609731", sku="AV")]
    sync(
        source, make_client(uah=[row(name="X", price="100,00", qty="5", cat="c5609731", sku="AV")])
    )

    Product.objects.filter(sku="AV").update(
        availability_locked=True, availability=Availability.ON_ORDER, order_lead_days=7
    )
    # force=True — бо qty 5→0 на ЄДИНОМУ товарі це 100% «обнулених», і guard #2c (правильно)
    # зупинив би прогін. Тут перевіряємо не guard, а замок наявності.
    run2 = sync(source, make_client(uah=rows), force=True)

    product = Product.objects.get(sku="AV")
    assert product.availability == Availability.ON_ORDER  # qty=0 НЕ перезаписав
    assert product.order_lead_days == 7
    assert product.last_seen_run == run2.id  # але службові поля — оновились


# ===========================================================================
# Ціна: інваріант «синк == recalc»
# ===========================================================================


def test_sync_and_recalc_give_identical_price(source, site_settings, catalog_refs):
    """ІНВАРІАНТ: recalc при незмінному курсі — повний no-op по ЦІНІ.

    У v1 recalc рахував `base * rate` БЕЗ націнки → перша ж зміна курсу стирала маржу.
    Обидва шляхи зобов'язані звати ОДНУ функцію complex_price_uah() з markup_percent.
    """
    PriceSource.objects.filter(pk=source.pk).update(price_markup_percent=Decimal("15.00"))
    source.refresh_from_db()

    sync(source, make_client(usd=[row(name="USD-товар", price="100,00", cat="c5609731", sku="U1")]))

    product = Product.objects.get(sku="U1")
    price_after_sync = product.price
    assert product.source_currency == "USD"
    assert product.markup_percent == Decimal("15.00")
    assert product.usd_rate_used == Decimal("41.5000")
    # 100 × 1.15 × 41.5 = 4772.5 → округлення «до гривні»
    assert price_after_sync == Decimal("4773.00")

    recalc_prices(scope="usd")
    product.refresh_from_db()
    assert product.price == price_after_sync  # ← жодного дрейфу
    assert product.markup_percent == Decimal("15.00")  # ← націнка НЕ зникла


def test_recalc_preserves_markup_after_rate_change(source, site_settings, catalog_refs):
    PriceSource.objects.filter(pk=source.pk).update(price_markup_percent=Decimal("15.00"))
    source.refresh_from_db()
    sync(source, make_client(usd=[row(name="USD", price="100,00", cat="c5609731", sku="U1")]))

    SiteSettings.objects.filter(pk=1).update(usd_rate=Decimal("45.0000"))
    SiteSettings.invalidate()
    recalc_prices(scope="usd")

    product = Product.objects.get(sku="U1")
    assert product.price == Decimal("5175.00")  # 100 × 1.15 × 45
    assert product.usd_rate_used == Decimal("45.0000")


def test_updated_at_changes_on_price_update(source, site_settings, catalog_refs):
    """Без `updated_at` у `update_fields` sitemap.lastmod і ISR-ревалідація мовчки брешуть."""
    sync(source, make_client(uah=[row(name="X", price="100,00", cat="c5609731", sku="A1")]))
    before = Product.objects.get(sku="A1").updated_at

    sync(source, make_client(uah=[row(name="X", price="110,00", cat="c5609731", sku="A1")]))
    after = Product.objects.get(sku="A1")

    assert after.updated_at > before
    assert after.price == Decimal("110.00")
    assert SupplierOffer.objects.get(sku="A1").updated_at > before


# ===========================================================================
# Категорії (крок 10 / 10b)
# ===========================================================================


def test_unknown_category_goes_to_unmapped_inactive(source, site_settings, catalog_refs):
    client = make_client(uah=[row(name="Дивний товар", price="100,00", cat="c50549829", sku="A1")])
    run = sync(source, client)
    product = Product.objects.get(sku="A1")

    assert product.category.external_id == Category.UNMAPPED_EXTERNAL_ID
    assert product.category.is_service is True
    assert product.is_active is False  # у v1 він просочувався у FTS, sitemap і на /p/{id}
    assert product.source_category_ext_id == "50549829"  # ← ключ до автопідбору
    assert run.categories_missing == ["50549829"]
    assert run.entries.filter(action=SyncLogEntry.Action.CATEGORY_MISSING).exists()


def test_products_remapped_after_category_created(source, site_settings, catalog_refs):
    """Контент-менеджер створив категорію → товари САМІ переїжджають з «__unmapped__»."""
    client = make_client(uah=[full_row(name="Товар", price="100,00", cat="c50549829", sku="A1")])
    sync(source, client)
    assert Product.objects.get(sku="A1").is_active is False

    new_cat = Category(external_id="50549829", name="Аксесуари", slug="aksesuary")
    if hasattr(new_cat, "name_uk"):
        new_cat.name_uk, new_cat.slug_uk = "Аксесуари", "aksesuary"
    new_cat.save()

    run2 = sync(source, client)
    product = Product.objects.get(sku="A1")
    assert product.category_id == new_cat.pk
    assert product.is_active is True
    assert run2.entries.filter(action=SyncLogEntry.Action.CATEGORY_REMAPPED).exists()


def test_cyrillic_category_prefix_is_stripped(source, site_settings, catalog_refs):
    """`с5609731` з КИРИЛИЧНОЮ «с» має знайти категорію 5609731."""
    client = make_client(uah=[row(name="Товар", price="100,00", cat="с5609731", sku="A1")])
    sync(source, client)
    assert Product.objects.get(sku="A1").category.external_id == "5609731"


def test_new_brand_created_with_needs_review(source, site_settings, catalog_refs):
    client = make_client(
        uah=[row(name="Товар", price="100,00", cat="c5609731", brand="Hisense", sku="A1")]
    )
    run = sync(source, client)

    brand = Brand.objects.get(name="Hisense")
    assert brand.needs_review is True
    assert Product.objects.get(sku="A1").brand_id == brand.pk
    assert run.entries.filter(action=SyncLogEntry.Action.BRAND_CREATED).exists()


# ===========================================================================
# Запобіжники (крок 6)
# ===========================================================================


def _five_rows():
    return [
        row(name=f"Товар {i}", price="100,00", qty="3", cat="c5609731", sku=f"G{i}")
        for i in range(5)
    ]


def test_guard_mass_deactivation_aborts(source, site_settings, catalog_refs):
    """60% товарів зникло → ABORTED. Каталог НЕ змінено."""
    sync(source, make_client(uah=_five_rows()))
    run2 = sync(source, make_client(uah=_five_rows()[:2]))  # 3 з 5 зникли = 60%

    assert run2.status == SyncRun.Status.ABORTED
    assert "guard_max_deactivate" in run2.guard_report["triggered"]
    assert run2.guard_report["will_deactivate_pct"] == 60.0
    # КАТАЛОГ НЕДОТОРКАНИЙ:
    assert Product.objects.filter(availability=Availability.IN_STOCK).count() == 5
    assert SupplierOffer.objects.filter(is_active=True).count() == 5


def test_guard_median_price_jump_aborts(source, site_settings, catalog_refs):
    """Усі ціни ×2 → медіанна зміна 100% > 30% → ABORTED (десяткова кома з'їхала)."""
    sync(source, make_client(uah=_five_rows()))
    doubled = [
        row(name=f"Товар {i}", price="200,00", qty="3", cat="c5609731", sku=f"G{i}")
        for i in range(5)
    ]
    run2 = sync(source, make_client(uah=doubled))

    assert run2.status == SyncRun.Status.ABORTED
    assert "guard_median_price" in run2.guard_report["triggered"]
    assert Product.objects.first().price == Decimal("100.00")  # ціни не змінились


def test_guard_qty_column_zeroed_aborts(source, site_settings, catalog_refs):
    """«Артур зсунув колонку К-сть»: рядків стільки ж, ціни ті самі, а qty скрізь 0."""
    sync(source, make_client(uah=_five_rows()))
    zeroed = [
        row(name=f"Товар {i}", price="100,00", qty="0", cat="c5609731", sku=f"G{i}")
        for i in range(5)
    ]
    run2 = sync(source, make_client(uah=zeroed))

    assert run2.status == SyncRun.Status.ABORTED
    assert "guard_qty_zeroed" in run2.guard_report["triggered"]
    assert run2.guard_report["qty_zeroed_pct"] == 100.0
    assert Product.objects.filter(availability=Availability.IN_STOCK).count() == 5


def test_guard_min_rows_aborts(source, site_settings, catalog_refs):
    """Прайс раптом схуд удвічі — читаємо не те."""
    sync(source, make_client(uah=_five_rows()))
    run2 = sync(source, make_client(uah=_five_rows()[:1]))

    assert run2.status == SyncRun.Status.ABORTED
    assert "guard_min_rows" in run2.guard_report["triggered"]


def test_force_true_bypasses_guard(source, site_settings, catalog_refs, django_user_model):
    """«Застосувати попри попередження» — тільки з адмінки і тільки з started_by."""
    user = django_user_model.objects.create_user(username="admin", password="x")  # noqa: S106
    sync(source, make_client(uah=_five_rows()))

    run2 = sync(
        source,
        make_client(uah=_five_rows()[:2]),
        force=True,
        user_id=user.pk,
        trigger=SyncRun.Trigger.MANUAL,
    )

    assert run2.status == SyncRun.Status.SUCCESS
    assert run2.forced is True
    assert run2.started_by_id == user.pk
    assert Product.objects.filter(availability=Availability.OUT_OF_STOCK).count() == 3


def test_first_run_has_no_guard(source, site_settings, catalog_refs):
    """Порівнювати нема з чим — саме тому guard мовчить, а не блокує старт проєкту."""
    run = sync(source, make_client(uah=_five_rows()))
    assert run.status == SyncRun.Status.SUCCESS
    assert run.guard_report["triggered"] == []


# ===========================================================================
# Відкат (SYNC.md §5)
# ===========================================================================


def test_rollback_restores_prices_and_availability(source, site_settings, catalog_refs):
    sync(source, make_client(uah=_five_rows()))
    before = {p.sku: (p.price, p.availability) for p in Product.objects.all()}

    changed = [
        row(name=f"Товар {i}", price="110,00", qty="0", cat="c5609731", sku=f"G{i}")
        for i in range(5)
    ]
    run2 = sync(source, make_client(uah=changed), force=True)
    assert run2.status == SyncRun.Status.SUCCESS
    assert Product.objects.get(sku="G0").price == Decimal("110.00")
    assert Product.objects.get(sku="G0").availability == Availability.OUT_OF_STOCK

    rollback = rollback_run(run2.id)

    assert rollback.status == SyncRun.Status.SUCCESS
    assert rollback.kind == SyncRun.Kind.ROLLBACK
    assert rollback.rolled_back_run_id == run2.id
    assert rollback.products_updated == 5
    for product in Product.objects.all():
        assert (product.price, product.availability) == before[product.sku]


def test_rollback_skips_products_touched_after_run(source, site_settings, catalog_refs):
    """Товари, змінені ПІСЛЯ прогону (наступним синком), відкат не чіпає."""
    sync(source, make_client(uah=_five_rows()))
    changed = [
        row(name=f"Товар {i}", price="110,00", qty="3", cat="c5609731", sku=f"G{i}")
        for i in range(5)
    ]
    run2 = sync(source, make_client(uah=changed))
    sync(source, make_client(uah=changed))  # run3 — «хтось уже поверх»

    rollback = rollback_run(run2.id)
    assert rollback.products_updated == 0
    assert rollback.stats["skipped_touched_later"] >= 5
    assert Product.objects.get(sku="G0").price == Decimal("110.00")  # не зіпсовано


# ===========================================================================
# Читання (SYNC.md §11 «Читання»)
# ===========================================================================


def test_incomplete_read_fails_hard(source, site_settings, catalog_refs):
    """Прочитано менше, ніж у сітці → FAILED, а НЕ PARTIAL. Каталог недоторканий.

    Це той самий баг, через який 5000 товарів мовчки ставали «Немає в наявності»:
    чанк упав після ретраїв, а код вважав прочитане «повним прайсом».
    """
    sync(source, make_client(uah=_five_rows()))

    run2 = sync(source, make_client(uah=_five_rows(), drop_rows=2))

    assert run2.status == SyncRun.Status.FAILED
    assert "неповне читання" in run2.traceback.lower()
    assert Product.objects.filter(availability=Availability.IN_STOCK).count() == 5
    assert run2.products_deactivated == 0


def test_missing_required_column_fails_loudly(source, site_settings, catalog_refs):
    """Колонку перейменували → FAILED. Ніколи не «тихо занулити ціни»."""
    headers = list(WORK_HEADERS)
    headers[8] = "Код товару"  # був «Артикул»
    run = sync(source, make_client(uah=_five_rows(), headers=headers))

    assert run.status == SyncRun.Status.FAILED
    assert "sku" in run.traceback


def test_header_underscore_vs_dash_is_not_an_error(source, site_settings, catalog_refs):
    """`Країна_виробник` vs `Країна-виробник` — один і той самий заголовок."""
    headers = list(WORK_HEADERS)
    headers[6] = "Країна-виробник"
    run = sync(
        source,
        make_client(
            uah=[row(name="X", price="100,00", cat="c5609731", country="Словенія", sku="A1")],
            headers=headers,
        ),
    )
    assert run.status == SyncRun.Status.SUCCESS
    assert Product.objects.get(sku="A1").country.code == "SI"


# ===========================================================================
# Характеристики і габарити (SYNC.md §6)
# ===========================================================================


def test_package_dims_from_specsheet(source, site_settings, catalog_refs):
    """Габарити упаковки і ВАГА В УПАКОВЦІ (61.5), а не вага товару (58) — вхід калькулятора НП."""
    client = make_client(
        uah=[
            row(name="Холодильник Gorenje NRK6202AXL4", price="27 445,50", cat="c5609731", sku="S1")
        ],
        specs=[
            spec_row(
                name="Холодильник Gorenje NRK6202AXL4",
                sku="S1",
                triples=[
                    ("Габарити упаковки (ВхШхГ)", "см", "171,5 x 56 x 35"),
                    ("Вага в упаковці", "кг", "61.5"),
                    ("Вага", "кг", "58"),
                    ("Гарантійний термін", "міс", "12"),
                    ("Загальний об'єм", "л", "331"),
                ],
            )
        ],
    )
    run = sync(source, client)
    product = Product.objects.get(sku="S1")

    assert run.specs_filled == 1
    assert product.package_height_cm == Decimal("171.5")
    assert product.package_width_cm == Decimal("56.0")
    assert product.package_depth_cm == Decimal("35.0")
    assert product.package_weight_kg == Decimal("61.500")  # НЕ 58 — це вага ТОВАРУ
    assert product.package_dims_source == ProductSource.SHEET
    assert product.warranty_months == 12
    assert product.attr_values.count() == 5


def test_manual_package_dims_are_never_overwritten(source, site_settings, catalog_refs):
    """`package_dims_source == "manual"` — це і є замок. Третього прапорця не існує."""
    client = make_client(
        uah=[row(name="Товар", price="100,00", cat="c5609731", sku="S1")],
        specs=[
            spec_row(
                name="Товар",
                sku="S1",
                triples=[("Габарити упаковки (ВхШхГ)", "см", "171,5 x 56 x 35")],
            )
        ],
    )
    sync(source, client)
    Product.objects.filter(sku="S1").update(
        package_dims_source=ProductSource.MANUAL,
        package_height_cm=Decimal("10.0"),
        package_width_cm=Decimal("10.0"),
        package_depth_cm=Decimal("10.0"),
    )

    sync(source, client)
    product = Product.objects.get(sku="S1")
    assert product.package_height_cm == Decimal("10.0")  # синк НЕ чіпав


def test_specs_matched_by_normalized_name_when_sku_missing(source, site_settings, catalog_refs):
    """Артикула в таблиці характеристик немає → фолбек на нормалізовану назву."""
    client = make_client(
        uah=[row(name="Чайник Philips HD9350", price="1 899,00", cat="c5609731", sku="K1")],
        specs=[
            spec_row(
                name="Чайник Philips HD9350",
                sku="",  # ← артикула немає
                dims_col="24 x 22 x 18",
                triples=[("Вага в упаковці", "кг", "1.4")],
            )
        ],
    )
    sync(source, client)

    product = Product.objects.get(sku="K1")
    assert product.package_weight_kg == Decimal("1.400")
    assert product.package_height_cm == Decimal("24.0")  # габарити з КОЛОНКИ C


def test_warranty_in_years_becomes_months(source, site_settings, catalog_refs):
    client = make_client(
        uah=[row(name="Пральна машина Bosch W", price="100,00", cat="c5609731", sku="W1")],
        specs=[
            spec_row(
                name="Пральна машина Bosch W",
                sku="W1",
                triples=[("Гарантійний термін", "", "2 роки")],
            )
        ],
    )
    sync(source, client)
    assert Product.objects.get(sku="W1").warranty_months == 24


def test_product_without_specs_is_still_created(source, site_settings, catalog_refs):
    """Вимога замовника (Артур), пряма цитата:

    «У цій таблиці не буде характеристик... Але точно буде фото, назва, ціна.
    І навіть якщо пусті характеристики, хай карта товару буде пуста з ціною»

    Товар, для якого в таблиці характеристик НЕМАЄ жодного збігу (ні за артикулом,
    ні за нормалізованою назвою), МАЄ все одно потрапити на сайт — з назвою, ціною
    і фото в черзі на завантаження. Відсутність характеристик — це НЕ помилка рядка
    і НЕ причина пропустити товар: `attr_values` і `package_*` просто лишаються
    пустими, а калькулятор доставки фолбечиться на дефолти категорії.

    НЕ «фіксити» цей тест додаванням рядка в specs — сенс саме в тому, що збігу немає.
    """
    client = make_client(
        uah=[
            row(
                name="Мікрохвильовка Samsung MS23K",
                price="3 500,00",
                cat="c5609731",
                photo="https://example.com/ms23k.jpg",
                sku="NOSPEC1",
            )
        ],
        specs=[],  # таблиця характеристик ПУСТА — жодного збігу ні за sku, ні за назвою
    )
    run = sync(source, client)
    product = Product.objects.get(sku="NOSPEC1")

    # 1. Товар створився, з правильною назвою і ціною, і він активний (не «unmapped»).
    assert run.status == SyncRun.Status.SUCCESS
    assert product.is_active is True
    assert product.name == "Мікрохвильовка Samsung MS23K"
    assert product.base_price == Decimal("3500.00")
    assert product.price == Decimal("3500.00")

    # 2. Нуль характеристик — і це НЕ помилка: жодного ERROR-запису в журналі,
    #    run НЕ PARTIAL і НЕ FAILED.
    assert product.attr_values.count() == 0
    assert not run.entries.filter(level=SyncLogEntry.Level.ERROR).exists()

    # 3. Фото все одно поставлене в чергу на завантаження.
    assert run.images_queued == 1
    image = ProductImage.objects.get(product=product)
    assert image.downloaded_at is None  # ще не завантажене, але ЗАПИС є — це і є «в черзі»
    assert image.source_url == "https://example.com/ms23k.jpg"

    # 4. Габаритів немає (бо немає характеристик) — і калькулятор доставки має вміти
    #    з цим жити: package_* порожні, а не помилка/крах синку.
    assert product.package_height_cm is None
    assert product.package_width_cm is None
    assert product.package_depth_cm is None
    assert product.package_weight_kg is None


# ===========================================================================
# Мультипостачальники (SYNC.md §11)
# ===========================================================================


@pytest.fixture
def alpha(db, catalog_refs) -> PriceSource:
    src = PriceSource.objects.create(
        code="alpha",
        name="Alpha",
        priority=200,  # БІЛЬШИЙ priority = ПРОГРАЄ
        spreadsheet_id=SPREADSHEET,
        default_availability=Availability.ON_ORDER,
        default_lead_days=14,
        price_markup_percent=Decimal("15.00"),
    )
    PriceSheet.objects.create(
        price_source=src,
        tab_name="UAH",
        currency="UAH",
        column_map=PriceSheet.objects.first().column_map,
        sort_order=0,
    )
    return src


def test_low_priority_source_does_not_steal_sku(source, alpha, site_settings, catalog_refs):
    """Alpha(200) не перезаписує ціну й наявність Complex(100)."""
    complex_rows = [row(name="Спільний", price="100,00", qty="5", cat="c5609731", sku="SHARED")]
    sync(source, make_client(uah=complex_rows))

    alpha_rows = [row(name="Спільний", price="999,00", cat="c5609731", sku="SHARED")]
    run = sync(alpha, make_client(uah=alpha_rows), fill_specs=False)

    product = Product.objects.get(sku="SHARED")
    assert run.status == SyncRun.Status.SUCCESS
    assert product.price == Decimal("100.00")  # ціна Complex
    assert product.availability == Availability.IN_STOCK  # не «під замовлення» Alpha
    assert product.price_source_id == source.pk
    assert SupplierOffer.objects.filter(sku="SHARED").count() == 2  # обидва оффери живі


def test_alpha_sync_does_not_deactivate_complex_products(
    source, alpha, site_settings, catalog_refs
):
    """Прогін Alpha НЕ чіпає каталог Complex. Це був critical #1.

    У v1 `Product.price_source` — ОДИНИЧНА колонка, тому прогін Alpha не бачив товарів Complex
    у своєму `last_seen_run` і чесно їх ДЕАКТИВОВУВАВ. Дзеркально ламалось і назад.
    """
    sync(source, make_client(uah=_five_rows()))
    sync(
        alpha,
        make_client(uah=[row(name="Свій", price="10,00", cat="c5609731", sku="ALPHA1")]),
        fill_specs=False,
    )

    complex_products = Product.objects.filter(sku__startswith="G")
    assert complex_products.count() == 5
    assert all(p.availability == Availability.IN_STOCK for p in complex_products)
    assert all(p.price_source_id == source.pk for p in complex_products)
    assert SupplierOffer.objects.filter(price_source=source, is_active=True).count() == 5

    # А власний товар Alpha живе за правилами ALPHA: «під замовлення», 14 днів.
    alpha_product = Product.objects.get(sku="ALPHA1")
    assert alpha_product.availability == Availability.ON_ORDER
    assert alpha_product.order_lead_days == 14


def test_winner_switches_when_complex_offer_disappears(source, alpha, site_settings, catalog_refs):
    """SKU зник з прайсу Complex → переможцем стає Alpha, товар лишається в продажу."""
    shared = [row(name="Спільний", price="100,00", qty="5", cat="c5609731", sku="SHARED")]
    sync(source, make_client(uah=shared))
    sync(
        alpha,
        make_client(uah=[row(name="Спільний", price="120,00", cat="c5609731", sku="SHARED")]),
        fill_specs=False,
    )

    # Complex більше не має цього sku (але має інший — інакше «жодного рядка»)
    sync(
        source,
        make_client(uah=[row(name="Інший", price="50,00", qty="1", cat="c5609731", sku="OTHER")]),
        force=True,
    )

    product = Product.objects.get(sku="SHARED")
    assert product.price_source_id == alpha.pk
    assert product.availability == Availability.ON_ORDER  # дефолт Alpha
    assert product.order_lead_days == 14
    assert product.price == Decimal("138.00")  # 120 × 1.15 (націнка Alpha)


# ===========================================================================
# Dry-run і advisory-lock
# ===========================================================================


def test_dry_run_changes_nothing_but_records_the_diff(source, site_settings, catalog_refs):
    run = sync(source, make_client(uah=_five_rows()), dry_run=True)

    assert run.status == SyncRun.Status.SUCCESS
    assert run.dry_run is True
    assert run.products_created == 5  # скільки Б створилось
    assert Product.objects.count() == 0  # але не створилось
    assert SupplierOffer.objects.count() == 0
    assert run.entries.exists() or run.rows_read > 0  # журнал ЗБЕРІГСЯ (він поза транзакцією)


def test_second_run_is_skipped_while_lock_is_held(source, site_settings, catalog_refs):
    """Cron і кнопка в адмінці одночасно → другий прогін SKIPPED, каталог не чіпаємо.

    ⚠️ Лок тримає ОКРЕМЕ З'ЄДНАННЯ, і це не педантизм тесту: advisory-lock РЕЕНТРАНТНИЙ
    у межах однієї сесії — `pg_try_advisory_lock` з того самого з'єднання поверне `true`
    вдруге. Тобто «тест» з локом у тому ж з'єднанні перевіряв би рівно нічого.
    """
    import psycopg
    from django.db import connection

    params = connection.get_connection_params()
    params.pop("cursor_factory", None)
    params.pop("context", None)
    rival = psycopg.connect(**params)
    try:
        with rival.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", [f"sync:prices:{source.code}"])
            assert cur.fetchone()[0] is True

        run = sync(source, make_client(uah=_five_rows()))

        assert run.status == SyncRun.Status.SKIPPED
        assert "advisory-lock" in run.traceback
        assert Product.objects.count() == 0
    finally:
        rival.close()


def test_idempotent_rerun(source, site_settings, catalog_refs):
    """Повторний прогін тих самих даних → нічого не змінилось по суті."""
    client = make_client(uah=_five_rows())
    sync(source, client)
    run2 = sync(source, client)

    assert run2.status == SyncRun.Status.SUCCESS
    assert run2.products_created == 0
    assert run2.products_updated == 5
    assert run2.products_deactivated == 0
    assert Product.objects.count() == 5
