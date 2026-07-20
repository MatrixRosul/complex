"""
РЕАЛЬНА структура прайсу + чернетки + звіт про проблеми (SYNC.md §1.5, §4.3, §12).

Усе тут — наслідок одного дня: 14.07.2026 запрацював OAuth-доступ до РЕАЛЬНИХ таблиць
замовника, і виявилось, що парсер написаний під структуру, реконструйовану зі скріншотів.
Кожен тест у цьому файлі закриває конкретну розбіжність між вигадкою і дійсністю.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from catalog.models import Category, Product
from sync.models import DataIssue, SyncRun
from sync.parsing import parse_package_dims
from sync.services import import_categories, run_price_sync, surrogate_sku
from sync.sheets import build_column_index, parse_sheet_date
from sync.tests.conftest import (
    SPEC_SPREADSHEET,
    SPREADSHEET,
    WORK_HEADERS,
    WORK_HEADERS_UAH,
    MemorySheetsClient,
    price_grid,
)
from sync.tests.test_sync import SPEC_HEADERS, full_row, make_client, row, spec_row

pytestmark = pytest.mark.django_db


# ===========================================================================
# §4.3 ЧЕРНЕТКИ: артикул з'явився → ТОЙ САМИЙ товар, а не дубль
# ===========================================================================


def test_sku_appears_later_updates_the_same_product_and_shows_it(
    source, site_settings, catalog_refs
):
    """★ НАЙВАЖЛИВІШИЙ ТЕСТ ЦІЄЇ ЗМІНИ.

    Сценарій замовника (він заповнює таблицю ПРЯМО ЗАРАЗ, вночі):

        прогін 1: рядок без артикула й фото  → товар створено, СХОВАНО
        замовник вписує артикул і фото
        прогін 2: той самий рядок, але повний → ТОЙ САМИЙ товар отримує артикул
                                                 і З'ЯВЛЯЄТЬСЯ на сайті САМ

    Якби синк не впізнавав товар за `name_normalized`, у каталозі виникло б ДВА товари:
    осиротілий AUTO-чернетка (з усією історією цін) і новий — з нуля.
    """
    name = "Саундбар Samsung HW-B650F"

    # --- Прогін 1: чернетка ---------------------------------------------------
    run1 = sync_rows(source, [row(name=name, price="280,00", qty="2", cat="c5609711")])

    draft = Product.objects.get()
    assert draft.sku == surrogate_sku(draft.name_normalized)
    assert draft.sku.startswith("AUTO-")
    assert draft.is_active is False  # ← схований
    assert draft.is_incomplete is True
    assert run1.stats["rows_surrogate_sku"] == 1

    # --- Замовник вписав артикул і фото --------------------------------------
    run2 = sync_rows(
        source,
        [full_row(name=name, price="280,00", qty="2", cat="c5609711", sku="2400999")],
    )

    # --- ТОЙ САМИЙ товар: жодного дубля --------------------------------------
    assert Product.objects.count() == 1, "з'явився ДУБЛЬ замість оновлення артикула"

    product = Product.objects.get()
    assert product.pk == draft.pk  # ← фізично той самий рядок у БД
    assert product.sku == "2400999"  # ← артикул оновлено
    assert product.sku_is_surrogate is False
    assert product.is_incomplete is False
    assert product.is_active is True  # ← З'ЯВИВСЯ НА САЙТІ САМ

    assert run2.products_created == 0  # нічого не створювали — тільки оновили
    assert run2.stats["surrogates_adopted"] == 1
    assert run2.stats["products_auto_activated"] == 1
    assert run2.entries.filter(payload__code="SKU_ADOPTED").exists()


def test_draft_stays_hidden_while_photo_is_missing(source, site_settings, catalog_refs):
    """Артикул вписали, а фото — ні. Товар лишається схованим: критерій — АРТИКУЛ + ФОТО."""
    name = "Саундбар LG S70TR"
    sync_rows(source, [row(name=name, price="280,00", cat="c5609711")])

    # артикул є, фото немає
    sync_rows(source, [row(name=name, price="280,00", cat="c5609711", sku="2400998")])

    product = Product.objects.get()
    assert product.sku == "2400998"  # артикул усе одно підхопився
    assert product.sku_is_surrogate is False
    assert product.is_incomplete is True  # але товар неповний
    assert product.is_active is False  # → і досі схований


def test_manually_hidden_product_is_not_resurrected(source, site_settings, catalog_refs):
    """Товар, вимкнений РУКАМИ в адмінці, синк назад не вмикає.

    Автоактивація стосується ЛИШЕ переходу «неповний → повний». Інакше «сховав товар»
    означало б «сховав до наступного синку».
    """
    rows = [full_row(name="Холодильник Gorenje X", price="100,00", cat="c5609731", sku="A1")]
    sync_rows(source, rows)

    Product.objects.filter(sku="A1").update(is_active=False)  # рішення людини
    sync_rows(source, rows)

    assert Product.objects.get(sku="A1").is_active is False


def test_surrogate_sku_is_deterministic():
    """Той самий рядок прайсу → той самий ключ. Інакше кожен синк плодив би нові товари."""
    assert surrogate_sku("boschwtz20410") == surrogate_sku("boschwtz20410")
    assert surrogate_sku("boschwtz20410") != surrogate_sku("boschwtz20411")
    assert surrogate_sku("boschwtz20410").startswith("AUTO-")
    # Назва з самої кирилиці нормалізується в "" → фолбек на хеш назви, а не спільний ключ.
    assert surrogate_sku("", "Пральна машина") != surrogate_sku("", "Холодильник")


# ===========================================================================
# §12 ЗВІТ ПРО ПРОБЛЕМИ В ДАНИХ (для замовника)
# ===========================================================================


def test_duplicate_sku_in_one_sheet_and_between_sheets(source, site_settings, catalog_refs):
    """Замовник: «може таке бути, що я десь провтикав і два товари будуть з одним артикулом».

    Прогін НЕ падає, товар лишається один — але обидва рядки потрапляють у звіт.
    """
    client = make_client(
        uah=[
            full_row(name="Холодильник Gorenje X", price="100,00", cat="c5609731", sku="DUP"),
            full_row(name="Холодильник Gorenje Y", price="200,00", cat="c5609731", sku="DUP"),
        ],
        usd=[full_row(name="Холодильник Gorenje Z", price="300,00", cat="c5609731", sku="DUP")],
    )
    run = run_price_sync(source, client=client)

    assert run.status != SyncRun.Status.FAILED  # дубль НЕ валить прогін
    assert Product.objects.filter(sku="DUP").count() == 1
    assert Product.objects.get(sku="DUP").base_price == Decimal("100.00")  # виграв перший

    issues = DataIssue.objects.filter(run=run, kind=DataIssue.Kind.DUPLICATE_SKU)
    assert issues.count() == 2  # дубль у межах UAH + дубль між листами

    in_sheet = issues.get(sheet="UAH")
    assert in_sheet.row_number == 5  # рядок ВІДКИНУТОГО входження
    assert "рядок 4" in in_sheet.message  # рядок того, що пішов на сайт
    assert "Gorenje X" in in_sheet.message and "Gorenje Y" in in_sheet.message
    assert in_sheet.payload["taken"]["row"] == 4
    assert in_sheet.payload["dropped"]["row"] == 5


def test_unknown_category_issue_counts_affected_products(source, site_settings, catalog_refs):
    client = make_client(
        uah=[
            full_row(name="Товар 1", price="100,00", cat="c99999999", sku="A1"),
            full_row(name="Товар 2", price="200,00", cat="c99999999", sku="A2"),
        ]
    )
    run = run_price_sync(source, client=client)

    issue = DataIssue.objects.get(run=run, kind=DataIssue.Kind.UNKNOWN_CATEGORY)
    assert issue.payload["external_id"] == "99999999"
    assert issue.payload["products_affected"] == 2
    assert "sync_categories" in issue.message  # ← сказано, ЩО зробити


def test_missing_category_empty_cell_is_reported_per_row(source, site_settings, catalog_refs):
    """★ ГОЛОВНА ПРОГАЛИНА (14.07.2026): у листі UAH колонка «Категорія» ПОРОЖНЯ в 202
    товарах — і жоден DataIssue цього не показував (порожня категорія — це НЕ
    unknown_category, там ID Є, а просто невідомий). Товар тихо осідав у «__unmapped__».

    MISSING_CATEGORY закриває прогалину: кожен такий рядок — ОКРЕМИЙ запис у звіті,
    з номером рядка й артикулом — точно як MISSING_SKU / MISSING_PHOTO.
    """
    client = make_client(
        uah=[full_row(name="Товар без категорії", price="100,00", sku="NOCAT1")],  # cat=""
    )
    run = run_price_sync(source, client=client)

    issue = DataIssue.objects.get(run=run, kind=DataIssue.Kind.MISSING_CATEGORY)
    assert issue.sheet == "UAH"
    assert issue.row_number == 4  # перший рядок даних (data_start_row=4)
    assert issue.sku == "NOCAT1"
    assert "Категорія" in issue.message
    assert "потрапляє в каталог" in issue.message  # сказано, ЧОМУ це серйозно
    assert run.stats["rows_missing_category"] == 1
    assert run.stats["issues"][DataIssue.Kind.MISSING_CATEGORY] == 1


def test_missing_category_placeholder_is_missing_not_unknown(source, site_settings, catalog_refs):
    """«Уточнюється» в колонці «Категорія» — те саме, що порожньо: замовник ще не вписав ID.

    Це MISSING_CATEGORY, а НЕ UNKNOWN_CATEGORY: дія замовника інша (вписати ID з листа
    «Категорії»), а не додавати рядок у довідник категорій.
    """
    client = make_client(
        uah=[
            full_row(name="Товар з плейсхолдером", price="100,00", cat="Уточнюється", sku="NOCAT2")
        ],
    )
    run = run_price_sync(source, client=client)

    assert DataIssue.objects.filter(
        run=run, kind=DataIssue.Kind.MISSING_CATEGORY, sku="NOCAT2"
    ).exists()
    assert not DataIssue.objects.filter(run=run, kind=DataIssue.Kind.UNKNOWN_CATEGORY).exists()


def test_unknown_category_id_is_not_reported_as_missing(source, site_settings, catalog_refs):
    """ID категорії Є («c99999999»), просто немає в довіднику → лишається UNKNOWN_CATEGORY,
    окремий вид від MISSING_CATEGORY (SYNC.md §12): це РІЗНІ проблеми з РІЗНИМИ діями замовника.
    """
    client = make_client(
        uah=[full_row(name="Невідома категорія", price="100,00", cat="c99999999", sku="NOCAT3")],
    )
    run = run_price_sync(source, client=client)

    assert DataIssue.objects.filter(run=run, kind=DataIssue.Kind.UNKNOWN_CATEGORY).exists()
    assert not DataIssue.objects.filter(run=run, kind=DataIssue.Kind.MISSING_CATEGORY).exists()
    assert run.stats["rows_missing_category"] == 0


def test_valid_category_produces_no_category_issue(source, site_settings, catalog_refs):
    """Товар з валідною категорією — жодного issue про категорію."""
    client = make_client(
        uah=[full_row(name="Товар з категорією", price="100,00", cat="c5609731", sku="OKCAT")],
    )
    run = run_price_sync(source, client=client)

    assert not DataIssue.objects.filter(
        run=run, kind__in=[DataIssue.Kind.MISSING_CATEGORY, DataIssue.Kind.UNKNOWN_CATEGORY]
    ).exists()
    assert run.stats["rows_missing_category"] == 0


def test_spec_not_found_is_reported_even_in_dry_run(source, site_settings, catalog_refs):
    """Артикул є, а рядка в «Основній» немає → товар без габаритів для Нової Пошти.

    ⚠️ Перевіряється в DRY-RUN: саме заради цього списку замовник і запускає пробний прогін.
    """
    client = make_client(
        uah=[full_row(name="Холодильник Gorenje X", price="100,00", cat="c5609731", sku="NOSPEC")],
        # у «Основній» є ІНШИЙ товар — тобто лист живий, просто нашого артикула в ньому немає
        specs=[spec_row(name="Пральна машина Bosch Y", sku="2400102")],
    )
    run = run_price_sync(source, client=client, dry_run=True)

    assert Product.objects.count() == 0  # dry-run нічого не записав…
    issue = DataIssue.objects.get(run=run, kind=DataIssue.Kind.SPEC_NOT_FOUND)  # …а звіт лишився
    assert issue.sku == "NOSPEC"
    assert "Основна" in issue.message


def test_data_issues_command_exports_valid_csv(source, site_settings, catalog_refs, tmp_path):
    """`manage.py data_issues --csv` → файл, який можна відкрити в Google Sheets і віддати."""
    client = make_client(
        uah=[
            row(name="Чернетка без артикула", price="100,00"),  # MISSING_SKU + MISSING_PHOTO
            full_row(name="Дубль A", price="100,00", cat="c5609731", sku="DUP"),
            full_row(name="Дубль B", price="200,00", cat="c5609731", sku="DUP"),
            full_row(name="Невідома категорія", price="300,00", cat="c99999999", sku="A3"),
        ]
    )
    run = run_price_sync(source, client=client)

    out = tmp_path / "issues.csv"
    call_command("data_issues", f"--run={run.id}", f"--csv={out}", stdout=StringIO())

    with out.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert rows, "CSV порожній"
    assert set(rows[0]) == {
        "Тип проблеми",
        "Лист",
        "Рядок",
        "Артикул",
        "Назва товару",
        "Що зробити",
    }

    kinds = {r["Тип проблеми"] for r in rows}
    assert kinds >= {"Дубль артикула", "Немає артикула", "Немає фото", "Невідома категорія"}

    # Кожен рядок має вести замовника до КОНКРЕТНОЇ клітинки його таблиці.
    dup = next(r for r in rows if r["Тип проблеми"] == "Дубль артикула")
    assert dup["Лист"] == "UAH"
    assert dup["Рядок"] and dup["Що зробити"]


# ===========================================================================
# §1.5 РЕАЛЬНА СТРУКТУРА ЛИСТА
# ===========================================================================


def test_columns_are_mapped_by_header_not_by_position(source, site_settings, catalog_refs):
    """UAH і USD називають ті самі колонки ПО-РІЗНОМУ («Бренд» vs «Виробник»).

    Позиційний мапінг тут дав би бренд у країні й навпаки. Обидва листи читаються ОДНИМ
    column_map — через синоніми.
    """
    client = MemorySheetsClient(
        {
            (SPREADSHEET, "UAH"): price_grid(
                [
                    full_row(
                        name="Холодильник Gorenje X",
                        price="100,00",
                        cat="c5609731",
                        brand="Gorenje",
                        country="Словенія",
                        sku="U1",
                    )
                ],
                headers=WORK_HEADERS_UAH,  # ← «Бренд», «Країна виробництва»
            ),
            (SPREADSHEET, "USD"): price_grid(
                [
                    full_row(
                        name="Пральна машина Bosch Y",
                        price="200,00",
                        cat="c5609731",
                        brand="Bosch",
                        country="Німеччина",
                        sku="D1",
                    )
                ],
                headers=WORK_HEADERS,  # ← «Виробник», «Країна_виробник»
            ),
            (SPEC_SPREADSHEET, "Основна"): [SPEC_HEADERS],
        }
    )
    run = run_price_sync(source, client=client)

    assert run.status == SyncRun.Status.SUCCESS
    assert Product.objects.get(sku="U1").brand.name == "Gorenje"
    assert Product.objects.get(sku="U1").country.code == "SI"
    assert Product.objects.get(sku="D1").brand.name == "Bosch"
    assert Product.objects.get(sku="D1").country.code == "DE"


def test_positional_column_for_junk_header():
    """«Основна»: колонка з БРЕНДОМ має заголовок-сміття («2401579») → оголошується як «#8»."""
    headers = [
        "Найменування",
        "Країна виробництва",
        "",
        "",
        "",
        "",
        "Артикул",
        "2401579",
        "Категорія",
    ]
    index = build_column_index(
        headers, {"name": "Найменування", "sku": "Артикул", "brand": "#8"}, ("sku",)
    )
    assert index["brand"] == 7  # 8-ма колонка, 0-based
    assert index["sku"] == 6


