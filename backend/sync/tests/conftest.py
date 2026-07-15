"""Спільні фікстури тестів синку. Жодної мережі: тільки CSV-фікстури і сітки в пам'яті."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from catalog.enums import Availability, SourceCurrency
from catalog.models import AttributeGroup, Brand, Category, Country, Unit
from core.models import SiteSettings
from sync.models import PriceSheet, PriceSource, SpecSheet
from sync.sheets import FixtureSheetsClient, _pad, fixtures_dir

SPREADSHEET = "test-price"
SPEC_SPREADSHEET = "test-specs"

PRICE_COLUMN_MAP_CLIENT = {  # лист без К-сті / Категорії / Бренда — колонки ОПЦІЙНІ
    "name": "Найменування",
    "price": "Ціна",
    "currency": "Валюта",
    "country": "Країна-виробник",
    "photo": "Фото",
    "sku": "Артикул",
}
#: РЕАЛЬНА карта колонок прайсу (звірено 14.07.2026). Один map на ОБИДВА листи: заголовки в
#: них РІЗНІ («Бренд» на UAH vs «Виробник» на USD), тому колонки оголошені СИНОНІМАМИ —
#: рівно так, як це робить міграція sync/0004_real_sheet_geometry.
PRICE_COLUMN_MAP_WORK = {
    "name": "Найменування",
    "price": "Ціна",
    "qty": "К-сть",
    "currency": "Валюта",
    "category": "Категорія",
    "brand": ["Бренд", "Виробник"],
    "country": ["Країна виробництва", "Країна_виробник"],
    "photo": "Фото",
    "sku": "Артикул",
}
#: «Основна»: колонка H (8-ма) містить БРЕНД під заголовком-сміттям («2401579») → позиційно.
SPEC_COLUMN_MAP = {
    "name": "Найменування",
    "country": "Країна виробництва",
    "package_dims": "Розміри в упакуванні (см)",
    "image": "Зображення",
    "sku": "Артикул",
    "brand": "#8",
    "category": "Категорія",
    "spec_triplet_start": "Назва_Характеристики",
}


class MemorySheetsClient:
    """Сітка в пам'яті. `grids = {(spreadsheet_id, tab): [[...], ...]}`.

    `drop_rows` імітує НЕДОЧИТАНИЙ чанк: віддає менше рядків, ніж є в сітці, — це і є
    той сценарій, заради якого існує жорстка звірка повноти (SYNC.md §2, крок 2).
    """

    def __init__(self, grids: dict[tuple[str, str], list[list[Any]]], drop_rows: int = 0) -> None:
        self.grids = grids
        self.drop_rows = drop_rows

    def row_count(self, spreadsheet_id: str, tab: str) -> int:
        return len(self.grids[(spreadsheet_id, tab)])

    def read_rows(
        self, spreadsheet_id: str, tab: str, first_row: int, last_row: int
    ) -> list[list[Any]]:
        grid = self.grids[(spreadsheet_id, tab)]
        rows = [list(grid[i]) if i < len(grid) else [] for i in range(first_row - 1, last_row)]
        padded = _pad(rows, last_row - first_row + 1)
        # Ріжемо ЛИШЕ читання діапазону даних (кілька рядків), а не читання заголовка/комірки:
        # інакше тест ламався б на іншому кроці й нічого не перевіряв.
        if self.drop_rows and last_row > first_row:
            return padded[: -self.drop_rows]  # «чанк не доїхав після ретраїв»
        return padded


def price_grid(
    rows: list[list[Any]], *, headers: list[str], date: Any = "", section: str = "Секція"
) -> list[list[Any]]:
    """РЕАЛЬНА сітка листа прайсу (звірено 14.07.2026):

        рядок 1  ЗАГОЛОВКИ
        рядок 2  A2 = дата зрізу (Excel serial, напр. 46169)
        рядок 3  рядок-секція («Аксесуари до техніки»)
        рядок 4+ ДАНІ

    ⚠️ Курсу USD у прайсі НЕМАЄ — колишній параметр `rate` (комірка E4) прибрано разом з
    рядками-контактами, яких у реальній таблиці теж ніколи не було.
    """
    width = len(headers)
    meta = [""] * width
    if date != "":
        meta[0] = date
    return [
        headers,
        meta,
        [section] + [""] * (width - 1),
        *rows,
    ]


#: Заголовки РЕАЛЬНОГО листа USD.
WORK_HEADERS = [
    "Найменування",
    "Ціна",
    "К-сть",
    "Валюта",
    "Категорія",
    "Виробник",
    "Країна_виробник",
    "Фото",
    "Артикул",
]

#: Заголовки РЕАЛЬНОГО листа UAH — ІНШІ («Бренд», «Країна виробництва»). Саме на цій
#: розбіжності ламався б будь-який позиційний мапінг.
WORK_HEADERS_UAH = [
    "Найменування",
    "Ціна",
    "К-сть",
    "Валюта",
    "Категорія",
    "Бренд",
    "Країна виробництва",
    "Фото",
    "Артикул",
]


@pytest.fixture(autouse=True)
def _isolated_media(settings: Any, tmp_path: Any) -> None:
    """Сирі знімки прогонів (крок 7) пишуться через `default_storage`.

    Без цієї ізоляції кожен тест лишав би .csv.gz у робочому `media/` проєкту — сотні файлів
    за один прогін pytest.
    """
    settings.MEDIA_ROOT = str(tmp_path / "media")


@pytest.fixture(autouse=True)
def _isolated_cache(settings: Any) -> None:
    """Кеш — ЛОКАЛЬНИЙ, а не спільний Redis.

    ⚠️ ЦЕ НЕ ГІГІЄНА, ЦЕ БАГ, ЯКИЙ УЖЕ СПРАЦЮВАВ. `SiteSettings.get_solo()` кешує ОБ'ЄКТ
    у Redis (`core:sitesettings`), а тестова БД і dev-БД користуються ОДНИМ І ТИМ САМИМ
    Redis (db1). Тест, який виставляє собі `usd_rate=41.5, price_rounding=unit`, ПЕРЕЗАПИСУЄ
    кеш робочого середовища — і наступний реальний `manage.py sync_prices` рахує ціни за
    правилом округлення З ТЕСТУ, а не з бази. Спіймано наживо: у БД було `nine`, у кеші
    після pytest — `unit`, і весь каталог порахувався не за тим правилом.

    ⚠️ Фікстура закриває це ЛИШЕ для тестів sync/. Правильне місце — глобальний conftest.py
    або `CACHES` у config/settings (окремий Redis-db для тестів). Див. звіт.
    """
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "complex-tests",
        }
    }


@pytest.fixture
def site_settings(db: Any) -> SiteSettings:
    solo = SiteSettings.get_solo()
    solo.usd_rate = Decimal("41.5000")
    solo.price_rounding = SiteSettings.Rounding.UNIT
    solo.sync_enabled = True
    SiteSettings.objects.filter(pk=1).update(
        usd_rate=solo.usd_rate, price_rounding=solo.price_rounding
    )
    SiteSettings.invalidate()
    return SiteSettings.get_solo()


@pytest.fixture
def catalog_refs(db: Any) -> dict[str, Any]:
    """Мінімальний довідник: категорії, країни, бренди, група атрибутів, одиниці."""
    cats = {}
    for ext_id, name in (
        ("5609731", "Холодильники"),
        ("5609711", "Телевізори"),
        ("5609733", "Посудомийні машини"),
        ("5609722", "Духові шафи"),
    ):
        cat = Category(external_id=ext_id, name=name, slug=f"c{ext_id}")
        if hasattr(cat, "name_uk"):
            cat.name_uk, cat.slug_uk = name, f"c{ext_id}"
        cat.save()
        cats[ext_id] = cat

    for code, name, aliases in (
        ("SI", "Словенія", ["Slovenia"]),
        ("DE", "Німеччина", ["Germany"]),
        ("PL", "Польща", ["Poland"]),
    ):
        country = Country(code=code, name=name, slug=code.lower(), aliases=aliases)
        if hasattr(country, "name_uk"):
            country.name_uk = name
        country.save()

    Brand.objects.create(name="Gorenje", slug="gorenje")
    Brand.objects.create(name="Bosch", slug="bosch")

    group = AttributeGroup(code="main", name="Основні")
    if hasattr(group, "name_uk"):
        group.name_uk = "Основні"
    group.save()
    for code, name in (("cm", "см"), ("kg", "кг"), ("month", "міс"), ("l", "л")):
        unit = Unit(code=code, name=name)
        if hasattr(unit, "name_uk"):
            unit.name_uk = name
        unit.save()

    return {"categories": cats, "group": group}


@pytest.fixture
def source(db: Any, catalog_refs: dict[str, Any]) -> PriceSource:
    """Джерело з ДВОМА листами (UAH з sort_order=0 виграє дублі) і таблицею характеристик."""
    src = PriceSource.objects.create(
        code="complex-test",
        name="Complex (тест)",
        is_primary=True,
        priority=100,
        spreadsheet_id=SPREADSHEET,
        default_availability=Availability.IN_STOCK,
    )
    PriceSheet.objects.create(
        price_source=src,
        tab_name="UAH",
        currency=SourceCurrency.UAH,
        column_map=PRICE_COLUMN_MAP_WORK,
        sort_order=0,
        header_row=1,
        data_start_row=4,
        rate_cell="",
        date_cell="",
    )
    PriceSheet.objects.create(
        price_source=src,
        tab_name="USD",
        currency=SourceCurrency.USD,
        column_map=PRICE_COLUMN_MAP_WORK,
        sort_order=1,
        header_row=1,
        data_start_row=4,
        rate_cell="",
        date_cell="",
    )
    SpecSheet.objects.create(
        price_source=src,
        spreadsheet_id=SPEC_SPREADSHEET,
        tab_name="Основна",
        column_map=SPEC_COLUMN_MAP,
        header_row=1,
        data_start_row=2,
        spec_triplet_start_col=10,
    )
    return src


@pytest.fixture
def fixture_client() -> FixtureSheetsClient:
    """Клієнт на РЕАЛЬНИХ CSV-фікстурах (sync/fixtures/) — тих самих, що й у демо-прогоні."""
    return FixtureSheetsClient(fixtures_dir())


@pytest.fixture
def demo_source(db: Any, catalog_refs: dict[str, Any]) -> PriceSource:
    """Джерело під CSV-фікстури — геометрія РЕАЛЬНОЇ таблиці (заголовки 1, дані з 4, A2 = дата)."""
    src = PriceSource.objects.create(
        code="complex-fixtures",
        name="Complex (фікстури)",
        is_primary=True,
        priority=100,
        spreadsheet_id="fixtures",
        default_availability=Availability.IN_STOCK,
    )
    for tab, currency, order in (("UAH", SourceCurrency.UAH, 0), ("USD", SourceCurrency.USD, 1)):
        PriceSheet.objects.create(
            price_source=src,
            tab_name=tab,
            currency=currency,
            column_map=PRICE_COLUMN_MAP_WORK,
            sort_order=order,
            header_row=1,
            data_start_row=4,
            rate_cell="",  # курсу в прайсі немає
            date_cell="A2",
        )
    SpecSheet.objects.create(
        price_source=src,
        spreadsheet_id="fixtures",
        tab_name="Основна",
        column_map=SPEC_COLUMN_MAP,
        header_row=1,
        data_start_row=2,
        spec_triplet_start_col=10,
    )
    return src
