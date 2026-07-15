"""
Пастки прайсу: класифікація рядка (INPUTS.md §3.2, SYNC.md §4.2).

Головна теза: **порожній `sku` — це не одна ситуація, а три**, з трьома різними реакціями.
Звалити їх в одну купу означає або залити лог сотнями фальшивих ERROR (і зробити PARTIAL
нормою, на яку перестануть дивитись), або мовчки проковтнути реальну втрату товару.

Матриця рішень (SYNC.md §4.2):

    # | name | sku | price | що це насправді                          | реакція
    --+------+-----+-------+------------------------------------------+---------------------------
    0 |  –   |  –  |   –   | порожній рядок / хвіст сітки             | SKIP мовчки
    1 |  +   |  –  |   –   | рядок-секція: «Аксесуари до техніки»     | SECTION мовчки, НЕ WARN
    2 |  +   |  –  |   +   | ЧЕРНЕТКА: товар без артикула             | DATA + сурогатний sku
      |      |     |       |                                          |   + WARN ROW_NO_SKU
    2а|  +   |  –  |   –   | назва і К-сть, більше нічого             | SKIP + WARN ROW_NO_SKU
    3 |  –   |  +  |   ?   | артикул без назви — зсув колонок         | SKIP + ERROR ROW_INVALID
    4 |  +   |  +  | –/0/<0| битий рядок                              | SKIP + ERROR ROW_INVALID
    5 |  +   |  +  | >10М  | з'їхала кома (27445 → 2744500)           | SKIP + ERROR ROW_INVALID
    6 |  +   |  +  |   +   | нормальний товар                         | DATA

⚠️ РЯДОК 2 ЗМІНИВСЯ (14.07.2026). Було: `SKIP` — товар без артикула просто не потрапляв на
сайт. У РЕАЛЬНОМУ прайсі таких рядків 237 з 836 — тобто «просто не потрапляє» означало
тихо втратити чверть прайсу. Пояснення замовника:

    «Це бо товари не додані в базу, у них нема ні фото ні артикула, нічого крім ціни.
     Роби одразу, щоб товар не відображався. Візьму з собою на нічну сьогодні комп,
     спробую за ніч додати»

Тобто це ЧЕРНЕТКИ, які замовник дозаповнює прямо зараз. Тому рядок 2 тепер `DATA`:
товар СТВОРЮЄТЬСЯ (з сурогатним ключем із нормалізованої назви), але лежить
`is_active=False` / `is_incomplete=True` — невидимий на сайті й у фіді, доки в прайсі не
з'являться артикул І фото. Тоді наступний синк упізнає його за `name_normalized`,
проставить справжній sku і ВВІМКНЕ сам (SYNC.md §4.3).

Модуль — чисті функції: ні ORM, ні мережі. Рядок сюди приходить уже розкладеним по
логічних іменах колонок (через `PriceSheet.column_map`), а не як сирий список клітинок.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sync.parsing.normalize import PLACEHOLDER_VALUES, clean_or_empty, is_placeholder
from sync.parsing.numbers import NumberParseError, is_ambiguous_thousands, parse_decimal, parse_qty

__all__ = [
    "MAX_SANE_PRICE",
    "PLACEHOLDER_VALUES",
    "Reason",
    "RowKind",
    "RowVerdict",
    "classify_row",
    "is_section_header",
    "split_photos",
]

#: Рядок = логічне ім'я колонки → сира клітинка.
Row = Mapping[str, Any]

#: Ціна вище цієї — не ціна, а з'їхала кома (SYNC.md §4.2, рядок 5).
#: Найдорожча побутова техніка в каталозі — сотні тисяч гривень, не десятки мільйонів.
MAX_SANE_PRICE = Decimal("10000000")


class RowKind(StrEnum):
    DATA = "data"
    SECTION = "section"
    SKIP = "skip"


class Reason(StrEnum):
    OK = "ok"
    EMPTY_ROW = "empty_row"  # мовчки
    SECTION_HEADER = "section_header"  # мовчки
    ROW_NO_SKU = "row_no_sku"  # WARN
    ROW_INVALID = "row_invalid"  # ERROR → rows_failed++


#: Причини, які викликач має рахувати як `rows_failed` (і які женуть прогін у PARTIAL).
ERROR_REASONS: frozenset[Reason] = frozenset({Reason.ROW_INVALID})


@dataclass(frozen=True, slots=True)
class RowVerdict:
    kind: RowKind
    reason: Reason
    warnings: tuple[str, ...] = ()
    #: Заповнене лише для `kind=DATA` — уже розпарсені й перевірені значення.
    price: Decimal | None = None
    qty: int | None = None
    detail: str = ""

    @property
    def is_error(self) -> bool:
        """`True` → `SyncLogEntry(ERROR)` + `rows_failed++` (SYNC.md §4.2)."""
        return self.reason in ERROR_REASONS

    @property
    def is_data(self) -> bool:
        return self.kind is RowKind.DATA


def _cell(row: Row, key: str) -> str:
    """Сира клітинка → чистий рядок. Плейсхолдер («Уточнюється») → ""."""
    return clean_or_empty(row.get(key))


def _raw_cell(row: Row, key: str) -> str:
    """
    Сира клітинка БЕЗ фільтра плейсхолдерів — тільки схлопування пробілів.

    Для `price`: там «Уточнюється» — це НЕ порожнє поле, а битий рядок (ERROR). Якщо
    пропустити ціну через `_cell`, вона тихо стане порожньою → і ми втратимо різницю між
    «ціну не заповнили» і «ціну заповнили сміттям».

    ⚠️ ДЛЯ `sku` ЦЕ ВЖЕ НЕ ТАК (звірено з реальним прайсом 14.07.2026). «Уточнюється» в
    колонці «Артикул» стоїть у 237 рядках з 836 — і означає рівно «артикула ще немає»
    (слова замовника: «товари не додані в базу, у них нема ні фото ні артикула»).
    Тому артикул читається через `_cell` (плейсхолдер → ""), інакше рядок-чернетка
    вважався б повноцінним товаром з артикулом «Уточнюється», і всі 237 злиплись би в один.
    """
    value = row.get(key)
    return "" if value is None else str(value).strip()


def is_section_header(row: Row) -> bool:
    """
    Рядок-секція: `Аксесуари до техніки` — заголовок групи, а не товар (пастка 1).

    Формальна ознака: **є назва і НЕМАЄ ЖОДНОГО іншого даного** — ні артикула, ні ціни,
    ні кількості.

    ⚠️ Ловити за списком імен («Аксесуари…») НЕ МОЖНА: Артур допише нову секцію,
    і вона поїде в каталог як товар з ціною 0.

    ⚠️ Кількість перевіряється теж — і це не педантизм. `Запчастина для духовки TEKA 83340602`
    має назву і К-сть, але не має ні артикула, ні ціни (INPUTS.md §3.2, пастка 3).
    Без перевірки `qty` вона б збіглася з секцією і зникла МОВЧКИ, замість WARN `ROW_NO_SKU`.
    Це той самий рядок, який ми зобов'язані показати людині.
    """
    if not _cell(row, "name"):
        return False
    return not any(_raw_cell(row, key) for key in ("sku", "price", "qty", "old_price"))


def split_photos(raw: Any) -> tuple[str, ...]:
    """
    Фото: розділяємо по `,` / `;` / новому рядку, лишаємо тільки `https://`.

    Кожен елемент проганяється через плейсхолдер-фільтр — інакше в чергу завантаження
    летить «URL» `Уточнюється` і 200 разів довбиться в DNS (SYNC.md §4).
    """
    if is_placeholder(raw):
        return ()
    text = str(raw).replace(";", ",").replace("\n", ",")
    out: list[str] = []
    for chunk in text.split(","):
        url = chunk.strip()
        if url.startswith("https://") and not is_placeholder(url):
            out.append(url)
    return tuple(out)


def classify_row(row: Row) -> RowVerdict:
    """
    Єдина точка рішення «що це за рядок». Реалізує матрицю з шапки модуля.

    Викликач НЕ приймає жодних рішень про пропуск сам — він лише рахує лічильники
    за `verdict.kind` / `verdict.reason` і логує `verdict.warnings`.
    """
    warnings: list[str] = []

    name = _cell(row, "name")
    # ⚠️ `sku` — через плейсхолдер-фільтр: «Уточнюється» = АРТИКУЛА НЕМАЄ (див. `_raw_cell`).
    #    `sku_raw` лишається тільки для діагностики «артикул без назви» (зсув колонок).
    sku = _cell(row, "sku")
    sku_raw = _raw_cell(row, "sku")
    price_raw = _raw_cell(row, "price")

    # --- 0. Порожній рядок / хвіст сітки → мовчки -----------------------------
    # rowCount у Sheets — це МІСТКІСТЬ сітки, а не кількість заповнених рядків: після
    # даних радо лежать тисячі порожніх. Вони входять у звірку повноти (крок 2) і
    # відсіюються ТУТ — без помилки й без WARN (SYNC.md §2, крок 2).
    if not any(_raw_cell(row, key) for key in ("name", "sku", "price", "qty", "old_price")):
        return RowVerdict(RowKind.SKIP, Reason.EMPTY_ROW)

    # --- 1. Рядок-секція → мовчки --------------------------------------------
    if is_section_header(row):
        return RowVerdict(RowKind.SECTION, Reason.SECTION_HEADER, detail=name)

    # --- 3. Артикул без назви → зсув колонок, ERROR ---------------------------
    if not name:
        return RowVerdict(
            RowKind.SKIP,
            Reason.ROW_INVALID,
            detail=f"Артикул {sku_raw!r} без назви — схоже на зсув колонок",
        )

    # --- Кількість. Сміття в К-сті рядок НЕ вбиває: qty=None → default джерела.
    try:
        qty = parse_qty(row.get("qty"))
    except NumberParseError as exc:
        qty = None
        warnings.append(f"QTY_INVALID: {exc}")

    # --- 2а. Ні артикула, ні ціни → створювати нема з чого → SKIP + WARN -------
    # `Запчастина для духовки TEKA 83340602` — є назва і К-сть, більше нічого.
    # Ціна NOT NULL і > 0, тому товару з такого рядка не буде за визначенням.
    if not sku and not price_raw:
        return RowVerdict(
            RowKind.SKIP,
            Reason.ROW_NO_SKU,
            warnings=(*warnings, f"ROW_NO_SKU: {name!r} — немає ні артикула, ні ціни"),
            qty=qty,
        )

    # --- 4/5. Ціна ------------------------------------------------------------
    # ⚠️ Ціна перевіряється ДО артикула: рядок-чернетка (без артикула) теж має пройти
    #    валідацію ціни — інакше «Уточнюється» в колонці «Ціна» проїхало б у каталог.
    try:
        price = parse_decimal(price_raw)
    except NumberParseError as exc:
        return RowVerdict(RowKind.SKIP, Reason.ROW_INVALID, detail=str(exc))

    if price is None:
        return RowVerdict(RowKind.SKIP, Reason.ROW_INVALID, detail="Ціна порожня")
    if price <= 0:
        return RowVerdict(RowKind.SKIP, Reason.ROW_INVALID, detail=f"Ціна не додатна: {price}")
    if price > MAX_SANE_PRICE:
        return RowVerdict(
            RowKind.SKIP,
            Reason.ROW_INVALID,
            detail=f"Ціна {price} > {MAX_SANE_PRICE} — схоже, з'їхала кома",
        )

    if is_ambiguous_thousands(price_raw):
        # "6,600" → 6.600. Розпарситься, але майже напевно занижене в 1000 разів.
        warnings.append(f"AMBIGUOUS_NUMBER: {price_raw!r} → {price}")

    # --- 2. ЧЕРНЕТКА: назва + ціна, але без артикула → DATA + сурогатний ключ ---
    # Товар СТВОРЮЄТЬСЯ, але схований (is_active=False): замовник дозаповнює ці рядки
    # прямо зараз. Викликач (services._parse_sheet) видасть sku = AUTO-<хеш назви>.
    if not sku:
        return RowVerdict(
            RowKind.DATA,
            Reason.ROW_NO_SKU,
            warnings=(*warnings, f"ROW_NO_SKU: {name!r}"),
            price=price,
            qty=qty,
        )

    # --- 6. Нормальний товар --------------------------------------------------
    return RowVerdict(RowKind.DATA, Reason.OK, warnings=tuple(warnings), price=price, qty=qty)