def test_optional_columns_may_be_absent():
    """Немає «К-сть» / «Категорія» — це НЕ помилка (лист може бути коротшим)."""
    index = build_column_index(
        ["Найменування", "Ціна", "Артикул"],
        {"name": "Найменування", "price": "Ціна", "sku": "Артикул", "qty": "К-сть"},
        ("sku", "name", "price"),
    )
    assert "qty" not in index
    assert index == {"name": 0, "price": 1, "sku": 2}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (46211, "2026-07-08"),  # A2 РЕАЛЬНОГО прайсу — ЧИСЛО, а не текст
        (46169.0, "2026-05-27"),
        ("46211", "2026-07-08"),
        ("14 08 25", "2025-08-14"),  # старий формат теж має працювати
        ("не дата", None),
        (5, None),  # не serial-дата, а просто число
    ],
)
def test_sheet_date_parses_excel_serial(raw, expected):
    """Без гілки serial дата зрізу не парситься НІКОЛИ → WARN про застарілий прайс мовчить."""
    result = parse_sheet_date(raw)
    assert (result.isoformat() if result else None) == expected


def test_real_package_dims_format():
    """Замовник підтвердив письмово: роздільник — ЗІРОЧКА, одиниці — САНТИМЕТРИ.

    «В базі бачиш розміри через * поділені. Це нормально?» → «Нормально.
     Лиш скажи в см чи в мм» → «См»
    """
    dims = parse_package_dims("600*650*1850")
    assert dims.height_cm == Decimal("600")
    assert dims.width_cm == Decimal("650")
    assert dims.depth_cm == Decimal("1850")


