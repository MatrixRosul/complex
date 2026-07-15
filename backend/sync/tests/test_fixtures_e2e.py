"""
Прогін на РЕАЛЬНИХ CSV-фікстурах (`sync/fixtures/`) — тих самих, що читає
`manage.py sync_prices` без ключів Google.

Фікстури повторюють РЕАЛЬНУ структуру таблиць замовника (звірено 14.07.2026, OAuth):
заголовки в рядку 1, дата зрізу (Excel serial) у A2, дані з рядка 4, рядки-секції,
РІЗНІ заголовки на UAH і USD («Бренд» vs «Виробник»), і всі пастки одразу:
«Уточнюється» в артикулі / фото / категорії, числа з комою і NBSP, кирилична «с»
в ID категорії, дубль артикула в межах листа і між листами, артикул із `.0`,
від'ємна ціна, з'їхала кома, артикул без назви.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from catalog.enums import Availability
from catalog.models import Brand, Category, Product, ProductImage
from sync.models import DataIssue, SupplierOffer, SyncRun
from sync.services import run_price_sync
from sync.sheets import FixtureSheetsClient, fixtures_dir

pytestmark = pytest.mark.django_db


@pytest.fixture
def run(demo_source, site_settings, fixture_client):
    return run_price_sync(demo_source, client=fixture_client)


def test_fixture_files_exist():
    for name in ("UAH.csv", "USD.csv", "Основна.csv", "Категорії.csv"):
        assert (fixtures_dir() / name).exists(), f"немає фікстури {name}"


def test_full_fixture_run(run):
    # 3 биті рядки в UAH (-1, з'їхала кома, без назви) + 1 у USD (ціна 0) → PARTIAL
    assert run.status == SyncRun.Status.PARTIAL
    assert run.rows_failed == 4
    assert run.stats["rows_section"] == 2  # «Вбудована техніка» в середині обох листів

    # ЧЕРНЕТКИ (SYNC.md §4.3): «Уточнюється» в артикулі — 3 рядки.
    #   TEKA        — ні артикула, ні ціни → товару не буде взагалі (SKIP)
    #   Кріплення   — є ціна → товар з сурогатним ключем, схований
    #   Саундбар LG — є ціна → товар з сурогатним ключем, схований
    assert run.stats["rows_no_sku"] == 3
    assert run.stats["rows_surrogate_sku"] == 2
    assert run.rows_duplicated == 2  # дубль у межах UAH + дубль між листами


def test_draft_rows_become_hidden_products(run):
    """Рядок без артикула → товар Є, але його НЕ ВИДНО на сайті.

    Слова замовника: «у них нема ні фото ні артикула, нічого крім ціни.
    Роби одразу, щоб товар не відображався».
    """
    draft = Product.objects.get(name__startswith="Саундбар LG")

    assert draft.sku.startswith("AUTO-")
    assert draft.sku_is_surrogate is True
    assert draft.is_incomplete is True
    assert draft.is_active is False  # ← головне: на сайті його немає
    assert draft.base_price == Decimal("280.00")  # ← але ціну ми зберегли


def test_product_with_sku_but_without_photo_is_hidden(run):
    """Критерій видимості — АРТИКУЛ + ФОТО + ЦІНА. Витяжка має артикул, але фото «Уточнюється»."""
    hood = Product.objects.get(sku="2400107")

    assert hood.sku_is_surrogate is False  # артикул справжній
    assert hood.is_incomplete is True  # але фото немає
    assert hood.is_active is False


def test_complete_product_is_visible(run):
    """Товар з артикулом, фото і ціною — видно, навіть якщо характеристик у нього нуль."""
    fridge = Product.objects.get(sku="2400101")

    assert fridge.is_incomplete is False
    assert fridge.is_active is True


def test_data_issues_are_reported_for_the_customer(run):
    """Звіт `data_issues`: дублі, чернетки, фото — з НОМЕРАМИ РЯДКІВ у таблиці замовника."""
    kinds = dict(run.stats["issues"])
    assert kinds[DataIssue.Kind.MISSING_SKU] == 2
    assert kinds[DataIssue.Kind.DUPLICATE_SKU] == 2

    dup = DataIssue.objects.get(run=run, kind=DataIssue.Kind.DUPLICATE_SKU, sheet="UAH")
    assert dup.sku == "2400101"
    assert dup.row_number == 11  # рядок ДРУГОГО (відкинутого) входження
    assert "рядок 4" in dup.message  # і рядок першого, який пішов на сайт
    assert "вітрина" in dup.message  # назви ОБОХ товарів — щоб замовник їх упізнав


def test_grid_completeness_check_passes_on_fixtures(run, fixture_client, demo_source):
    """Порожній хвіст сітки входить у звірку повноти і відсіюється вже в парсері."""
    assert run.rows_expected == fixture_client.row_count(
        "fixtures", "UAH"
    ) + fixture_client.row_count("fixtures", "USD")


def test_thousands_separators(run):
    """NBSP («27 445,50») і звичайний пробіл («6 600,00») — обидва роздільники тисяч."""
    assert Product.objects.get(sku="2400101").base_price == Decimal("27445.50")
    assert Product.objects.get(sku="2400102").base_price == Decimal("6600.00")


def test_sku_with_trailing_dot_zero(run):
    """Sheets віддає артикул числом → «2400206.0». Без зрізання `.0` він не зіставиться НІКОЛИ."""
    assert Product.objects.filter(sku="2400206").exists()
    assert not Product.objects.filter(sku="2400206.0").exists()


def test_cross_sheet_duplicate_uah_wins(run):
    """2400101 є в обох листах: виграє UAH (sort_order=0)."""
    offer = SupplierOffer.objects.get(sku="2400101")
    assert offer.sheet == "UAH"
    assert offer.source_currency == "UAH"


def test_unknown_category_and_cyrillic_prefix(run):
    unmapped = Product.objects.get(sku="2400200")
    assert unmapped.category.external_id == Category.UNMAPPED_EXTERNAL_ID
    assert unmapped.source_category_ext_id == "50549829"
    assert unmapped.is_active is False
    assert "50549829" in run.categories_missing


def test_qty_semantics(run):
    assert Product.objects.get(sku="2400201").availability == Availability.OUT_OF_STOCK  # qty=0
    assert Product.objects.get(sku="2400202").availability == Availability.IN_STOCK  # qty порожня
    assert Product.objects.get(sku="2400202").stock_qty is None


def test_placeholder_photo_not_queued(run):
    """«Уточнюється» у колонці «Фото» не має перетворитись на URL для завантаження."""
    hisense = Product.objects.get(sku="2400203")
    assert hisense.images.count() == 0
    assert not ProductImage.objects.filter(source_url__icontains="Уточню").exists()


def test_new_brand_from_usd_sheet(run):
    assert Brand.objects.get(name="Hisense").needs_review is True
    assert Brand.objects.get(name="Siemens").needs_review is True


def test_specs_and_package_dims_from_fixture(run):
    fridge = Product.objects.get(sku="2400101")
    assert fridge.package_height_cm == Decimal("171.5")
    assert fridge.package_width_cm == Decimal("56.0")
    assert fridge.package_depth_cm == Decimal("35.0")
    assert fridge.package_weight_kg == Decimal("61.500")  # вага В УПАКОВЦІ, не товару (58)
    assert fridge.warranty_months == 12

    washer = Product.objects.get(sku="2400102")
    assert washer.warranty_months == 24  # «2 роки» → місяці

    kettle = Product.objects.get(sku="2400106")  # артикула в SpecSheet немає → фолбек по назві
    assert kettle.package_weight_kg == Decimal("1.400")
    assert kettle.package_height_cm == Decimal("24.0")  # з колонки C


def test_stale_price_sheet_warning(run):
    """Дата зрізу в A2 — ЧИСЛО (Excel serial 46169 = 27.05.2026), а не «14 08 25».

    Без підтримки serial дата не парситься взагалі — і WARN про застарілий прайс
    не спрацьовує НІКОЛИ (тобто ми тихо синкаємо ціни місячної давності).
    """
    assert run.entries.filter(payload__code="STALE_PRICE_SHEET").exists()


def test_usd_price_uses_admin_rate(run, site_settings):
    """КУРСУ USD У ПРАЙСІ НЕМАЄ ЗОВСІМ (звірено 14.07.2026) — він жив в іншому файлі.

    Єдине джерело курсу — адмінка (SiteSettings). Комірка E4, з якої курс читали раніше,
    у реальній таблиці містить «Категорію», а не курс: читати її означало б рахувати ціни
    за числом, узятим зі стелі.
    """
    tv = Product.objects.get(sku="2400201")
    assert tv.usd_rate_used == Decimal("41.5000")
    assert tv.price == Decimal("21580.00")  # 520 × 41.5
    assert run.stats["sheet_usd_rate"] is None


def test_rerun_on_fixtures_is_idempotent(demo_source, site_settings, fixture_client):
    run_price_sync(demo_source, client=fixture_client)
    before = Product.objects.count()
    run2 = run_price_sync(demo_source, client=fixture_client)

    assert run2.products_created == 0
    assert run2.products_deactivated == 0
    assert Product.objects.count() == before


def test_fixture_client_falls_back_to_bare_tab_name():
    """`{spreadsheet_id}__{tab}.csv` → `{tab}.csv`: ті самі фікстури для будь-якого ID."""
    client = FixtureSheetsClient(fixtures_dir())
    assert client.row_count("будь-який-id", "UAH") > 0