# ===========================================================================
# §6.6 ДОВІДНИК КАТЕГОРІЙ
# ===========================================================================


def test_import_categories_builds_the_tree(db, fixture_client):
    stats = import_categories("fixtures", "Категорії", client=fixture_client)

    assert stats.created == 10
    assert stats.skipped == 0
    assert not stats.problems

    # 'c87728634' → external_id БЕЗ префікса (саме так його чистить norm_category_id з прайсу)
    root = Category.objects.get(external_id="87728634")
    assert root.name == "Аудіо, відео техніка"
    assert root.parent_id is None
    assert root.slug == "audio-video-tekhnika"  # кирилиця → транслітерація, не порожній slug

    child = Category.objects.get(external_id="5609711")
    assert child.parent_id == root.pk
    assert child.path == "87728634/5609711"

    grandchild = Category.objects.get(external_id="12462857")
    assert grandchild.depth == 2
    assert grandchild.path == "87728379/5589021/12462857"


def test_import_categories_is_idempotent(db, fixture_client):
    import_categories("fixtures", "Категорії", client=fixture_client)
    stats = import_categories("fixtures", "Категорії", client=fixture_client)

    assert stats.created == 0
    assert stats.updated == 0
    assert stats.unchanged == 10
    assert Category.objects.filter(external_id="87728634").count() == 1


def test_imported_categories_rescue_products_from_unmapped(source, site_settings, fixture_client):
    """Довідник знімає проблему «__unmapped__»: товар з категорією c5609711 стає видимим."""
    # c12462857 «Газові варильні поверхні» є в довіднику, але НЕ в дереві каталогу
    client = make_client(
        uah=[
            full_row(name="Варильна поверхня Gorenje X", price="100,00", cat="c12462857", sku="H1")
        ]
    )
    run_price_sync(source, client=client)
    assert Product.objects.get(sku="H1").category.external_id == Category.UNMAPPED_EXTERNAL_ID
    assert Product.objects.get(sku="H1").is_active is False  # ← на сайт не потрапив

    import_categories("fixtures", "Категорії", client=fixture_client)
    run_price_sync(source, client=client)

    product = Product.objects.get(sku="H1")
    assert product.category.external_id == "12462857"
    assert product.category.path == "87728379/5589021/12462857"
    assert product.is_active is True  # ← і САМ з'явився на сайті


# ===========================================================================
# НАЗВА ТОВАРУ: синк її НЕ ЗАТИРАЄ, але про розбіжність — ПОВІДОМЛЯЄ
# ===========================================================================


def test_renamed_in_sheet_keeps_site_name_but_reports_the_drift(
    source, site_settings, catalog_refs
):
    """★ Замовник перейменував товар у прайсі — на сайті лишилась стара назва.

    Це НЕ баг, а свідоме правило (SYNC.md §0): `Product.name` — власність адмінки, інакше
    кожен прогін затирав би ручні правки контент-менеджера. Але МОВЧАТИ про розбіжність
    не можна: замовник міняє назву в таблиці, на сайті нічого не відбувається, і він про
    це не дізнається НІЯК.

    Тепер синк лишає назву старою І кладе рядок у звіт DataIssue з обома назвами.
    """
    sync_rows(source, [row(sku="A1", name="Кондиціонер Hisense", price="100,00", qty="1")])
    assert Product.objects.get(sku="A1").name == "Кондиціонер Hisense"

    # Замовник перейменував рядок у прайсі (той самий артикул).
    run = sync_rows(
        source,
        [row(sku="A1", name="Витяжка вбудована ELICA ERA C WH/A/52", price="100,00", qty="1")],
    )

    # 1. Назва на сайті НЕ змінилась — ручні правки в безпеці.
    assert Product.objects.get(sku="A1").name == "Кондиціонер Hisense"

    # 2. Але розбіжність ПОТРАПИЛА У ЗВІТ замовнику — з обома назвами.
    issue = DataIssue.objects.get(run=run, kind=DataIssue.Kind.NAME_CHANGED, sku="A1")
    assert issue.payload["name_in_db"] == "Кондиціонер Hisense"
    assert issue.payload["name_in_sheet"] == "Витяжка вбудована ELICA ERA C WH/A/52"


def test_no_drift_issue_when_overwrite_names_is_on(source, site_settings, catalog_refs):
    """Якщо перезапис назв УВІМКНЕНО — розбіжності не виникає, і скаржитись нема на що."""
    source.overwrite_names = True
    source.save(update_fields=["overwrite_names"])

    sync_rows(source, [row(sku="A1", name="Стара назва", price="100,00", qty="1")])
    run = sync_rows(source, [row(sku="A1", name="Нова назва", price="100,00", qty="1")])

    assert Product.objects.get(sku="A1").name == "Нова назва"  # синк назву ПЕРЕЗАПИСАВ
    assert not DataIssue.objects.filter(run=run, kind=DataIssue.Kind.NAME_CHANGED).exists()


def sync_rows(source, rows):
    return run_price_sync(source, client=make_client(uah=rows))
