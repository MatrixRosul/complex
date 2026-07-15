"""
ДВИГУН СИНХРОНІЗАЦІЇ (SYNC.md §2–§6).

Архітектура в одному рядку:

    Прайс → SupplierOffer (пісочниця джерела) → project_offers() → Product

Синк НІКОЛИ не пише в `Product` напряму. Кожне джерело живе у власній пісочниці
`(price_source, sku)`: upsert і деактивація не перетинаються ЗА ПОБУДОВОЮ, тому прайс
постачальника фізично не може деактивувати склад Complex (SYNC.md §1).

П'ять речей, кожна з яких у v1 була окремим тихим багом — і кожна тут має свій рядок коду:

  1. `price_locked` блокує ЛИШЕ ціну (блок 2 проєкції). Наявність і службові поля
     оновлюються ЗАВЖДИ — інакше «зафіксував ціну» дорівнює «товар зник з продажу».
  2. `updated_at` — у КОЖНОМУ `update_fields` і КОЖНОМУ `qs.update()`. Без цього
     sitemap/ISR/інкрементальний експорт мовчки брешуть.
  3. Ціну рахує БД (`complex_price_uah()`) — одна функція на синк, recalc і адмінку.
     Python-двійника формули тут НЕМАЄ і бути не може.
  4. Дедуп sku ДО `bulk_create` — інакше PostgreSQL кидає
     «ON CONFLICT DO UPDATE cannot affect row a second time» і валить увесь батч.
  5. Advisory-lock у PostgreSQL, а не Redis: він звільняється сам при обриві з'єднання
     (OOM-kill воркера не лишає завислого локу) і не має проблеми fencing-токена.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import logging
import statistics
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from django.utils.text import slugify

from catalog.enums import Availability, ProductSource, SourceCurrency
from catalog.models import (
    Attribute,
    AttributeGroup,
    AttributeOption,
    Brand,
    Category,
    Country,
    Product,
    ProductAttributeValue,
    ProductImage,
    Unit,
)
from catalog.services.naming import normalize_model_name, translit_slug
from catalog.services.pricing import price_reason
from core.models import SiteSettings
from sync.models import (
    DataIssue,
    PriceSheet,
    PriceSource,
    ProductPriceSnapshot,
    SupplierOffer,
    SyncLogEntry,
    SyncRun,
)
from sync.parsing import (
    RowKind,
    Spec,
    classify_row,
    clean_or_empty,
    collapse_spaces,
    extract_package_dims_and_weight,
    find_spec_match,
    format_spec_value,
    parse_decimal,
    parse_package_dims,
    parse_warranty,
    split_photos,
)
from sync.sheets import (
    HeaderMismatch,
    SheetsError,
    SpecRecord,
    get_client,
    norm_header,
    parse_sheet_date,
    read_price_sheet,
    read_spec_sheet,
)

log = logging.getLogger(__name__)

L = SyncLogEntry.Level
A = SyncLogEntry.Action
I = DataIssue.Kind  # noqa: E741 — «I» тут читається як «Issue», і саме так воно й вживається

#: Префікс сурогатного артикула (SYNC.md §4.3). Формат: `AUTO-<12 hex від нормалізованої назви>`.
#:
#: ⚠️ ЧОМУ ВІН ВЗАГАЛІ ІСНУЄ. У РЕАЛЬНОМУ прайсі 237 з 836 рядків мають у колонці «Артикул»
#: слово «Уточнюється». Це не збій — це рядки-чернетки, які замовник дозаповнює:
#:     «Це бо товари не додані в базу, у них нема ні фото ні артикула, нічого крім ціни.
#:      Роби одразу, щоб товар не відображався.»
#: Мовчки їх пропускати не можна (чверть прайсу зникає), а створити товар без ключа
#: неможливо — його нічим оновлювати на наступному прогоні. Тому ключ будується з
#: НОРМАЛІЗОВАНОЇ НАЗВИ — рівно того, чим ми й так зіставляємо прайс із характеристиками.
#:
#: ⚠️ Хеш, а не сама назва: `Product.sku` — 64 символи, а назви бувають довші й з кирилицею.
#: blake2s-6 (12 hex) на 246 рядках дає 0 колізій; випадкова колізія все одно ловиться
#: дедупом (обидва рядки отримають той самий sku → DUPLICATE_SKU у звіті).
SURROGATE_SKU_PREFIX = "AUTO-"


#: На реальних даних `normalize_model_name()` порожній НЕ буває (0 з 246), але якщо назва
#: складається лише з кирилиці — нормалізація дає "" і всі такі рядки злиплись би в ОДИН sku.
#: Тому фолбек — хеш від самої назви.
def surrogate_sku(name_normalized: str, name: str = "") -> str:
    """`"iiboschwtz20410"` → `"AUTO-3F2A9C1B7E04"`. Детермінований: той самий рядок прайсу
    на кожному прогоні дає ТОЙ САМИЙ sku — інакше кожен синк плодив би нові товари.
    """
    key = name_normalized or collapse_spaces(name).casefold()
    digest = hashlib.blake2s(key.encode("utf-8"), digest_size=6).hexdigest()
    return f"{SURROGATE_SKU_PREFIX}{digest.upper()}"


#: Скільки офферів пишемо одним `bulk_create`. Битий рядок не має забрати з собою сусідів —
#: тому кожен батч у власному savepoint, а на IntegrityError йде порядковий фолбек.
BATCH_SIZE = 500

#: Розбіжність курсу «таблиця vs адмінка», після якої показуємо жовтий банер.
RATE_MISMATCH_THRESHOLD = Decimal("0.03")

#: Прайс, старший за стільки днів, — привід для WARN: Артур давно не оновлював таблицю,
#: а ми старанно синкаємо старі ціни.
STALE_SHEET_DAYS = 14

#: Поріг «qty обнулили масово» (крок 6, guard #2). У PriceSource його немає — і не треба:
#: це не налаштування постачальника, а детектор зсунутої колонки.
GUARD_MAX_QTY_ZEROED_PCT = Decimal("20")

#: Габарит упаковки, більший за цей (см), — майже напевно міліметри в колонці «см».
SUSPICIOUS_DIM_CM = Decimal("300")

#: `update_fields` для upsert-а оффера. `updated_at` — ОБОВ'ЯЗКОВО (SYNC.md §2, крок 8).
OFFER_FIELDS = [
    "sku_is_surrogate",
    "name",
    "base_price",
    "base_old_price",
    "source_currency",
    "stock_qty",
    "availability",
    "order_lead_days",
    "category_ext_id",
    "brand_raw",
    "country_raw",
    "mpn",
    "photo_urls",
    "raw",
    "is_active",
    "last_seen_run",
    "sheet",
    "row_number",
    "updated_at",
]


class SyncError(RuntimeError):
    """Прогін неможливо продовжити → FAILED. Каталог НЕ чіпаємо."""


class GuardAborted(RuntimeError):
    """Спрацював запобіжник → ABORTED. Каталог НЕ чіпаємо, чекаємо людину."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("Спрацював запобіжник")
        self.report = report


class _DryRunRollback(Exception):
    """Внутрішнє: відкотити транзакцію запису після dry-run."""


# ---------------------------------------------------------------------------
# Нормалізатори (SYNC.md §2, крок 4)
# ---------------------------------------------------------------------------


def norm_sku(raw: Any) -> str:
    """`2400042.0` (Sheets віддав ЧИСЛОМ) → `"2400042"`. Пробіли всіх видів — геть, upper.

    ⚠️ Без зрізання `.0` артикули з прайсу і з таблиці характеристик не зіставляться
    ЖОДНОГО разу, і вся §6 мовчки працюватиме на фолбеку по назві.
    """
    if isinstance(raw, float):
        s = f"{raw:.0f}" if raw.is_integer() else str(raw)
    elif isinstance(raw, int):
        s = str(raw)
    else:
        s = clean_or_empty(raw).replace(" ", "")
    s = s.strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.upper()


def norm_category_id(raw: Any) -> str:
    """`"c50549829"` / `"с50549829"` (КИРИЛИЧНА «с»!) / `"50549829.0"` → `"50549829"`."""
    s = clean_or_empty(raw).replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    if s[:1] in ("c", "C", "с", "С"):  # латинська і кирилична — обидві
        s = s[1:]
    return s.strip()


def resolve_availability(qty: int | None, src: PriceSource) -> tuple[str, int | None]:
    """`qty` → (наявність, строк постачання). SYNC.md §2, крок 9.

    ⚠️ `None` і `0` означають ПРОТИЛЕЖНІ речі:
        None → колонки «К-сть» немає (клієнтський лист!) або клітинка порожня.
               Наявність визначає САМ ФАКТ присутності рядка в прайсі → дефолт джерела.
        <= 0 → кількості немає → OUT_OF_STOCK.
    У v1 наївне приведення None→0 миттєво робило ВЕСЬ прайс такого постачальника
    «Немає в наявності».
    """
    if qty is None:
        return src.default_availability, src.default_lead_days
    if qty <= 0:
        return Availability.OUT_OF_STOCK, None
    return src.default_availability, src.default_lead_days


# ---------------------------------------------------------------------------
# Рядок прайсу
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceRow:
    sku: str
    name: str
    name_normalized: str
    price: Decimal
    old_price: Decimal | None
    qty: int | None
    currency: str
    category_ext_id: str
    brand_raw: str
    country_raw: str
    mpn: str
    photo_urls: tuple[str, ...]
    sheet: str
    sheet_order: int
    row_number: int
    raw: dict[str, Any]
    #: `True` → артикула в прайсі не було, `sku` згенеровано (`surrogate_sku`).
    sku_is_surrogate: bool = False

    @property
    def is_incomplete(self) -> bool:
        """ЧЕРНЕТКА: немає артикула та/або фото → товар створюємо, але НЕ показуємо.

        Критерій повноти — АРТИКУЛ + ФОТО + ЦІНА (ціну вже гарантував `classify_row`).
        ⚠️ Характеристики в критерій НЕ входять — це окрема, чинна вимога замовника:
           «навіть якщо пусті характеристики, хай карта товару буде пуста з ціною».
        """
        return self.sku_is_surrogate or not self.photo_urls


# ---------------------------------------------------------------------------
# Advisory-lock (SYNC.md §2, крок 0)
# ---------------------------------------------------------------------------


@contextmanager
def advisory_lock(key: str) -> Iterator[bool]:
    """Сесійний advisory-lock PostgreSQL. `False` → лок уже тримає інший прогін.

    ⚠️ Чому НЕ Redis: advisory-lock звільняється АВТОМАТИЧНО при обриві з'єднання
    (OOM-kill воркера не лишає завислого локу), не має проблеми fencing-токена
    (наївний `DEL` міг зняти ЧУЖИЙ лок) і не залежить від Redis (`FLUSHALL` кешу зносив і локи).

    ⚠️ Чому сесійний, а не `xact`: прогін — це кілька транзакцій (SyncRun має пережити
    відкат запису, dry-run робить `ROLLBACK` навмисно). `xact`-лок помер би на першому ж
    коміті. Гарантія «звільниться при обриві» від цього не страждає: сесійні локи
    так само прив'язані до з'єднання.
    """
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", [key])
        acquired = bool(cur.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", [key])


# ---------------------------------------------------------------------------
# Журнал прогону
# ---------------------------------------------------------------------------


class RunLog:
    """Буфер `SyncLogEntry`.

    Пишеться ОДНИМ `bulk_create` наприкінці — і, головне, ПОЗА транзакцією запису:
    інакше dry-run (який навмисно робить ROLLBACK) стер би власний журнал, а саме заради
    журналу dry-run і існує.
    """

    def __init__(self, run: SyncRun) -> None:
        self.run = run
        self.entries: list[SyncLogEntry] = []
        self.counts: dict[str, int] = {}

    def add(
        self,
        level: str,
        action: str,
        *,
        code: str = "",
        sku: str = "",
        sheet: str = "",
        row_number: int | None = None,
        message: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        data = dict(payload or {})
        if code:
            data["code"] = code
            self.counts[code] = self.counts.get(code, 0) + 1
        self.entries.append(
            SyncLogEntry(
                run=self.run,
                level=level,
                action=action,
                sku=sku[:64],
                sheet=sheet[:60],
                row_number=row_number,
                message=message[:4000],
                payload=data,
            )
        )

    def flush(self) -> int:
        if not self.entries:
            return 0
        n = len(self.entries)
        SyncLogEntry.objects.bulk_create(self.entries, batch_size=1000)
        self.entries = []
        return n


class IssueLog:
    """Буфер `DataIssue` — звіт ДЛЯ ЗАМОВНИКА (не для розробника).

    Пишеться так само, як `RunLog`: одним `bulk_create` ПОЗА транзакцією запису, тому
    переживає rollback dry-run. Саме заради цього списку dry-run і запускають:
    «покажи, що я наламав у таблиці, ДО того як воно поїде на сайт».
    """

    def __init__(self, run: SyncRun) -> None:
        self.run = run
        self.issues: list[DataIssue] = []

    def add(
        self,
        kind: str,
        *,
        sheet: str = "",
        row_number: int | None = None,
        sku: str = "",
        name: str = "",
        message: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.issues.append(
            DataIssue(
                run=self.run,
                kind=kind,
                sheet=sheet[:60],
                row_number=row_number,
                sku=sku[:64],
                name=name[:255],
                message=message,
                payload=payload or {},
            )
        )

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for issue in self.issues:
            out[issue.kind] = out.get(issue.kind, 0) + 1
        return out

    def flush(self) -> int:
        if not self.issues:
            return 0
        n = len(self.issues)
        DataIssue.objects.bulk_create(self.issues, batch_size=1000)
        self.issues = []
        return n


# ---------------------------------------------------------------------------
# КРОК 4 — парсинг рядків
# ---------------------------------------------------------------------------


@dataclass
class SheetCounters:
    section: int = 0
    empty: int = 0
    no_sku: int = 0
    invalid: int = 0
    #: Рядки-чернетки, з яких ВСЕ Ж таки буде товар (сурогатний ключ), — підмножина no_sku.
    surrogate: int = 0
    #: Товари без фото (з артикулом або без) — теж ховаються з сайту.
    no_photo: int = 0
    #: Товари без категорії (порожньо або плейсхолдер) — осідають у службовій «__unmapped__».
    #: ⚠️ НЕ плутати з `missing_categories` у `project_offers` — там ID Є, але невідомий.
    no_category: int = 0


def _parse_sheet(
    read: Any, sheet: PriceSheet, rlog: RunLog, counters: SheetCounters, issues: IssueLog
) -> list[PriceRow]:
    rows: list[PriceRow] = []
    for row_number, cells in read.rows:
        raw = read.logical(cells)
        verdict = classify_row(raw)

        # Рядок-секція («Аксесуари до техніки») — пропуск МОВЧКИ, це НЕ помилка і НЕ WARN.
        # Лічильник у звіті потрібен, щоб раптовий стрибок (з 12 до 4000) було видно оком:
        # це означало б, що з'їхала колонка «Артикул», а не що замовник додав 3988 секцій.
        if verdict.kind is RowKind.SECTION:
            counters.section += 1
            continue
        if verdict.reason.value == "empty_row":
            counters.empty += 1
            continue
        if verdict.reason.value == "row_no_sku":
            counters.no_sku += 1

        for warning in verdict.warnings:
            code = warning.split(":", 1)[0]
            rlog.add(
                L.WARN,
                A.INCOMPLETE if code == "ROW_NO_SKU" else A.ROW_INVALID,
                code=code,
                sheet=sheet.tab_name,
                row_number=row_number,
                message=warning,
            )

        if verdict.is_error:
            counters.invalid += 1
            rlog.add(
                L.ERROR,
                A.ROW_INVALID,
                code="ROW_INVALID",
                sheet=sheet.tab_name,
                row_number=row_number,
                message=verdict.detail,
                payload={"raw": {k: str(v)[:80] for k, v in raw.items()}},
            )
            continue

        if not verdict.is_data:
            continue

        sku = norm_sku(raw.get("sku"))
        name = clean_or_empty(raw.get("name"))
        currency = (clean_or_empty(raw.get("currency")) or sheet.currency).upper()
        if currency not in SourceCurrency.values:
            counters.invalid += 1
            rlog.add(
                L.ERROR,
                A.ROW_INVALID,
                code="ROW_INVALID",
                sku=sku,
                sheet=sheet.tab_name,
                row_number=row_number,
                message=f"Невідома валюта {currency!r}",
            )
            continue

        try:
            old_price = parse_decimal(raw.get("old_price"))
        except ValueError:
            old_price = None
        if old_price is not None and old_price <= verdict.price:
            old_price = None  # CheckConstraint prod_old_price_gt_price — друга лінія оборони

        name_normalized = normalize_model_name(name)
        photo_urls = split_photos(raw.get("photo"))

        # --- ЧЕРНЕТКА: артикула немає → сурогатний ключ з нормалізованої назви ---------
        sku_is_surrogate = not sku
        if sku_is_surrogate:
            counters.surrogate += 1
            sku = surrogate_sku(name_normalized, name)
            issues.add(
                I.MISSING_SKU,
                sheet=sheet.tab_name,
                row_number=row_number,
                sku=sku,
                name=name,
                message=(
                    "У колонці «Артикул» стоїть «Уточнюється». Товар створено, але СХОВАНО "
                    "з сайту. Впишіть артикул — і після наступної синхронізації товар "
                    "з'явиться сам (нічого більше робити не треба)."
                ),
                payload={"price": str(verdict.price), "currency": currency},
            )

        if not photo_urls:
            counters.no_photo += 1
            rlog.add(
                L.WARN,
                A.INCOMPLETE,
                code="ROW_NO_PHOTO",
                sku=sku,
                sheet=sheet.tab_name,
                row_number=row_number,
                message=f"Немає фото: {name!r} — товар сховано з сайту",
            )
            issues.add(
                I.MISSING_PHOTO,
                sheet=sheet.tab_name,
                row_number=row_number,
                sku="" if sku_is_surrogate else sku,
                name=name,
                message=(
                    "У колонці «Фото» немає посилання. Товар створено, але СХОВАНО з сайту. "
                    "Вставте посилання на фото — товар з'явиться після наступної синхронізації."
                ),
                payload={"price": str(verdict.price), "currency": currency},
            )

        # --- НЕМАЄ КАТЕГОРІЇ: порожньо або плейсхолдер («Уточнюється») ------------
        # ⚠️ `norm_category_id` уже пропускає сирий текст через `clean_or_empty` (тому й
        # порожня клітинка, і «Уточнюється» дають тут "" ОДНАКОВО) — на відміну від
        # UNKNOWN_CATEGORY (project_offers), де ID Є, але його немає в довіднику. Це РІЗНІ
        # проблеми з РІЗНИМИ діями замовника, тому й види різні.
        category_ext_id = norm_category_id(raw.get("category"))
        if not category_ext_id:
            counters.no_category += 1
            rlog.add(
                L.WARN,
                A.INCOMPLETE,
                code="ROW_NO_CATEGORY",
                sku="" if sku_is_surrogate else sku,
                sheet=sheet.tab_name,
                row_number=row_number,
                message=(
                    f"Немає категорії: {name!r} — товар осяде в службовій «__unmapped__» "
                    f"і не потрапить у каталог"
                ),
            )
            issues.add(
                I.MISSING_CATEGORY,
                sheet=sheet.tab_name,
                row_number=row_number,
                sku="" if sku_is_surrogate else sku,
                name=name,
                message=(
                    "У колонці «Категорія» порожньо. Товар створено, але він НЕ потрапляє "
                    "в каталог (лежить у службовій категорії). Вкажіть ID категорії з листа "
                    "«Категорії» — товар з'явиться після наступної синхронізації."
                ),
                payload={"price": str(verdict.price), "currency": currency},
            )

        rows.append(
            PriceRow(
                sku=sku,
                name=name,
                name_normalized=name_normalized,
                price=verdict.price,  # type: ignore[arg-type]
                old_price=old_price,
                qty=verdict.qty,
                currency=currency,
                category_ext_id=category_ext_id,
                brand_raw=clean_or_empty(raw.get("brand"))[:160],
                country_raw=clean_or_empty(raw.get("country"))[:160],
                mpn=clean_or_empty(raw.get("mpn"))[:64],
                photo_urls=photo_urls,
                sheet=sheet.tab_name,
                sheet_order=sheet.sort_order,
                row_number=row_number,
                raw={k: str(v)[:200] for k, v in raw.items()},
                sku_is_surrogate=sku_is_surrogate,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# КРОК 5 — дедуплікація sku
# ---------------------------------------------------------------------------


def dedupe_rows(rows: Sequence[PriceRow], rlog: RunLog, issues: IssueLog) -> list[PriceRow]:
    """Виграє ПЕРШИЙ: лист з меншим `sort_order`, потім менший номер рядка.

    ⚠️ Два об'єкти з однаковим sku в ОДНОМУ `bulk_create(update_conflicts=True)` дають
    «ON CONFLICT DO UPDATE command cannot affect row a second time» — і валять увесь батч
    разом з транзакцією. Дубль артикула в таблиці, яку редагують люди, — питання тижнів.

    ⚠️ Дедуп РЯТУЄ ПРОГІН, але НЕ ЛІКУЄ ДАНІ: один з двох товарів усе одно не потрапить на
    сайт. Замовник просив прямо:
        «І може таке бути, що я десь провтикав і два товари будуть з одним артикулом.
         Якщо тобі це відобразиться десь, скажи плз, я виправлю»
    Тому кожен дубль — це ще й `DataIssue` з НОМЕРАМИ ОБОХ РЯДКІВ і НАЗВАМИ ОБОХ товарів:
    без них «знайди дубль у таблиці на 800 рядків» — робота на вечір.
    """
    by_sku: dict[str, PriceRow] = {}
    for row in sorted(rows, key=lambda r: (r.sheet_order, r.sheet, r.row_number)):
        prev = by_sku.get(row.sku)
        if prev is not None:
            taken = f"{prev.sheet}:{prev.row_number}"
            dropped = f"{row.sheet}:{row.row_number}"
            rlog.add(
                L.WARN,
                A.DUPLICATE_SKU,
                code="DUPLICATE_SKU",
                sku=row.sku,
                sheet=row.sheet,
                row_number=row.row_number,
                message=f"Дубль артикула: {taken} vs {dropped}. Взято {taken}",
            )
            issues.add(
                I.DUPLICATE_SKU,
                sheet=row.sheet,
                row_number=row.row_number,
                sku=row.sku,
                name=row.name,
                message=(
                    f"Артикул «{row.sku}» стоїть у ДВОХ рядках: "
                    f"лист {prev.sheet}, рядок {prev.row_number} — «{prev.name}»; "
                    f"лист {row.sheet}, рядок {row.row_number} — «{row.name}». "
                    f"На сайт пішов ПЕРШИЙ ({taken}), другий ПРОІГНОРОВАНО. "
                    f"Виправте артикул в одному з рядків."
                ),
                payload={
                    "taken": {
                        "sheet": prev.sheet,
                        "row": prev.row_number,
                        "name": prev.name,
                        "price": str(prev.price),
                    },
                    "dropped": {
                        "sheet": row.sheet,
                        "row": row.row_number,
                        "name": row.name,
                        "price": str(row.price),
                    },
                },
            )
            continue
        by_sku[row.sku] = row
    return list(by_sku.values())


# ---------------------------------------------------------------------------
# КРОК 6 — запобіжники
# ---------------------------------------------------------------------------


def build_guard_report(
    rows: Sequence[PriceRow], src: PriceSource, adoptions: Mapping[str, str] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Рахується на ПРОЧИТАНИХ рядках проти поточного стану — ДО будь-якого запису.

    Guard #1 «мало рядків» ловить обрізаний прайс.
    Guard #2 «пороги масової зміни» ловить те, чого #1 не бачить: замовник **зсунув колонку
    К-сть** (рядків стільки ж → #1 мовчить → 10k товарів стають OUT_OF_STOCK), вставив
    колонку, поміняв десяткову кому.

    ⚠️ `adoptions` (`{старий AUTO-sku: новий артикул}`) — НЕ оптимізація, а виправлення
    хибнопозитиву, який зупиняв би синк рівно тоді, коли замовник робить усе правильно.
    Він заповнює артикули пачками («візьму комп на нічну, спробую за ніч додати») → зранку
    сотні AUTO-артикулів зникають, а натомість з'являються справжні. Для guard #2a це
    виглядало б як масова деактивація половини каталогу → ABORTED. Перейменування — це не
    зникнення: усиновлені sku НЕ рахуються ні як «зниклі», ні як розрив ціни.
    """
    alive = dict(
        SupplierOffer.objects.filter(price_source=src, is_active=True).values_list(
            "sku", "base_price"
        )
    )
    alive_qty = dict(
        SupplierOffer.objects.filter(price_source=src, is_active=True).values_list(
            "sku", "stock_qty"
        )
    )
    alive_count = len(alive)
    by_sku: dict[str, PriceRow] = {r.sku: r for r in rows}
    renamed: Mapping[str, str] = adoptions or {}

    report: dict[str, Any] = {
        "rows": len(rows),
        "alive": alive_count,
        "adopted_skus": len(renamed),
        "min_ratio": float(src.guard_min_rows_ratio),
        "max_deactivate_pct": float(src.guard_max_deactivate_pct),
        "max_median_price_pct": float(src.guard_max_median_price_pct),
        "ratio": None,
        "will_deactivate_pct": 0.0,
        "median_price_delta_pct": 0.0,
        "qty_zeroed_pct": 0.0,
    }
    triggered: list[str] = []

    if not alive_count:
        # Перший прогін джерела: порівнювати нема з чим, і саме тому guard мовчить.
        return report, triggered

    # --- Guard #1: різке падіння кількості рядків -------------------------------
    ratio = len(rows) / alive_count
    report["ratio"] = round(ratio, 4)
    if Decimal(str(ratio)) < src.guard_min_rows_ratio:
        triggered.append("guard_min_rows")

    # --- Guard #2a: масова деактивація ------------------------------------------
    # Усиновлений sku «зник» лише на папері: той самий товар лишається, просто під новим
    # артикулом. Рахувати його як зниклий — це зупиняти прогін за нормальну роботу замовника.
    gone = sum(1 for sku in alive if sku not in by_sku and sku not in renamed)
    zeroed = sum(
        1 for sku, row in by_sku.items() if sku in alive and row.qty is not None and row.qty <= 0
    )
    will_deactivate_pct = (gone + zeroed) * 100.0 / alive_count
    report["will_deactivate_pct"] = round(will_deactivate_pct, 2)
    if Decimal(str(will_deactivate_pct)) > src.guard_max_deactivate_pct:
        triggered.append("guard_max_deactivate")

    # --- Guard #2b: медіанна зміна ціни -----------------------------------------
    # Для усиновлених порівнюємо СТАРУ ціну чернетки з НОВОЮ ціною того самого рядка
    # (шукаємо його вже за новим артикулом), інакше товар просто випав би з вибірки.
    deltas = []
    for sku, base_price in alive.items():
        row = by_sku.get(sku) or by_sku.get(renamed.get(sku, ""))
        if row is not None and base_price > 0:
            deltas.append(abs(row.price - base_price) / base_price * 100)
    if deltas:
        median = float(statistics.median(deltas))
        report["median_price_delta_pct"] = round(median, 2)
        if Decimal(str(median)) > src.guard_max_median_price_pct:
            triggered.append("guard_median_price")

    # --- Guard #2c: обнулена колонка «К-сть» ------------------------------------
    if rows:
        qty_zeroed = sum(
            1
            for sku, row in by_sku.items()
            if row.qty is not None and row.qty <= 0 and (alive_qty.get(sku) or 0) > 0
        )
        pct = qty_zeroed * 100.0 / len(rows)
        report["qty_zeroed_pct"] = round(pct, 2)
        if Decimal(str(pct)) > GUARD_MAX_QTY_ZEROED_PCT:
            triggered.append("guard_qty_zeroed")

    return report, triggered


# ---------------------------------------------------------------------------
# КРОК 7 — сирий знімок
# ---------------------------------------------------------------------------

_SNAPSHOT_COLUMNS = (
    "sheet",
    "row_number",
    "sku",
    "name",
    "price",
    "old_price",
    "qty",
    "currency",
    "category_ext_id",
    "brand_raw",
    "country_raw",
    "mpn",
    "photo_urls",
)


def store_raw_snapshot(run: SyncRun, rows: Sequence[PriceRow]) -> str:
    """`gzip(CSV(rows))` у сховище. Форензика «що було в таблиці о 12:00» + переграти офлайн.

    Збій сховища знімок не робить фатальним: прогін важливіший за форензику.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_SNAPSHOT_COLUMNS)
    for r in rows:
        writer.writerow(
            [
                r.sheet,
                r.row_number,
                r.sku,
                r.name,
                r.price,
                r.old_price if r.old_price is not None else "",
                "" if r.qty is None else r.qty,
                r.currency,
                r.category_ext_id,
                r.brand_raw,
                r.country_raw,
                r.mpn,
                " ".join(r.photo_urls),
            ]
        )
    key = f"sync-snapshots/{run.id}.csv.gz"
    try:
        default_storage.save(key, ContentFile(gzip.compress(buf.getvalue().encode("utf-8"))))
    except Exception as exc:
        log.warning("Не вдалося зберегти сирий знімок %s: %s", key, exc)
        return ""
    return key


# ---------------------------------------------------------------------------
# КРОКИ 10–11 — категорії, бренди, країни
# ---------------------------------------------------------------------------


def get_unmapped_category() -> Category:
    """Службова категорія-смітник. `is_active=False` + `is_service=True`.

    Товар у ній створюється НЕАКТИВНИМ (у v1 він був активним і просочувався у FTS,
    sitemap і на `/p/{id}` з категорією «__unmapped__»).
    """
    cat = Category.objects.filter(external_id=Category.UNMAPPED_EXTERNAL_ID).first()
    if cat is None:
        cat = Category(
            external_id=Category.UNMAPPED_EXTERNAL_ID,
            name="Без категорії (службова)",
            slug="unmapped",
            is_active=False,
            is_service=True,
            show_in_megamenu=False,
            sort_order=9999,
        )
        if hasattr(cat, "name_uk"):
            cat.name_uk = cat.name
            cat.slug_uk = cat.slug
        cat.save()
    return cat


def resolve_brand(raw: str, cache: dict[str, int | None], rlog: RunLog) -> int | None:
    """Пошук по `name` (case-insensitive) і по `aliases`. Не знайдено → створюємо + WARN.

    Злиття дублів — ТІЛЬКИ дією «Злити з…» (пряме видалення в адмінці заборонене:
    `Product.brand` має `on_delete=SET_NULL`, тож видалення дубля мовчки обнулило б бренд
    у сотень товарів).
    """
    if not raw:
        return None
    key = raw.casefold()
    if key in cache:
        return cache[key]

    brand = Brand.objects.filter(name__iexact=raw).first()
    if brand is None:
        brand = Brand.objects.filter(aliases__contains=[raw]).first()
    if brand is None:
        slug = slugify(raw) or f"brand-{abs(hash(key)) % 10**6}"
        base, n = slug, 1
        while Brand.objects.filter(slug=slug).exists():
            n += 1
            slug = f"{base}-{n}"
        brand = Brand.objects.create(name=raw[:120], slug=slug[:140], needs_review=True)
        rlog.add(
            L.WARN,
            A.BRAND_CREATED,
            code="BRAND_CREATED",
            message=f"Створено бренд «{raw}» (needs_review=True) — перевірте дублі",
        )

    cache[key] = brand.pk
    return brand.pk


def resolve_country(raw: str, cache: dict[str, int | None], rlog: RunLog) -> int | None:
    """Країна — ТІЛЬКИ з довідника: `Country.code` — ISO-3166 alpha-2, вигадати його не можна.

    Не знайшли → WARN і `country = NULL`. Свідомо: країна-фантом «Уточнюється» з кодом
    «UN» гірша за порожню (вона потрапила б у фасети й у фід Hotline).
    """
    if not raw:
        return None
    key = raw.casefold()
    if key in cache:
        return cache[key]

    country = _find_by_name(Country.objects.all(), "name", raw)
    if country is None and hasattr(Country, "name_ru"):
        country = Country.objects.filter(name_ru__iexact=raw).first()
    if country is None:
        rlog.add(
            L.WARN,
            A.SKIPPED,
            code="COUNTRY_MISSING",
            message=f"Країни «{raw}» немає в довіднику — товар лишиться без країни",
        )
        cache[key] = None
        return None

    cache[key] = country.pk
    return country.pk


# ---------------------------------------------------------------------------
# КРОК 8a-bis — УСИНОВЛЕННЯ СУРОГАТІВ (SYNC.md §4.3)
# ---------------------------------------------------------------------------


def plan_surrogate_adoptions(rows: Sequence[PriceRow], rlog: RunLog) -> dict[str, str]:
    """`{"AUTO-3F2A…": "2400123"}` — чернетки, яким замовник ЩОЙНО вписав артикул.

    ЧИСТО ЧИТАЛЬНИЙ крок, і рахується він ДО запобіжників — навмисно. Без цього
    усиновлення виглядає для guard #2a як МАСОВА ДЕАКТИВАЦІЯ: старий AUTO-артикул зник,
    новий з'явився. Замовник обіцяв заповнити артикули «за ніч» — тобто наступний ранковий
    прогін мав би сотні «зниклих» sku і ABORTED-прогін на рівному місці.
    Перейменування — це НЕ зникнення, і guard має знати про це заздалегідь.

    Ключ зіставлення — `name_normalized` (той самий, яким зшиваються прайс і характеристики).
    """
    real_by_norm: dict[str, str] = {}
    ambiguous: set[str] = set()
    for row in rows:
        if row.sku_is_surrogate or not row.name_normalized:
            continue
        prev = real_by_norm.get(row.name_normalized)
        if prev is not None and prev != row.sku:
            # Дві РІЗНІ назви нормалізувались в одне → вгадувати, кого усиновити, не можна.
            ambiguous.add(row.name_normalized)
        real_by_norm.setdefault(row.name_normalized, row.sku)
    for key in ambiguous:
        real_by_norm.pop(key, None)

    if not real_by_norm:
        return {}

    plan: dict[str, str] = {}
    candidates = Product.objects.filter(
        sku_is_surrogate=True, name_normalized__in=list(real_by_norm)
    ).only("id", "sku", "name", "name_normalized")

    for product in candidates:
        new_sku = real_by_norm.get(product.name_normalized)
        if not new_sku or new_sku == product.sku:
            continue

        # Артикул уже зайнятий ІНШИМ товаром (замовник помилився) → мовчки склеювати не
        # можна: це втрата даних. Кажемо голосно, чернетка лишається схованою.
        if Product.objects.filter(sku=new_sku).exclude(pk=product.pk).exists():
            rlog.add(
                L.WARN,
                A.DUPLICATE_SKU,
                code="SURROGATE_CONFLICT",
                sku=new_sku,
                message=(
                    f"Рядку «{product.name}» вписали артикул {new_sku}, але такий артикул уже "
                    f"має ІНШИЙ товар. Чернетка лишилась схованою — перевірте артикул."
                ),
            )
            continue

        plan[product.sku] = new_sku
    return plan


def adopt_surrogate_skus(plan: Mapping[str, str], src: PriceSource, rlog: RunLog) -> list[int]:
    """Застосувати план `plan_surrogate_adoptions()`: перейменувати sku ТОГО САМОГО товару.

    ⚠️ ЦЕ НАЙВАЖЛИВІШИЙ КРОК УСІЄЇ §4.3, і без нього сурогатні ключі — не рішення, а бомба:
        прогін 1: рядок без артикула  → Product(sku="AUTO-3F2A…", is_incomplete=True, схований)
        замовник вписує артикул 2400123 і фото
        прогін 2: рядок з артикулом   → Product(sku="2400123") — НОВИЙ, а старий AUTO-товар
                  лишається сиротою і деактивується. У каталозі ДВА товари замість одного,
                  у старого — вся історія цін, у нового — жодної.

    Разом з товаром переїжджає й оффер цього ж джерела: (price_source, sku) — ключ upsert-а,
    і якщо його не перейменувати, у пісочниці лишиться мертвий AUTO-рядок.

    Повертає id усиновлених товарів: у них ЩОЙНО з'явився справжній артикул, тому саме зараз
    їх варто зіставити з таблицею характеристик (раніше це було можливо лише по назві).
    """
    if not plan:
        return []

    adopted: list[int] = []
    products = {
        p.sku: p for p in Product.objects.filter(sku__in=list(plan)).only("id", "sku", "name")
    }

    for old_sku, new_sku in plan.items():
        product = products.get(old_sku)
        if product is None:
            continue

        if not SupplierOffer.objects.filter(price_source=src, sku=new_sku).exists():
            SupplierOffer.objects.filter(price_source=src, sku=old_sku).update(
                sku=new_sku, sku_is_surrogate=False, updated_at=timezone.now()
            )
        Product.objects.filter(pk=product.pk).update(
            sku=new_sku,
            sku_is_surrogate=False,
            denorm_dirty=True,
            updated_at=timezone.now(),
        )
        adopted.append(product.pk)
        rlog.add(
            L.INFO,
            A.SKU_ADOPTED,
            code="SKU_ADOPTED",
            sku=new_sku,
            message=(
                f"Артикул з'явився: {old_sku} → {new_sku} («{product.name}»). "
                f"Оновлено ТОЙ САМИЙ товар, дубль не створено."
            ),
        )

    return adopted


# ---------------------------------------------------------------------------
# КРОК 8b — upsert офферів
# ---------------------------------------------------------------------------


def _offer_from_row(row: PriceRow, src: PriceSource, run_id: uuid.UUID) -> SupplierOffer:
    availability, lead_days = resolve_availability(row.qty, src)
    return SupplierOffer(
        price_source=src,
        sku=row.sku,
        sku_is_surrogate=row.sku_is_surrogate,
        name=row.name[:255],
        base_price=row.price,
        base_old_price=row.old_price,
        source_currency=row.currency,
        stock_qty=row.qty,
        availability=availability,
        order_lead_days=lead_days,
        category_ext_id=row.category_ext_id[:32],
        brand_raw=row.brand_raw,
        country_raw=row.country_raw,
        mpn=row.mpn,
        photo_urls=list(row.photo_urls),
        raw=row.raw,
        is_active=True,
        last_seen_run=run_id,
        sheet=row.sheet,
        row_number=row.row_number,
    )


def upsert_offers(rows: Sequence[PriceRow], src: PriceSource, run: SyncRun, rlog: RunLog) -> int:
    """`bulk_create(update_conflicts=True)` батчами, кожен — у власному savepoint.

    Батч усе одно впав на IntegrityError → фолбек на ПОРЯДКОВУ вставку: один аномальний
    рядок не забирає з собою 499 нормальних. Без savepoint будь-який `IntegrityError`
    отруює всю транзакцію прогону («current transaction is aborted»).
    """
    failed = 0
    offers = [_offer_from_row(r, src, run.id) for r in rows]

    for i in range(0, len(offers), BATCH_SIZE):
        batch = offers[i : i + BATCH_SIZE]
        try:
            with transaction.atomic(savepoint=True):
                SupplierOffer.objects.bulk_create(
                    batch,
                    update_conflicts=True,
                    unique_fields=["price_source", "sku"],
                    update_fields=OFFER_FIELDS,
                    batch_size=BATCH_SIZE,
                )
        except IntegrityError:
            for offer in batch:
                try:
                    with transaction.atomic(savepoint=True):
                        SupplierOffer.objects.update_or_create(
                            price_source=src,
                            sku=offer.sku,
                            defaults={
                                f: getattr(offer, f) for f in OFFER_FIELDS if f != "updated_at"
                            },
                        )
                except IntegrityError as exc:
                    failed += 1
                    rlog.add(
                        L.ERROR,
                        A.ROW_INVALID,
                        code="ROW_INVALID",
                        sku=offer.sku,
                        sheet=offer.sheet,
                        row_number=offer.row_number,
                        message=str(exc)[:500],
                    )
    return failed


# ---------------------------------------------------------------------------
# КРОК 8d — ПРОЄКЦІЯ (SYNC.md §3)
# ---------------------------------------------------------------------------

# ⚠️ У ЗАПИСУВАЛЬНИХ SQL — `clock_timestamp()`, а НЕ `now()`. `now()` = час СТАРТУ ТРАНЗАКЦІЇ:
#    усі UPDATE-и одного прогону отримали б ОДНАКОВИЙ updated_at, а поруч Django пише
#    `auto_now` / `timezone.now()` реальним годинником — і `updated_at` стає НЕМОНОТОННИМ
#    (наступний запис у тій самій транзакції може виявитись «раніше» за попередній).
#    Саме на цьому ламається все, що читає світ по `updated_at__gte`: sitemap lastmod,
#    ISR-ревалідація, інкрементальний експорт. `clock_timestamp()` — справжній час запису.
#
# ⚠️ DROP IF EXISTS — обов'язковий, а не «про всяк випадок». `ON COMMIT DROP` спрацьовує лише
# на КОМІТІ, а проєкція цілком може виконатись двічі в одній транзакції: у тестах
# (pytest-django тримає весь тест в одній транзакції) і в будь-якому виклику, який синкає два
# джерела під спільним atomic. Без цього другий виклик падає з «relation winner already exists».
_DROP_TEMP_SQL = "DROP TABLE IF EXISTS winner; DROP TABLE IF EXISTS winner_meta;"

_WINNER_SQL = """
CREATE TEMP TABLE winner ON COMMIT DROP AS
SELECT DISTINCT ON (o.sku)
       o.id AS offer_id, o.sku, o.name, o.base_price, o.base_old_price, o.source_currency,
       o.stock_qty, o.availability, o.order_lead_days, o.category_ext_id,
       o.brand_raw, o.country_raw, o.mpn,
       o.sku_is_surrogate,
       (o.sku_is_surrogate OR cardinality(o.photo_urls) = 0) AS is_incomplete,
       ps.id AS price_source_id, ps.price_markup_percent AS markup, ps.overwrite_names
FROM   sync_supplieroffer o
JOIN   sync_pricesource  ps ON ps.id = o.price_source_id
WHERE  o.is_active AND ps.is_active AND o.sku = ANY(%(skus)s)
ORDER BY o.sku, ps.priority ASC, ps.is_primary DESC, o.id ASC;
"""
# ↑ `is_incomplete` — КРИТЕРІЙ ВИДИМОСТІ (SYNC.md §4.3): немає артикула АБО немає фото.
#   Рахується тут, у SQL, а не в Python, бо проєкція бачить ПЕРЕМОЖЦЯ серед джерел:
#   якщо в Complex рядок-чернетка, а в Alpha той самий sku з фото — виграє повний рядок.
# ↑ МЕНШИЙ priority ВИГРАЄ. Саме тут (а не в ON CONFLICT) вирішується, чий прайс головний:
#   у v1 вигравав той, хто синкнувся ОСТАННІМ, і прайс постачальника з націнкою 15%
#   і «під замовлення 14 днів» тихо перезаписував складський товар Complex.

# ⚠️ `category_id` резолвиться в PYTHON і кладеться сюди, а не джоїниться по external_id.
#    Причина — БАГ, який це замінило: `JOIN catalog_category ON external_id = COALESCE(
#    NULLIF(w.category_ext_id,''), '__unmapped__')` знаходить «__unmapped__» ЛИШЕ для
#    ПОРОЖНЬОЇ категорії. Для НЕВІДОМОГО, але непорожнього ID («50549829») join не знаходив
#    нічого — і рядок ТИХО зникав з INSERT. Тобто товар з невідомою категорією не потрапляв
#    у каталог зовсім, замість того щоб осісти в «__unmapped__» і чекати на категорію.
_META_SQL = """
CREATE TEMP TABLE winner_meta (
    sku text PRIMARY KEY, slug text, name_norm text,
    brand_id bigint, country_id bigint, category_id bigint
) ON COMMIT DROP;
"""

# 1) НОВІ ТОВАРИ. ON CONFLICT DO NOTHING — оновлення робить блок 4, а не цей.
_INSERT_SQL = """
INSERT INTO catalog_product (
    sku, sku_is_surrogate, is_incomplete, mpn, barcode,
    name, name_uk, slug, slug_uk, description, description_uk,
    short_description, short_description_uk, name_normalized,
    seo_title, seo_title_uk, seo_description, seo_description_uk, og_image,
    category_id, brand_id, country_id, source_category_ext_id,
    source_currency, base_price, markup_percent, price, base_old_price, old_price,
    usd_rate_used, price_updated_at, price_locked, availability_locked,
    availability, order_lead_days, stock_qty,
    package_dims_source, is_active, installment_available, hotline_enabled, is_featured,
    condition, condition_note, condition_note_uk,
    source, price_source_id, winning_offer_id, last_seen_run, synced_at,
    specs_json, specs_json_uk, filter_tokens, specs_num, main_image_url,
    denorm_dirty, created_at, updated_at
)
SELECT w.sku, w.sku_is_surrogate, w.is_incomplete, w.mpn, '',
       w.name, w.name, m.slug, m.slug, '', '',
       '', '', m.name_norm,
       '', '', '', '', '',
       c.id, m.brand_id, m.country_id, w.category_ext_id,
       w.source_currency, w.base_price, w.markup,
       complex_price_uah(w.base_price, w.source_currency, %(rate)s, w.markup, %(rule)s),
       w.base_old_price,
       CASE WHEN complex_price_uah(w.base_old_price, w.source_currency, %(rate)s, w.markup, %(rule)s)
                 > complex_price_uah(w.base_price, w.source_currency, %(rate)s, w.markup, %(rule)s)
            THEN complex_price_uah(w.base_old_price, w.source_currency, %(rate)s, w.markup, %(rule)s)
       END,
       CASE WHEN w.source_currency = 'USD' THEN %(rate)s END, clock_timestamp(), false, false,
       w.availability, w.order_lead_days, w.stock_qty,
       '', (NOT c.is_service AND NOT w.is_incomplete), false, c.hotline_enabled_default, false,
       0, '', '',
       'sheet', w.price_source_id, w.offer_id, %(run_id)s, clock_timestamp(),
       '[]'::jsonb, '[]'::jsonb, ARRAY[]::varchar(80)[], '{}'::jsonb, '',
       true, clock_timestamp(), clock_timestamp()
FROM winner w
JOIN winner_meta m ON m.sku = w.sku
JOIN catalog_category c ON c.id = m.category_id
WHERE complex_price_uah(w.base_price, w.source_currency, %(rate)s, w.markup, %(rule)s) IS NOT NULL
ON CONFLICT (sku) DO NOTHING
RETURNING id;
"""
# ↑ `(NOT c.is_service AND NOT w.is_incomplete)` — товар створюється НЕАКТИВНИМ, якщо він
#   або в «__unmapped__», або НЕПОВНИЙ (немає артикула та/або фото). Слова замовника:
#   «у них нема ні фото ні артикула, нічого крім ціни. Роби одразу, щоб товар не відображався».
#   Дані при цьому НЕ втрачаються: ціна, назва, категорія збережені — товар просто схований,
#   доки замовник не дозаповнить рядок (тоді блок 4b увімкне його САМ).
#   `WHERE complex_price_uah(...) IS NOT NULL` — USD без валідного курсу дав би NULL у NOT NULL
#   колонку `price`. Крок 3.6 це вже перевірив; тут — друга лінія оборони.

# 2) ЦІНА — тільки для НЕзаблокованих. Це ЄДИНЕ, що робить price_locked.
_UPDATE_PRICE_SQL = """
UPDATE catalog_product p SET
    base_price      = w.base_price,
    base_old_price  = w.base_old_price,
    source_currency = w.source_currency,
    markup_percent  = w.markup,
    price     = complex_price_uah(w.base_price, w.source_currency, %(rate)s, w.markup, %(rule)s),
    old_price = CASE
        WHEN complex_price_uah(w.base_old_price, w.source_currency, %(rate)s, w.markup, %(rule)s)
             > complex_price_uah(w.base_price, w.source_currency, %(rate)s, w.markup, %(rule)s)
        THEN complex_price_uah(w.base_old_price, w.source_currency, %(rate)s, w.markup, %(rule)s)
    END,
    usd_rate_used    = CASE WHEN w.source_currency = 'USD' THEN %(rate)s END,
    price_updated_at = clock_timestamp(),
    updated_at       = clock_timestamp()
FROM winner w
WHERE p.sku = w.sku AND NOT p.price_locked
  AND complex_price_uah(w.base_price, w.source_currency, %(rate)s, w.markup, %(rule)s) IS NOT NULL;
"""

# 3) НАЯВНІСТЬ — тільки для НЕзаблокованих (MAJOR FIX: у v1 ручне «під замовлення» затиралось).
_UPDATE_AVAIL_SQL = """
UPDATE catalog_product p SET
    availability    = w.availability,
    order_lead_days = w.order_lead_days,
    stock_qty       = w.stock_qty,
    updated_at      = clock_timestamp()
FROM winner w
WHERE p.sku = w.sku AND NOT p.availability_locked;
"""

# 3b) ЧЕРНЕТКА → СХОВАТИ. Рядок неповний (немає артикула та/або фото) → товар не показуємо.
#     ⚠️ Виняток — РУЧНЕ фото: якщо контент-менеджер сам додав картинку, товар уже не «пустий»,
#        і гасити його щосинку означало б воювати з людиною. Фото з прайсу (source='sheet')
#        таким винятком НЕ є — воно й так лежить у photo_urls оффера.
_HIDE_INCOMPLETE_SQL = """
UPDATE catalog_product p SET
    is_incomplete    = true,
    sku_is_surrogate = w.sku_is_surrogate,
    is_active        = false,
    denorm_dirty     = true,
    updated_at       = clock_timestamp()
FROM winner w
WHERE p.sku = w.sku
  AND w.is_incomplete
  AND NOT EXISTS (
      SELECT 1 FROM catalog_productimage i
      WHERE i.product_id = p.id AND i.source = 'manual'
  )
  AND (NOT p.is_incomplete OR p.is_active OR p.sku_is_surrogate <> w.sku_is_surrogate)
RETURNING p.id;
"""

# 4a) ДОЗАПОВНИЛИ → ПОКАЗАТИ САМІ. Артикул І фото з'явились → товар стає видимим БЕЗ жодної
#     ручної дії. Це прямий запит замовника: він заповнює таблицю вночі й хоче, щоб товари
#     з'явились на сайті самі.
#     ⚠️ Вмикаємо ТІЛЬКИ те, що ми ж і ховали (`p.is_incomplete`). Товар, вимкнений РУКАМИ в
#        адмінці, синк не воскрешає — інакше «сховав товар» дорівнювало б «сховав до
#        наступного синку».
#     ⚠️ Товар у «__unmapped__» лишається неактивним (категорії все ще немає), але прапорець
#        неповноти знімаємо — дані ж повні. Увімкне його блок 10b, коли з'явиться категорія.
_COMPLETE_SQL = """
UPDATE catalog_product p SET
    is_incomplete    = false,
    sku_is_surrogate = false,
    is_active        = CASE WHEN c.is_service THEN p.is_active ELSE true END,
    denorm_dirty     = true,
    updated_at       = clock_timestamp()
FROM winner w
JOIN winner_meta m ON m.sku = w.sku
JOIN catalog_category c ON c.id = m.category_id
WHERE p.sku = w.sku
  AND NOT w.is_incomplete
  AND p.is_incomplete
RETURNING p.id, (NOT c.is_service) AS activated;
"""

# 4) СЛУЖБОВІ — ЗАВЖДИ, включно з price_locked-товарами.
#    ⚠️ CRITICAL: саме тут виставляється last_seen_run. У v1 price_locked-товар його не
#    отримував → деактивувався на ПЕРШОМУ ж прогоні, тобто «зафіксував ціну» = «зник з продажу».
_UPDATE_SERVICE_SQL = """
UPDATE catalog_product p SET
    price_source_id        = w.price_source_id,
    winning_offer_id       = w.offer_id,
    source_category_ext_id = w.category_ext_id,
    last_seen_run          = %(run_id)s,
    synced_at              = clock_timestamp(),
    denorm_dirty           = true,
    updated_at             = clock_timestamp(),
    name    = CASE WHEN w.overwrite_names THEN w.name ELSE p.name END,
    name_uk = CASE WHEN w.overwrite_names THEN w.name ELSE p.name_uk END,
    name_normalized = CASE WHEN w.overwrite_names THEN m.name_norm ELSE p.name_normalized END
FROM winner w
JOIN winner_meta m ON m.sku = w.sku
WHERE p.sku = w.sku;
"""

# 4b) РОЗБІЖНІСТЬ НАЗВ — тільки ЗВІТ, жодного запису (SYNC.md §0: назва — власність адмінки).
#     Рахується ДО блоку 4, поки `p.name` ще старий. Показуємо лише там, де синк САМ вирішив
#     не чіпати назву (`NOT ps.overwrite_names`) — якщо перезапис увімкнено, розбіжності не
#     виникає взагалі, бо блок 4 назву й перезапише.
_NAME_DRIFT_SQL = """
SELECT p.sku, p.name, w.name
FROM   catalog_product p
JOIN   winner w ON w.sku = p.sku
WHERE  NOT w.overwrite_names
  AND  w.name <> ''
  AND  p.name <> w.name;
"""

# 5) ДЕАКТИВАЦІЯ товарів без ЖОДНОГО активного оффера.
#    ⚠️ Дивимось на ВСІ оффери, а не на price_source товару: інакше прогін Alpha деактивував би
#    товари Complex. `price_source_id IS NOT NULL` — щоб не зачепити товари, які взагалі ніколи не
#    приходили з прайсу (легасі-імпорт, ручні з source='sheet').
_DEACTIVATE_SQL = """
UPDATE catalog_product p SET
    availability = 'out_of_stock', stock_qty = 0, synced_at = clock_timestamp(), updated_at = clock_timestamp()
WHERE p.source = 'sheet'
  AND p.price_source_id IS NOT NULL
  AND p.availability IN ('in_stock', 'on_order')
  AND NOT p.availability_locked
  AND NOT EXISTS (
      SELECT 1 FROM sync_supplieroffer o
      JOIN sync_pricesource ps ON ps.id = o.price_source_id
      WHERE o.sku = p.sku AND o.is_active AND ps.is_active
  );
"""

# КРОК 10b — автопідбір товарів з __unmapped__ (у v1 шляху назад не було ЗОВСІМ).
# ⚠️ `NOT p.is_incomplete` — інакше поява категорії ВОСКРЕСИЛА Б ЧЕРНЕТКУ: товар без артикула
#    й фото став би видимим лише тому, що замовник імпортував довідник категорій.
_REMAP_SQL = """
UPDATE catalog_product p SET
    category_id  = c.id,
    is_active    = true,
    denorm_dirty = true,
    updated_at   = clock_timestamp()
FROM catalog_category c, catalog_category u
WHERE u.external_id = %(unmapped)s
  AND p.category_id = u.id
  AND c.external_id = p.source_category_ext_id
  AND c.id <> u.id
  AND p.source_category_ext_id <> ''
  AND NOT p.is_incomplete
RETURNING p.id, p.sku, c.external_id;
"""


@dataclass
class ProjectionStats:
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    remapped: int = 0
    #: Сховано як неповні (немає артикула та/або фото).
    hidden_incomplete: int = 0
    #: Дозаповнили в прайсі → товар УВІМКНУВСЯ сам.
    auto_activated: int = 0
    new_ids: list[int] = field(default_factory=list)


def project_offers(
    skus: Sequence[str],
    run: SyncRun,
    rate: Decimal,
    rule: str,
    rlog: RunLog,
    issues: IssueLog | None = None,
) -> ProjectionStats:
    """SupplierOffer → Product. Сім окремих UPDATE, кожен зі своїм WHERE-замком.

    Саме розділення на блоки робить per-row локи тривіальними:
    `price_locked` — це `WHERE NOT p.price_locked` у блоці 2, і більше ніде;
    `is_incomplete` — це блоки 3b/4a, і більше ніде.
    """
    stats = ProjectionStats()
    if not skus:
        return stats

    unmapped = get_unmapped_category()
    params = {
        "skus": list(skus),
        "rate": rate,
        "rule": rule,
        "run_id": str(run.id),
        "unmapped": Category.UNMAPPED_EXTERNAL_ID,
    }

    with connection.cursor() as cur:
        cur.execute(_DROP_TEMP_SQL)
        cur.execute(_WINNER_SQL, {"skus": list(skus)})
        cur.execute(_META_SQL)

        # --- бренди / країни / slug / name_normalized — резолвимо в Python -------
        cur.execute("SELECT sku, name, brand_raw, country_raw, category_ext_id FROM winner")
        winners = cur.fetchall()

        brand_cache: dict[str, int | None] = {}
        country_cache: dict[str, int | None] = {}
        meta_rows: list[tuple[str, str, str, int | None, int | None, int]] = []

        # 59 категорій — один SELECT, а не запит на товар.
        cat_by_ext: dict[str, int] = dict(
            Category.objects.exclude(external_id=Category.UNMAPPED_EXTERNAL_ID).values_list(
                "external_id", "id"
            )
        )
        missing_categories: set[str] = set()

        for sku, name, brand_raw, country_raw, cat_ext in winners:
            category_id = cat_by_ext.get(cat_ext, unmapped.pk) if cat_ext else unmapped.pk
            if cat_ext and cat_ext not in cat_by_ext:
                missing_categories.add(cat_ext)
            meta_rows.append(
                (
                    sku,
                    _product_slug(name, sku),
                    normalize_model_name(name),
                    resolve_brand(brand_raw, brand_cache, rlog),
                    resolve_country(country_raw, country_cache, rlog),
                    category_id,
                )
            )

        affected_by_cat: dict[str, int] = {}
        for _sku, _name, _b, _c, cat_ext in winners:
            if cat_ext in missing_categories:
                affected_by_cat[cat_ext] = affected_by_cat.get(cat_ext, 0) + 1

        for ext_id in sorted(missing_categories):
            rlog.add(
                L.WARN,
                A.CATEGORY_MISSING,
                code="CATEGORY_MISSING",
                message=(
                    f"Категорії «{ext_id}» немає в дереві → товари осіли в «__unmapped__» "
                    f"(неактивні). Створіть категорію — вони переїдуть автоматично."
                ),
                payload={"external_id": ext_id},
            )
            if issues is not None:
                issues.add(
                    I.UNKNOWN_CATEGORY,
                    sku="",
                    name="",
                    message=(
                        f"ID категорії «c{ext_id}» є в прайсі, але його немає в довіднику "
                        f"категорій (лист «Категорії»). Товарів із цим ID: "
                        f"{affected_by_cat.get(ext_id, 0)} — вони СХОВАНІ з сайту, поки категорії "
                        f"немає. Додайте рядок у лист «Категорії» і запустіть sync_categories."
                    ),
                    payload={
                        "external_id": ext_id,
                        "products_affected": affected_by_cat.get(ext_id, 0),
                    },
                )
        if missing_categories:
            run.categories_missing = sorted(set(run.categories_missing) | missing_categories)

        cur.executemany(
            "INSERT INTO winner_meta (sku, slug, name_norm, brand_id, country_id, category_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            meta_rows,
        )

        # --- 1. нові товари -----------------------------------------------------
        cur.execute(_INSERT_SQL, params)
        stats.new_ids = [r[0] for r in cur.fetchall()]
        stats.created = len(stats.new_ids)

        # --- 2/3. ціна, наявність -----------------------------------------------
        cur.execute(_UPDATE_PRICE_SQL, params)
        cur.execute(_UPDATE_AVAIL_SQL, params)

        # --- 3b/4a. ПОВНОТА: сховати чернетки / увімкнути дозаповнені -------------
        cur.execute(_HIDE_INCOMPLETE_SQL)
        stats.hidden_incomplete = len(cur.fetchall())

        cur.execute(_COMPLETE_SQL)
        completed = cur.fetchall()
        stats.auto_activated = sum(1 for _pid, activated in completed if activated)
        for _pid, activated in completed:
            if activated:
                rlog.add(
                    L.INFO,
                    A.UPDATED,
                    code="AUTO_ACTIVATED",
                    message="Дані дозаповнено (артикул + фото) → товар з'явився на сайті",
                )

        # --- 4. службові --------------------------------------------------------
        # ⚠️ ЗВІТ ПРО РОЗБІЖНІСТЬ НАЗВ — ДО апдейта, поки в БД ще стара назва.
        #    Назву синк свідомо НЕ затирає (див. DataIssue.Kind.NAME_CHANGED), але тепер
        #    хоча б КАЖЕ, що прайс і сайт розійшлись.
        if issues is not None:
            cur.execute(_NAME_DRIFT_SQL)
            for sku, old_name, new_name in cur.fetchall():
                issues.add(
                    I.NAME_CHANGED,
                    sku=sku,
                    name=new_name,
                    message=(
                        f"У прайсі назву змінено на «{new_name}», але на сайті лишається "
                        f"«{old_name}». Назва товару — власність адмінки, синк її не затирає "
                        f"(щоб не з'їдати ручні правки). Якщо потрібна назва з прайсу — "
                        f"перейменуйте товар в адмінці або увімкніть «Дозволити перезапис "
                        f"назв товарів» у джерелі прайсу."
                    ),
                    payload={"name_in_db": old_name, "name_in_sheet": new_name},
                )

        cur.execute(_UPDATE_SERVICE_SQL, params)
        stats.updated = max(cur.rowcount - stats.created, 0)

        # --- 5. деактивація зниклих --------------------------------------------
        cur.execute(_DEACTIVATE_SQL)
        stats.deactivated = cur.rowcount

        # --- 10b. автопідбір категорії ------------------------------------------
        cur.execute(_REMAP_SQL, {"unmapped": Category.UNMAPPED_EXTERNAL_ID})
        remapped = cur.fetchall()
        stats.remapped = len(remapped)
        for _pid, sku, ext_id in remapped:
            rlog.add(
                L.INFO,
                A.CATEGORY_REMAPPED,
                code="CATEGORY_REMAPPED",
                sku=sku,
                message=f"Категорію «{ext_id}» знайдено — товар переїхав з «__unmapped__»",
            )

    return stats


def _product_slug(name: str, sku: str) -> str:
    """Слаг декоративний (роут іде по id), але він NOT NULL і має бути латиницею.

    `slugify` з'їдає кирилицю повністю → для українських назв лишається порожньо.
    Тому: нормалізована модель (`gorenjenrk6202axl4`) → інакше `p-{sku}`.
    """
    base = slugify(name)
    if not base:
        base = slugify(normalize_model_name(name))
    if not base:
        base = f"p-{slugify(sku) or 'item'}"
    return base[:280]


# ---------------------------------------------------------------------------
# КРОК 8a — знімок для відкату
# ---------------------------------------------------------------------------

_SNAPSHOT_SQL = """
INSERT INTO sync_productpricesnapshot (
    run_id, product_id, old_base_price, old_price, old_old_price, old_source_currency,
    old_markup_percent, old_usd_rate_used, old_availability, old_order_lead_days,
    old_stock_qty, old_synced_at
)
SELECT %(run_id)s, p.id, p.base_price, p.price, p.old_price, p.source_currency,
       p.markup_percent, p.usd_rate_used, p.availability, p.order_lead_days,
       p.stock_qty, p.synced_at
FROM catalog_product p
WHERE p.sku = ANY(%(skus)s)
   OR (p.source = 'sheet' AND p.price_source_id IS NOT NULL
       AND p.availability IN ('in_stock', 'on_order'));
"""


def take_snapshot(run: SyncRun, skus: Sequence[str]) -> int:
    """COPY поточних цін і наявності ПЕРЕД проєкцією — єдина основа для відкату.

    Знімаємо не тільки товари з прайсу, а й усі живі «sheet»-товари: класика
    «зсунули колонку К-сть» ламає саме тих, кого в новому прайсі немає.
    """
    with connection.cursor() as cur:
        cur.execute(_SNAPSHOT_SQL, {"run_id": str(run.id), "skus": list(skus)})
        return cur.rowcount


# ---------------------------------------------------------------------------
# ГОЛОВНА
# ---------------------------------------------------------------------------


def run_price_sync(
    source: PriceSource,
    *,
    trigger: str = SyncRun.Trigger.CRON,
    dry_run: bool = False,
    force: bool = False,
    user_id: int | None = None,
    client: Any = None,
    fill_specs: bool = True,
) -> SyncRun:
    """Повний прогін прайсу одного джерела. Повертає завершений `SyncRun`.

    Ніколи не кидає виняток назовні: будь-який збій — це `SyncRun` зі статусом
    FAILED/ABORTED і заповненим `traceback`. Викликач (cron, кнопка в адмінці,
    manage.py) дивиться на статус.
    """
    lock_key = f"sync:prices:{source.code}"

    with advisory_lock(lock_key) as acquired:
        if not acquired:
            # Дві beat-репліки або cron + кнопка в адмінці одночасно.
            return SyncRun.objects.create(
                kind=SyncRun.Kind.SHEETS_PRICES,
                price_source=source,
                trigger=trigger,
                status=SyncRun.Status.SKIPPED,
                dry_run=dry_run,
                forced=force,
                started_by_id=user_id,
                finished_at=timezone.now(),
                traceback="Інший прогін цього джерела вже виконується (advisory-lock).",
            )

        run = SyncRun.objects.create(
            kind=SyncRun.Kind.SHEETS_PRICES,
            price_source=source,
            trigger=trigger,
            dry_run=dry_run,
            forced=force,
            started_by_id=user_id,
        )
        rlog = RunLog(run)
        issues = IssueLog(run)
        started = timezone.now()

        try:
            _run_price_sync_inner(source, run, rlog, issues, dry_run, force, client, fill_specs)
        except GuardAborted as exc:
            run.status = SyncRun.Status.ABORTED
            run.guard_report = exc.report
            run.traceback = (
                "Запобіжник зупинив прогін. Каталог НЕ змінено.\n"
                f"{exc.report}\n"
                "Якщо зміни справді очікувані — «Застосувати попри попередження» (force=True)."
            )
            log.error("SyncRun %s ABORTED: %s", run.id, exc.report)
        except (SheetsError, SyncError) as exc:
            run.status = SyncRun.Status.FAILED
            run.traceback = f"{type(exc).__name__}: {exc}"
            log.error("SyncRun %s FAILED: %s", run.id, exc)
        except Exception as exc:
            import traceback as tb

            run.status = SyncRun.Status.FAILED
            run.traceback = tb.format_exc()[:8000]
            log.exception("SyncRun %s FAILED: %s", run.id, exc)
        else:
            run.status = SyncRun.Status.PARTIAL if run.rows_failed else SyncRun.Status.SUCCESS

        run.finished_at = timezone.now()
        run.duration_ms = int((run.finished_at - started).total_seconds() * 1000)
        # ⚠️ `issues` — ЗВІТ ДЛЯ ЗАМОВНИКА, і він мусить пережити dry-run (саме заради нього
        #    dry-run і запускають) та ABORT запобіжника (там теж треба знати, ЩО не так).
        run.stats = run.stats | {"log_codes": rlog.counts, "issues": issues.counts}
        run.save()
        rlog.flush()
        issues.flush()
        return run


def _run_price_sync_inner(
    source: PriceSource,
    run: SyncRun,
    rlog: RunLog,
    issues: IssueLog,
    dry_run: bool,
    force: bool,
    client: Any,
    fill_specs: bool,
) -> None:
    # --- КРОК 3.5. fail-fast конфігу джерела (ДО читання Sheets) -----------------
    if source.default_availability == Availability.ON_ORDER and source.default_lead_days is None:
        raise SyncError(
            "Джерело налаштоване як «Під замовлення», але не вказано default_lead_days. "
            "Кожен INSERT впав би на CheckConstraint prod_on_order_needs_lead_days."
        )

    sheets = list(source.sheets.filter(is_active=True).order_by("sort_order", "id"))
    if not sheets:
        raise SyncError(f"У джерела «{source.code}» немає жодного активного листа прайсу.")

    client = client or get_client()
    settings_solo = SiteSettings.get_solo()
    rate = settings_solo.usd_rate
    rule = settings_solo.price_rounding

    # --- КРОК 2/3/4. Читання, звірка повноти, парсинг ----------------------------
    all_rows: list[PriceRow] = []
    counters = SheetCounters()
    rows_read = rows_expected = 0
    sheet_rate: Decimal | None = None
    sheet_date = None

    if not source.spreadsheet_id:
        raise SyncError(f"У джерела «{source.code}» не вказано spreadsheet_id.")

    for sheet in sheets:
        read = read_price_sheet(client, source.spreadsheet_id, sheet)
        rows_expected += read.row_count
        rows_read += len(read.rows)

        # --- КРОК 3.6. Курс USD і дата зрізу з самої таблиці ---------------------
        if sheet.rate_cell and sheet_rate is None:
            sheet_rate = _safe_decimal(read.meta.get(sheet.rate_cell))
        if sheet.date_cell and sheet_date is None:
            sheet_date = parse_sheet_date(read.meta.get(sheet.date_cell))

        all_rows.extend(_parse_sheet(read, sheet, rlog, counters, issues))

    run.rows_read = rows_read
    run.rows_expected = rows_expected
    run.rows_failed = counters.invalid

    # --- Курс: адмінка — ЄДИНЕ джерело правди -----------------------------------
    if sheet_rate and rate and abs(sheet_rate - rate) / rate > RATE_MISMATCH_THRESHOLD:
        rlog.add(
            L.WARN,
            A.SKIPPED,
            code="RATE_MISMATCH",
            message=(
                f"Курс у таблиці {sheet_rate}, в адмінці {rate}. Ціни пораховано за КУРСОМ "
                f"З АДМІНКИ. Щоб узяти табличний — «Підтягнути курс з таблиці» (це зробить "
                f"нормальний recalc з аудитом UsdRateChange)."
            ),
            payload={"sheet_rate": str(sheet_rate), "admin_rate": str(rate)},
        )
    if sheet_date and (timezone.localdate() - sheet_date).days > STALE_SHEET_DAYS:
        rlog.add(
            L.WARN,
            A.SKIPPED,
            code="STALE_PRICE_SHEET",
            message=(
                f"Прайс від {sheet_date:%d.%m.%Y}, сьогодні {timezone.localdate():%d.%m.%Y} — "
                f"таблицю давно не оновлювали, а ми старанно синкаємо старі ціни."
            ),
        )

    run.stats = run.stats | {
        "sheet_usd_rate": str(sheet_rate) if sheet_rate else None,
        "sheet_snapshot_date": sheet_date.isoformat() if sheet_date else None,
        "admin_usd_rate": str(rate),
        "price_rounding": rule,
        "needs_attention": bool(
            rlog.counts.get("RATE_MISMATCH") or rlog.counts.get("CATEGORY_MISSING")
        ),
    }

    # USD-рядок без валідного курсу = ціна NULL = NOT NULL violation. Ловимо ДО запису.
    if any(r.currency == SourceCurrency.USD for r in all_rows) and (not rate or rate <= 0):
        raise SyncError("У прайсі є USD-товари, а курс у «Налаштуваннях магазину» не додатний.")

    # --- КРОК 5. Дедуплікація ----------------------------------------------------
    before = len(all_rows)
    rows = dedupe_rows(all_rows, rlog, issues)
    run.rows_duplicated = before - len(rows)
    # ⚠️ Рядки-чернетки (сурогатний ключ) — це НЕ «пропущено»: з них БУДЕ товар, просто
    #    схований. У rows_skipped лишаються тільки ті, з яких товару не буде взагалі.
    run.rows_skipped = counters.section + counters.empty + (counters.no_sku - counters.surrogate)
    run.stats = run.stats | {
        "rows_no_sku": counters.no_sku,
        "rows_no_photo": counters.no_photo,
        "rows_missing_category": counters.no_category,
        "rows_surrogate_sku": counters.surrogate,
        "rows_incomplete": sum(1 for r in rows if r.is_incomplete),
        "rows_section": counters.section,
        "rows_empty": counters.empty,
    }

    if not rows:
        raise SyncError("У прайсі не знайдено ЖОДНОГО валідного рядка — читаємо не те.")

    # --- КРОК 5b. План усиновлення чернеток (читання; ПОТРІБЕН ЗАПОБІЖНИКАМ) ------
    # Замовник вписав артикули → старі AUTO-sku «зникають». Guard мусить знати, що це
    # перейменування, а не масова деактивація (див. build_guard_report).
    adoption_plan = plan_surrogate_adoptions(rows, rlog)

    # --- КРОК 6. ЗАПОБІЖНИКИ (ДО будь-якого запису) ------------------------------
    report, triggered = build_guard_report(rows, source, adoption_plan)
    report["triggered"] = triggered
    report["forced"] = force
    run.guard_report = report
    if triggered and not force:
        raise GuardAborted(report)
    if triggered and force:
        rlog.add(
            L.WARN,
            A.SKIPPED,
            code="GUARD_FORCED",
            message=f"Запобіжники {triggered} проігноровано (force=True).",
            payload=report,
        )

    # --- КРОК 7. Сирий знімок ----------------------------------------------------
    run.raw_snapshot_key = store_raw_snapshot(run, rows)

    # --- КРОК 7b. Покриття характеристиками — ДО запису, щоб потрапило й у dry-run ----
    # Недоступна таблиця характеристик НЕ валить прайс: ціни й наявність важливіші за
    # габарити, а їх добере наступний прогін.
    spec_records: list[SpecRecord] = []
    if fill_specs:
        try:
            spec_records = read_spec_records(source, client)
            run.stats = run.stats | {
                "specs_rows": len(spec_records),
                "specs_not_found": check_spec_coverage(rows, spec_records, issues),
            }
        except SheetsError as exc:
            rlog.add(
                L.ERROR,
                A.SKIPPED,
                code="SPECSHEET_FAILED",
                message=f"Таблиця характеристик недоступна: {exc}",
            )

    # --- КРОК 8. ЗАПИС -----------------------------------------------------------
    skus = [r.sku for r in rows]
    stats = ProjectionStats()

    try:
        with transaction.atomic(), price_reason("sync", str(run.id)):
            # 8a-bis. УСИНОВЛЕННЯ: замовник вписав артикул у рядок, який раніше був чернеткою →
            #         оновлюємо sku ТОМУ САМОМУ товару. МУСИТЬ бути ДО upsert-а офферів,
            #         інакше проєкція створить другий товар, а перший осиротіє (SYNC.md §4.3).
            adopted_ids = adopt_surrogate_skus(adoption_plan, source, rlog)
            run.stats = run.stats | {"surrogates_adopted": len(adopted_ids)}

            take_snapshot(run, skus)  # 8a
            run.rows_failed += upsert_offers(rows, source, run, rlog)  # 8b

            # 8c. Деактивація ЗНИКЛИХ ОФФЕРІВ — ТІЛЬКИ у своїй пісочниці.
            #     Жодного перетину з іншими джерелами: прогін Alpha більше НЕ деактивує Complex.
            vanished = SupplierOffer.objects.filter(price_source=source, is_active=True).exclude(
                last_seen_run=run.id
            )
            # ⚠️ Список ЗНИКЛИХ артикулів потрібен проєкції, і це не оптимізація.
            #    SKU зник з прайсу Complex, але ЛИШИВСЯ у прайсі Alpha → переможцем має стати Alpha.
            #    Якщо проєктувати тільки артикули З ЦЬОГО прогону, зниклий SKU у winner не
            #    потрапляє взагалі: блоки 2–4 його не бачать, блок 5 не деактивує (активний
            #    оффер Alpha є) — і товар назавжди лишається з ЦІНОЮ ТА ДЖЕРЕЛОМ Complex,
            #    якого в прайсі вже немає. Тихо і «успішно».
            vanished_skus = list(vanished.values_list("sku", flat=True))
            vanished.update(
                is_active=False,
                availability=Availability.OUT_OF_STOCK,
                stock_qty=0,
                updated_at=timezone.now(),
            )

            projected = list(dict.fromkeys([*skus, *vanished_skus]))
            stats = project_offers(projected, run, rate, rule, rlog, issues)  # 8d

            run.products_created = stats.created
            run.products_updated = stats.updated
            run.products_deactivated = stats.deactivated
            run.stats = run.stats | {
                "products_hidden_incomplete": stats.hidden_incomplete,
                "products_auto_activated": stats.auto_activated,
            }

            # КРОК 12. Рядки ProductImage створюються СИНХРОННО, у цій же транзакції;
            # завантаження байтів — задачею в черзі images. Тому втрата задачі не фатальна:
            # heal_images добере записи з downloaded_at IS NULL.
            run.images_queued = _queue_images(rows, run, rlog)

            if dry_run:
                raise _DryRunRollback
    except _DryRunRollback:
        log.info("SyncRun %s — dry-run, транзакцію відкочено.", run.id)
        run.stats = run.stats | {"dry_run_rolled_back": True}
        return

    if fill_specs and spec_records:
        try:
            # Усиновлені товари теж ідуть у зіставлення: у них ЩОЙНО з'явився справжній
            # артикул, тобто вперше можна зіставити їх за КЛЮЧЕМ, а не за назвою.
            targets = list(dict.fromkeys([*stats.new_ids, *adopted_ids]))
            run.specs_filled = fill_specs_for_products(
                source, targets, run, rlog, client, records=spec_records
            )
        except SheetsError as exc:
            # Таблиця характеристик недоступна → це НЕ привід валити прайс:
            # ціни й наявність уже актуальні, а габарити добере наступний прогін.
            rlog.add(
                L.ERROR,
                A.SKIPPED,
                code="SPECSHEET_FAILED",
                message=f"Таблиця характеристик недоступна: {exc}",
            )

    _enqueue_translations(stats.new_ids)
    _enqueue_denorm(run)


def _enqueue_denorm(run: SyncRun) -> int:
    """Ставить `rebuild_product_denorm` на товари, яких торкнувся цей прогін.

    ⚠️ ЧОМУ ЦЕ ПОТРІБНО. Синк виставляв `denorm_dirty=True` — і на цьому все: задачу
    перебудови НЕ СТАВИВ НІХТО. Товар лишався з порожнім specs_json і БЕЗ пошукового
    вектора, доки його випадково не підбере beat-задача `heal_denorm` (раз на 5 хв,
    ПАЧКАМИ ПО 500 і по ВСІЙ базі). Тобто щойно завезений товар не знаходився пошуком
    невизначено довго — а якщо beat не запущений, то НІКОЛИ. Саме так виглядала скарга
    «пошук не працює»: він працював, але вибірково й «випадково».

    `heal_denorm` лишається — але як СТРАХОВКА від втраченої задачі (ADR-011), а не як
    основний і єдиний шлях, яким денорм узагалі колись відбувається.

    Пачками по 500: один `.delay()` зі списком у тисячі id роздуває повідомлення брокера.
    """
    from sync.tasks import rebuild_product_denorm

    ids = list(
        Product.objects.filter(last_seen_run=run.id, denorm_dirty=True).values_list(
            "id", flat=True
        )
    )
    if not ids:
        return 0

    chunks = [ids[i : i + 500] for i in range(0, len(ids), 500)]
    # Ми вже ПОЗА `transaction.atomic` (dry-run відсіявся раніше), дані закомічені —
    # on_commit тут виконає колбек негайно, але лишає коректну семантику, якщо цю
    # функцію колись викличуть усередині транзакції.
    transaction.on_commit(
        lambda: [rebuild_product_denorm.delay(chunk) for chunk in chunks] and None
    )
    return len(ids)


def _safe_decimal(raw: Any) -> Decimal | None:
    try:
        return parse_decimal(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Фото (черга images)
# ---------------------------------------------------------------------------


def url_hash(url: str) -> str:
    """sha256(URL) — перший з трьох рівнів дедуплікації фото (SYNC.md §7)."""
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _queue_images(rows: Sequence[PriceRow], run: SyncRun, rlog: RunLog | None = None) -> int:
    """Створює `ProductImage` (без байтів) і ставить завантаження в чергу `images`."""
    from sync.tasks import download_product_image

    by_sku = {r.sku: r for r in rows if r.photo_urls}
    if not by_sku:
        return 0

    products = dict(Product.objects.filter(sku__in=list(by_sku)).values_list("sku", "id"))
    queued: list[int] = []
    touched: list[int] = []

    for sku, row in by_sku.items():
        pid = products.get(sku)
        if pid is None:
            continue
        touched.append(pid)
        for position, url in enumerate(row.photo_urls):
            image, created = ProductImage.objects.get_or_create(
                product_id=pid,
                source_url_hash=url_hash(url),
                defaults={
                    "source": ProductSource.SHEET,
                    "source_url": url[:1000],
                    "position": position,
                    "last_seen_run": run.id,
                },
            )
            if not created:
                # ⚠️ last_seen_run — саме те, за чим прибираються ЗАСТАРІЛІ фото
                # (постачальник замінив URL) → prune_stale_images().
                # ⚠️ position теж переписуємо З ПРАЙСУ: постачальник міг поставити нове фото
                #    ПЕРШИМ, а наявне посунути другим. Без цього обидва мали б position=0,
                #    порядок вирішувався б за id — і в галереї першим лишалось би старе фото.
                # ⚠️ Фільтр source=SHEET: позицію РУЧНОГО фото розставила людина — не чіпаємо.
                ProductImage.objects.filter(pk=image.pk, source=ProductSource.SHEET).update(
                    last_seen_run=run.id, position=position, updated_at=timezone.now()
                )
            if not image.downloaded_at:
                queued.append(image.pk)

    # Фото, яких у прайсі вже НЕМАЄ. Товари, у яких зараз щось качається, prune пропустить сам
    # (див. запобіжник 4) — їх добере `download_product_image` після завантаження заміни.
    # А от «постачальник ПРИБРАВ друге фото» жодного завантаження не породжує — і без цього
    # виклику зайве фото не прибралось би НІКОЛИ.
    prune_stale_images(touched, run.id, rlog=rlog)

    run_id = str(run.id)
    for image_id in queued:
        transaction.on_commit(
            lambda iid=image_id: download_product_image.delay(image_id=iid, run_id=run_id)
        )
    return len(queued)


#: Усі чотири файли фото (оригінал + 3 деривативи) — прибирати треба КОЖЕН, інакше зі сховища
#: зникає тільки рядок у БД, а мегабайти лишаються назавжди.
IMAGE_FILE_FIELDS = ("file", "file_large", "file_card", "file_thumb")


def delete_image_files(paths: Sequence[str]) -> None:
    """Видаляє файли зі сховища. Викликається ТІЛЬКИ з `on_commit` — тобто ПОЗА транзакцією.

    ⚠️ R2/S3 не вміє rollback: файл, видалений усередині транзакції, яку потім відкотили, — це
       товар із рядком у БД і без байтів. Плюс мережевий виклик усередині транзакції тримає
       локи на рядках рівно стільки, скільки відповідає сховище.
    Помилка сховища не має валити синк: непотрібний файл — це сміття, а не аварія.
    """
    for path in paths:
        try:
            default_storage.delete(path)
        except Exception as exc:  # сміття в сховищі — не привід валити прогін
            log.warning("Не вдалось видалити файл фото %s: %s", path, exc)


def ensure_main_image(product_id: int) -> None:
    """Товар МАЄ мати головне фото, якщо фото взагалі є.

    Товар без `is_main` — це товар без фото у фіді Hotline (`feeds/generator.py` бере саме
    головне) і без картинки в картці. Головним стає ПЕРШЕ ЗА ПОЗИЦІЄЮ фото, у якого вже є
    файл: рядок без байтів у ролі головного = порожня картка.

    ⚠️ Наявне головне фото НЕ перепризначаємо: його міг поставити руками контент-менеджер
       (SYNC.md §1 — порядок фото і головне фото належать адмінці).
    ⚠️ `uniq_main_image` — ЧАСТКОВИЙ unique-індекс, він НЕ МОЖЕ бути DEFERRABLE. Тому зміна
       головного фото ЗАВЖДИ двокрокова: спершу гасимо старий прапорець, потім ставимо новий.
    """
    images = ProductImage.objects.filter(product_id=product_id)
    if images.filter(is_main=True).exclude(file="").exists():
        return
    first = images.exclude(file="").order_by("position", "id").first()
    if first is None:
        return
    with transaction.atomic():
        images.filter(is_main=True).exclude(pk=first.pk).update(
            is_main=False, updated_at=timezone.now()
        )
        ProductImage.objects.filter(pk=first.pk).update(is_main=True, updated_at=timezone.now())


def prune_stale_images(
    product_ids: Sequence[int], run_id: uuid.UUID | str, *, rlog: RunLog | None = None
) -> int:
    """Прибирає фото З ПРАЙСУ, яких у прайсі цього прогону вже немає (SYNC.md §7).

    Замовник замінив мертве посилання на нове → синк створює НОВИЙ `ProductImage`, а старий
    без цієї функції лишається назавжди і (з меншим `position`) далі є головним: людина
    змінила фото, на сайті не змінилось нічого, сховище накопичує сміття.

    ЧОТИРИ ЗАПОБІЖНИКИ — кожен закриває свій спосіб залишити картку без фото:
      1. ТІЛЬКИ `source=sheet`. Ручне фото додала ЛЮДИНА — синк не чіпає його НІКОЛИ.
      2. ТІЛЬКИ товари, у яких у ЦЬОМУ прогоні є фото з прайсу. Порожня комірка «Фото» не
         створює жодного рядка → товар сюди просто не потрапляє → його фото лишаються.
         Порожня комірка ≠ «видали всі фото».
      3. ТІЛЬКИ коли заміна ВЖЕ НА ДИСКУ (`downloaded_at`). Нове посилання мертве (404) →
         старе фото лишається: краще старе фото, ніж порожня картка.
      4. НІЧОГО не чіпаємо, поки хоч одне фото цього прогону ще в черзі, — інакше прибирання
         сталося б РАНІШЕ, ніж доїхала заміна.

    Файли зі сховища прибираються ПІСЛЯ коміту (`on_commit`), а не в транзакції.
    """
    pids = list(dict.fromkeys(product_ids))
    if not pids:
        return 0

    sheet_images = ProductImage.objects.filter(product_id__in=pids, source=ProductSource.SHEET)
    current = sheet_images.filter(last_seen_run=run_id)

    ready = set(current.filter(downloaded_at__isnull=False).values_list("product_id", flat=True))
    pending = set(
        current.filter(downloaded_at__isnull=True, failed_reason="").values_list(
            "product_id", flat=True
        )
    )
    targets = ready - pending
    if not targets:
        return 0

    stale = list(sheet_images.filter(product_id__in=targets).exclude(last_seen_run=run_id))
    if not stale:
        return 0

    files = [name for img in stale for f in IMAGE_FILE_FIELDS if (name := getattr(img, f).name)]
    by_product: dict[int, list[str]] = {}
    for img in stale:
        by_product.setdefault(img.product_id, []).append(img.source_url)

    ProductImage.objects.filter(pk__in=[img.pk for img in stale]).delete()

    skus = dict(Product.objects.filter(id__in=list(by_product)).values_list("id", "sku"))
    for pid, urls in by_product.items():
        # Разом зі старим рядком щойно поїхало й головне фото — товар не має лишитись без нього.
        ensure_main_image(pid)
        _log_purged(run_id, pid, skus.get(pid, ""), urls, rlog)

    if files:
        transaction.on_commit(lambda: delete_image_files(files))

    log.info("Прогін %s: прибрано %s застарілих фото з прайсу.", run_id, len(stale))
    return len(stale)


def _log_purged(
    run_id: uuid.UUID | str, product_id: int, sku: str, urls: list[str], rlog: RunLog | None
) -> None:
    """Видалення даних має бути ВИДНО в журналі синхронізацій, а не тільки в логах воркера."""
    message = f"Прибрано застарілих фото з прайсу: {len(urls)}"
    payload = {"product_id": product_id, "urls": urls[:20]}
    if rlog is not None:
        # Синхронний шлях (усередині прогону): rlog переживає rollback dry-run.
        rlog.add(
            L.INFO, A.IMAGE_PURGED, code="IMAGE_PURGED", sku=sku, message=message, payload=payload
        )
        return
    # Шлях черги `images`: прогін уже закінчився і rlog давно змито — пишемо рядок напряму.
    SyncLogEntry.objects.create(
        run_id=run_id,
        level=L.INFO,
        action=A.IMAGE_PURGED,
        sku=sku[:64],
        product_id=product_id,
        message=message,
        payload=payload | {"code": "IMAGE_PURGED"},
    )


# ---------------------------------------------------------------------------
# §6 — ХАРАКТЕРИСТИКИ зі SpecSheet
# ---------------------------------------------------------------------------


def _spec_index(
    records: Sequence[SpecRecord],
) -> tuple[dict[str, SpecRecord], dict[str, SpecRecord]]:
    by_sku: dict[str, SpecRecord] = {}
    by_name: dict[str, SpecRecord] = {}
    for rec in records:
        if rec.sku:
            by_sku.setdefault(rec.sku, rec)
        if rec.name_normalized:
            by_name.setdefault(rec.name_normalized, rec)
    return by_sku, by_name


def _get_or_create_unit(name: str, cache: dict[str, int | None], rlog: RunLog) -> int | None:
    if not name:
        return None
    key = name.casefold()
    if key in cache:
        return cache[key]
    unit = _find_by_name(Unit.objects.all(), "name", name)
    if unit is None:
        unit = Unit.objects.filter(code=slugify(name)).first()
    if unit is None:
        code = slugify(name) or f"u{abs(hash(key)) % 10**5}"
        unit, _ = Unit.objects.get_or_create(
            code=code[:32], defaults={"name": name[:32], "needs_review": True}
        )
    cache[key] = unit.pk
    return unit.pk


_BOOL_TRUE = {"так", "є", "yes", "true", "1", "+", "да", "имеется", "наявний"}
_BOOL_FALSE = {"ні", "немає", "no", "false", "0", "-", "нет", "отсутствует"}


def _value_type(spec: Spec) -> str:
    value = spec.value.strip().casefold()
    if value in _BOOL_TRUE or value in _BOOL_FALSE:
        return Attribute.ValueType.BOOL
    number = _safe_decimal(spec.value)
    if number is not None and spec.unit:
        return Attribute.ValueType.NUMBER
    if number is not None and _is_pure_number(spec.value):
        return Attribute.ValueType.NUMBER
    return Attribute.ValueType.OPTION


def _is_pure_number(raw: str) -> bool:
    stripped = raw.strip().replace(",", ".").replace(" ", "")
    try:
        Decimal(stripped)
    except Exception:
        return False
    return True


def _get_or_create_attribute(
    spec: Spec,
    cache: dict[str, Attribute],
    unit_cache: dict[str, int | None],
    default_group: AttributeGroup,
    rlog: RunLog,
) -> Attribute:
    """Знайти `Attribute` за `code`, `name` або `aliases` (GIN); не знайшли — створити.

    ⚠️ Новий атрибут ЗАВЖДИ `is_filterable=False`, `filter_widget=NONE`, `needs_review=True`.
    Він одразу видно в картці, але у фільтри не лізе, поки людина не увімкне — саме цим ми
    не повторюємо брудні фасети старого сайту (`Sharp` і `SHARP` як два різні фільтри).
    """
    key = spec.name.casefold()
    if key in cache:
        return cache[key]

    attr = _find_by_name(Attribute.objects.all(), "name", spec.name)
    if attr is None:
        code = slugify(normalize_model_name(spec.name)) or slugify(spec.name)
        if code:
            attr = Attribute.objects.filter(code=code).first()

    if attr is None:
        code = slugify(spec.name) or f"attr-{abs(hash(key)) % 10**6}"
        base, n = code, 1
        while Attribute.objects.filter(code=code).exists():
            n += 1
            code = f"{base}-{n}"
        attr = Attribute.objects.create(
            code=code[:80],
            name=spec.name[:160],
            unit_id=_get_or_create_unit(spec.unit, unit_cache, rlog),
            group=default_group,
            value_type=_value_type(spec),
            is_filterable=False,
            filter_widget=Attribute.FilterWidget.NONE,
            needs_review=True,
        )
        rlog.add(
            L.WARN,
            A.ATTR_CREATED,
            code="ATTR_CREATED",
            message=(
                f"Створено характеристику «{spec.name}» (needs_review=True, у фільтри НЕ йде "
                f"до схвалення)"
            ),
        )
    cache[key] = attr
    return attr


def _find_by_name(qs: Any, field: str, value: str) -> Any:
    """Пошук довідникового запису за назвою — по aliases, по `<field>_uk` І по БАЗОВІЙ колонці.

    ⚠️ БАЗОВА КОЛОНКА — не педантизм, а реальний баг, спійманий бойовим прогоном.
    modeltranslation підміняє менеджер: `filter(name__iexact=…)` МОВЧКИ перетворюється на
    `filter(name_uk__iexact=…)`. У сідах є атрибут з `name="Колір"`, але `name_uk="Колір виробу"`
    — тобто пошук по «Колір» не знаходив НІЧОГО і синк створював ДРУГИЙ атрибут «Колір»
    (needs_review). Це рівно ті брудні фасети старого сайту («Sharp» і «SHARP» окремими
    фільтрами), заради яких уся ця нормалізація й затівалась.
    `rewrite(False)` вимикає підміну і дає доступ до справжньої колонки.
    """
    hit = qs.filter(aliases__contains=[value]).first()
    if hit is not None:
        return hit
    hit = qs.filter(**{f"{field}__iexact": value}).first()  # → <field>_uk (modeltranslation)
    if hit is not None:
        return hit
    if hasattr(qs, "rewrite"):
        return qs.rewrite(False).filter(**{f"{field}__iexact": value}).first()
    return None


def _get_or_create_option(attr: Attribute, value: str) -> AttributeOption:
    option = _find_by_name(AttributeOption.objects.filter(attribute=attr), "value", value)
    if option is None:
        slug = slugify(normalize_model_name(value)) or slugify(value) or "opt"
        base, n = slug, 1
        while AttributeOption.objects.filter(attribute=attr, slug=slug).exists():
            n += 1
            slug = f"{base}-{n}"
        option = AttributeOption.objects.create(
            attribute=attr,
            value=value[:160],
            slug=slug[:180],
            needs_review=True,
        )
    return option


def read_spec_records(source: PriceSource, client: Any = None) -> list[SpecRecord]:
    """Таблиця характеристик — ОДИН раз за прогін.

    ⚠️ Читається ДО запису (щоб `check_spec_coverage` встиг покласти проблеми у звіт навіть
    у dry-run) і використовується повторно у `fill_specs_for_products`. Без цього ми або
    читали б лист 1580×150 двічі, або dry-run не показував би замовнику найкорисніше:
    «на які артикули з прайсу немає рядка в характеристиках».
    """
    spec_sheet = source.spec_sheets.filter(is_active=True).first()
    if spec_sheet is None:
        return []
    return read_spec_sheet(client or get_client(), spec_sheet)


def check_spec_coverage(
    rows: Sequence[PriceRow], records: Sequence[SpecRecord], issues: IssueLog
) -> int:
    """Артикул є в прайсі, але рядка в «Основній» немає → товар без габаритів і характеристик.

    Перевіряється по ВСІХ рядках прайсу, а не лише по новостворених товарах: артикул могли
    додати в прайс місяць тому, а в характеристики так і не внести — і товар усі ці тижні
    їде в Нову Пошту без габаритів упаковки (тобто з заниженою вартістю доставки).
    """
    if not records:
        return 0

    by_sku, by_name = _spec_index(records)
    missing = 0
    for row in rows:
        if row.sku_is_surrogate:
            continue  # чернетки й так у MISSING_SKU — не дублюємо шум
        if row.sku in by_sku or find_spec_match(row.name, by_name) is not None:
            continue
        missing += 1
        issues.add(
            I.SPEC_NOT_FOUND,
            sheet=row.sheet,
            row_number=row.row_number,
            sku=row.sku,
            name=row.name,
            message=(
                f"Артикул «{row.sku}» є в прайсі, але його немає в таблиці характеристик "
                f"(«База даних (Актуальна)» → лист «Основна»). Товар показується, але без "
                f"характеристик і БЕЗ РОЗМІРІВ УПАКОВКИ — доставку Новою Поштою для нього "
                f"порахує неточно. Додайте рядок у «Основну»."
            ),
        )
    return missing


def fill_specs_for_products(
    source: PriceSource,
    product_ids: Sequence[int],
    run: SyncRun,
    rlog: RunLog,
    client: Any = None,
    records: Sequence[SpecRecord] | None = None,
) -> int:
    """Характеристики з окремої таблиці (SYNC.md §6).

    Ключ зіставлення — **АРТИКУЛ** (він є в обох таблицях). Фолбек — нормалізована назва
    через `find_spec_match` (порт скрипта Артура). Це не педантизм: у даних лишились рядки
    з порожнім артикулом, які інакше просто не отримають ані габаритів, ані гарантії.
    """
    if not product_ids:
        return 0

    spec_sheet = source.spec_sheets.filter(is_active=True).first()
    if spec_sheet is None:
        return 0

    if records is None:
        records = read_spec_sheet(client or get_client(), spec_sheet)
    by_sku, by_name = _spec_index(records)

    products = list(Product.objects.filter(id__in=list(product_ids)))
    default_group = (
        AttributeGroup.objects.filter(code="main").first()
        or AttributeGroup.objects.order_by("sort_order", "id").first()
    )
    if default_group is None:
        default_group = AttributeGroup.objects.create(code="main", name="Основні")

    attr_cache: dict[str, Attribute] = {}
    unit_cache: dict[str, int | None] = {}
    filled = 0

    for product in products:
        rec = by_sku.get(product.sku)
        matched_by = "sku"
        if rec is None:
            rec = find_spec_match(product.name, by_name)
            matched_by = "name"
        if rec is None:
            # DataIssue для замовника пише check_spec_coverage() — по ВСІХ рядках прайсу
            # і ДО запису (щоб список був і в dry-run). Тут лишається журнал для розробника.
            rlog.add(
                L.WARN,
                A.SKIPPED,
                code="SPECS_MISSING",
                sku=product.sku,
                message="Немає рядка в таблиці характеристик (ні за артикулом, ні за назвою)",
            )
            continue

        pavs: dict[tuple[int, int | None], ProductAttributeValue] = {}
        for spec in rec.specs:
            attr = _get_or_create_attribute(spec, attr_cache, unit_cache, default_group, rlog)
            pav = ProductAttributeValue(
                product=product,
                attribute=attr,
                raw_value=format_spec_value(spec.unit, spec.value)[:500],
                source=ProductSource.SHEET,
            )
            if attr.value_type == Attribute.ValueType.OPTION:
                pav.option = _get_or_create_option(attr, spec.value)
            elif attr.value_type == Attribute.ValueType.NUMBER:
                pav.value_number = _safe_decimal(spec.value)
                if pav.value_number is None:
                    pav.value_string = spec.value[:500]
            elif attr.value_type == Attribute.ValueType.BOOL:
                pav.value_bool = spec.value.strip().casefold() in _BOOL_TRUE
            else:
                pav.value_string = spec.value[:500]

            # ⚠️ ДЕДУП PAV У МЕЖАХ ТОВАРУ — та сама пастка «ON CONFLICT двічі».
            pavs[(attr.pk, pav.option_id)] = pav

        if pavs:
            _upsert_pavs(list(pavs.values()))
            filled += 1
            rlog.add(
                L.INFO,
                A.SPEC_FILLED,
                code="SPEC_FILLED",
                sku=product.sku,
                message=f"Заповнено {len(pavs)} характеристик (зіставлено за {matched_by})",
            )

        _apply_package_and_warranty(product, rec, rlog)

    Product.objects.filter(id__in=[p.pk for p in products]).update(
        denorm_dirty=True, updated_at=timezone.now()
    )
    return filled


#: ⚠️ `bulk_create(update_conflicts=True)` ТУТ НЕ ПРАЦЮЄ — і це не забаганка ORM.
#: `uniq_pav_scalar` / `uniq_pav_option` — ЧАСТКОВІ unique-індекси (ADR-016: на PG14 інакше
#: скаляри не захистиш, бо NULL-и завжди розрізняються). А PostgreSQL уміє вивести цільовий
#: індекс для `ON CONFLICT` лише разом з його ПРЕДИКАТОМ:
#:     ON CONFLICT (product_id, attribute_id) WHERE option_id IS NULL
#: Django ж генерує голий `ON CONFLICT (product_id, attribute_id)` і отримує
#: «there is no unique or exclusion constraint matching the ON CONFLICT specification».
#: Тому — сирий SQL з предикатом. Це ЄДИНИЙ спосіб зробити PAV ідемпотентними на PG14.
#:
#: ⚠️ `WHERE pav.source <> 'manual'` у DO UPDATE: значення, введене людиною, синк не затирає.
_PAV_UPSERT_SQL = """
INSERT INTO catalog_productattributevalue (
    product_id, attribute_id, option_id, value_string, value_string_uk,
    value_number, value_bool, raw_value, sort_order, source, created_at, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp(), clock_timestamp())
ON CONFLICT (product_id, attribute_id) WHERE option_id IS NULL
DO UPDATE SET
    value_string    = EXCLUDED.value_string,
    value_string_uk = EXCLUDED.value_string_uk,
    value_number    = EXCLUDED.value_number,
    value_bool      = EXCLUDED.value_bool,
    raw_value       = EXCLUDED.raw_value,
    updated_at      = clock_timestamp()
WHERE catalog_productattributevalue.source <> 'manual';
"""

_PAV_UPSERT_OPTION_SQL = """
INSERT INTO catalog_productattributevalue (
    product_id, attribute_id, option_id, value_string, value_string_uk,
    value_number, value_bool, raw_value, sort_order, source, created_at, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp(), clock_timestamp())
ON CONFLICT (product_id, attribute_id, option_id) WHERE option_id IS NOT NULL
DO UPDATE SET
    raw_value  = EXCLUDED.raw_value,
    updated_at = clock_timestamp()
WHERE catalog_productattributevalue.source <> 'manual';
"""


def _upsert_pavs(pavs: list[ProductAttributeValue]) -> None:
    """Ідемпотентний запис EAV. Два часткові unique → два окремі INSERT з предикатом."""

    def params(p: ProductAttributeValue) -> tuple[Any, ...]:
        return (
            p.product_id,
            p.attribute_id,
            p.option_id,
            p.value_string,
            p.value_string,  # value_string_uk: UK — мова-джерело прайсу
            p.value_number,
            p.value_bool,
            p.raw_value,
            p.sort_order,
            p.source,
        )

    scalars = [params(p) for p in pavs if p.option_id is None]
    options = [params(p) for p in pavs if p.option_id is not None]

    with connection.cursor() as cur:
        if scalars:
            cur.executemany(_PAV_UPSERT_SQL, scalars)
        if options:
            cur.executemany(_PAV_UPSERT_OPTION_SQL, options)


def _apply_package_and_warranty(product: Product, rec: SpecRecord, rlog: RunLog) -> None:
    """Габарити упаковки + вага + гарантія → денормалізовані поля `Product`.

    ⚠️ `package_dims_source == "manual"` — це і є замок: менеджер увів габарити руками,
    синк їх НЕ ЧІПАЄ. Третього булевого прапорця `dims_locked` не існує і не треба.
    ⚠️ У Нову Пошту йде ВАГА В УПАКОВЦІ (61.5), а не вага товару (58). Фолбек на вагу товару
    логується: це заниження доставки на КОЖНІЙ посилці.
    """
    if product.package_dims_source == ProductSource.MANUAL:
        return

    info = extract_package_dims_and_weight(rec.specs)
    height, width, depth = info.height_cm, info.width_cm, info.depth_cm

    if height is None and rec.package_dims_raw:
        dims = parse_package_dims(rec.package_dims_raw)  # колонка C таблиці характеристик
        if dims:
            height, width, depth = dims.height_cm, dims.width_cm, dims.depth_cm

    if info.weight_is_fallback:
        rlog.add(
            L.WARN,
            A.SKIPPED,
            code="PACKAGE_WEIGHT_FALLBACK",
            sku=product.sku,
            message=(
                f"«Вага в упаковці» не заповнена — взято вагу ТОВАРУ ({info.weight_kg} кг). "
                f"Це заниження ваги посилки: доплатимо за доставку ми."
            ),
        )

    for value, axis in ((height, "висота"), (width, "ширина"), (depth, "глибина")):
        if value is not None and value > SUSPICIOUS_DIM_CM:
            rlog.add(
                L.WARN,
                A.SKIPPED,
                code="SUSPICIOUS_DIMS",
                sku=product.sku,
                message=(
                    f"Габарит упаковки ({axis}) = {value} см — схоже, в колонці «см» лежать "
                    f"МІЛІМЕТРИ. Одиниці НЕ конвертуємо (вгадувати = ділити чужу посилку на 10)."
                ),
            )

    warranty = parse_warranty(rec.specs)
    updates: dict[str, Any] = {}
    if height is not None:
        updates |= {
            "package_height_cm": height,
            "package_width_cm": width,
            "package_depth_cm": depth,
            "package_dims_source": ProductSource.SHEET,
        }
    if info.weight_kg is not None:
        updates["package_weight_kg"] = info.weight_kg
    if warranty is not None:
        updates["warranty_months"] = warranty
    if rec.mpn and not product.mpn:
        updates["mpn"] = rec.mpn[:64]

    if updates:
        updates["updated_at"] = timezone.now()
        Product.objects.filter(pk=product.pk).update(**updates)


# ---------------------------------------------------------------------------
# §6.5 — черга перекладу (СЛОВНИКОВА, не потоварна)
# ---------------------------------------------------------------------------


def _enqueue_translations(new_product_ids: Sequence[int]) -> int:
    """Ставить у чергу `TranslationEntry` те, що синк реально створив.

    ⚠️ Синк НІКОЛИ не пише в `*_ru` напряму (SYNC.md §0) — тільки ставить у чергу.
    ⚠️ Черга СЛОВНИКОВА: `Attribute.name` / `AttributeOption.value` / `Unit.name`
    перекладаються ОДИН раз і застосовуються до всіх товарів. Наївне «переклади блок
    характеристик товару» дало б «Чорний» → і «Черный», і «Чёрный» (фасет розпадається),
    і рахунок ×140 (TRANSLATION.md §2).
    """
    from django.contrib.contenttypes.models import ContentType

    from translation.models import TranslationEntry, TranslationKind, compute_source_hash

    entries: list[TranslationEntry] = []

    def add(obj: Any, field_name: str, kind: str) -> None:
        text = getattr(obj, f"{field_name}_uk", None) or getattr(obj, field_name, "")
        if not text:
            return
        entries.append(
            TranslationEntry(
                content_type=ContentType.objects.get_for_model(type(obj)),
                object_id=obj.pk,
                field=field_name,
                kind=kind,
                source_text=text,
                source_hash=compute_source_hash(text),
            )
        )

    for attr in Attribute.objects.filter(needs_review=True):
        add(attr, "name", TranslationKind.ATTRIBUTE_NAME)
    for option in AttributeOption.objects.filter(needs_review=True):
        add(option, "value", TranslationKind.ATTRIBUTE_VALUE)
    for unit in Unit.objects.filter(needs_review=True):
        add(unit, "name", TranslationKind.UNIT)
    for product in Product.objects.filter(id__in=list(new_product_ids)):
        add(product, "name", TranslationKind.PRODUCT_NAME)

    if not entries:
        return 0

    TranslationEntry.objects.bulk_create(entries, ignore_conflicts=True, batch_size=500)
    return len(entries)


# ---------------------------------------------------------------------------
# §6.6 — ДОВІДНИК КАТЕГОРІЙ (лист «Категорії»)
# ---------------------------------------------------------------------------

#: Лист-довідник дерева категорій у файлі характеристик.
CATEGORY_TAB = "Категорії"


@dataclass
class CategoryImportStats:
    rows: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    problems: list[str] = field(default_factory=list)


def import_categories(
    spreadsheet_id: str,
    tab: str = CATEGORY_TAB,
    *,
    client: Any = None,
    dry_run: bool = False,
) -> CategoryImportStats:
    """Лист «Категорії» → дерево `catalog.Category`. Ідемпотентно.

    ⚠️ ЦЕ ГОТОВИЙ ДОВІДНИК, ЯКИЙ МИ НЕ ВИКОРИСТОВУВАЛИ. У файлі характеристик уже лежить
    повне дерево категорій замовника (70 рядків, 3 рівні):

        id         | level_1                    | level_2                     | level_3
        c87728634  | Аудіо, відео техніка       |                             |
        c10891916  | Аудіо, відео техніка       | Телевізори                  |
        c12462857  | Вбудована побутова техніка | Вбудовані варильні поверхні | Газові варильні поверхні

    Саме ці `id` стоять у колонці «Категорія» прайсу (`c50549829`). Без імпорту довідника
    КОЖЕН такий товар осідав у «__unmapped__» неактивним — тобто не потрапляв на сайт узагалі.
    Після імпорту всі 37 категорій, що реально вживаються в прайсі, знаходяться (звірено).

    `external_id` — id БЕЗ префікса «c» (так вимагає `Category.external_id`, і так само
    `norm_category_id()` чистить значення з прайсу — ключі сходяться).

    Батько шукається за ПРЕФІКСОМ ІМЕН (`level_1` → `level_1/level_2`), а не за порядком
    рядків: лист можуть відсортувати як завгодно, і дерево від цього ламатись не має.
    """
    client = client or get_client()
    total = client.row_count(spreadsheet_id, tab)
    if total < 2:
        raise SheetsError(f"Лист «{tab}»: у сітці {total} рядків — довідника немає")

    grid = client.read_rows(spreadsheet_id, tab, 1, total)
    positions = {norm_header(str(h)): i for i, h in enumerate(grid[0]) if str(h).strip()}
    id_col = positions.get("id")
    level_cols = [positions.get(f"level {n}") for n in (1, 2, 3)]

    if id_col is None or level_cols[0] is None:
        raise HeaderMismatch(
            f"Лист «{tab}»: очікувались колонки id / level_1 / level_2 / level_3, "
            f"знайдено {[str(h) for h in grid[0] if str(h).strip()]}"
        )

    def cell(cells: list[Any], col: int | None) -> str:
        if col is None or col >= len(cells):
            return ""
        return clean_or_empty(cells[col])

    stats = CategoryImportStats()
    parsed: list[tuple[str, tuple[str, ...]]] = []
    by_names: dict[tuple[str, ...], str] = {}

    for cells in grid[1:]:
        ext_id = norm_category_id(cell(cells, id_col))
        names = tuple(n for n in (cell(cells, c) for c in level_cols) if n)
        if not ext_id and not names:
            continue
        stats.rows += 1
        if not ext_id or not names:
            stats.skipped += 1
            stats.problems.append(f"рядок без id або без назви: id={ext_id!r}, назви={names}")
            continue
        if names in by_names and by_names[names] != ext_id:
            stats.skipped += 1
            stats.problems.append(f"дубль гілки {' / '.join(names)}: {by_names[names]} vs {ext_id}")
            continue
        by_names[names] = ext_id
        parsed.append((ext_id, names))

    with transaction.atomic():
        # Батьки — ПЕРШИМИ: Category.save() будує path з path батька, і без нього гілка
        # 3-го рівня отримала б шлях від кореня.
        for ext_id, names in sorted(parsed, key=lambda p: len(p[1])):
            parent = None
            if len(names) > 1:
                parent_ext = by_names.get(names[:-1])
                parent = (
                    Category.objects.filter(external_id=parent_ext).first() if parent_ext else None
                )
                if parent is None:
                    stats.skipped += 1
                    stats.problems.append(
                        f"«{' / '.join(names)}» (id={ext_id}): немає батьківського рядка "
                        f"«{' / '.join(names[:-1])}» — гілку пропущено"
                    )
                    continue

            name = names[-1]
            category = Category.objects.filter(external_id=ext_id).first()

            if category is None:
                category = Category(external_id=ext_id, name=name, parent=parent)
                slug = translit_slug(name) or f"c{ext_id}"
                category.slug = slug
                if hasattr(category, "name_uk"):
                    category.name_uk = name
                    category.slug_uk = slug
                category.save()
                stats.created += 1
                continue

            # ⚠️ slug НЕ чіпаємо: це URL, який уже може бути в індексі Google і в рекламі.
            #    Перейменування категорії в таблиці міняє НАЗВУ, а не адресу.
            changed = category.name != name or category.parent_id != (parent.pk if parent else None)
            if not changed:
                stats.unchanged += 1
                continue

            category.name = name
            if hasattr(category, "name_uk"):
                category.name_uk = name
            category.parent = parent
            category.save()
            stats.updated += 1

        # dry-run: усе порахували, показали — і відкотили. Жодних «майже застосував».
        if dry_run:
            transaction.set_rollback(True)

    return stats


# ---------------------------------------------------------------------------
# §5 — ВІДКАТ ПРОГОНУ
# ---------------------------------------------------------------------------

#: ⚠️ Відкочуємо ЛИШЕ товари, у яких `last_seen_run` досі дорівнює цьому прогону — тобто тих,
#: кого після нього не торкнувся наступний синк. Товари, змінені пізніше, НЕ чіпаються, і
#: скільки їх було — видно у звіті відкату (інакше «відкат» затер би свіжі коректні дані).
_ROLLBACK_SQL = """
UPDATE catalog_product p SET
    base_price      = s.old_base_price,
    price           = s.old_price,
    old_price       = s.old_old_price,
    source_currency = s.old_source_currency,
    markup_percent  = s.old_markup_percent,
    usd_rate_used   = s.old_usd_rate_used,
    availability    = s.old_availability,
    order_lead_days = s.old_order_lead_days,
    stock_qty       = s.old_stock_qty,
    synced_at       = s.old_synced_at,
    denorm_dirty    = true,
    updated_at      = clock_timestamp()
FROM sync_productpricesnapshot s
WHERE s.run_id = %(run_id)s
  AND p.id = s.product_id
  AND p.last_seen_run = %(run_uuid)s
  AND s.old_price IS NOT NULL
  AND s.old_base_price IS NOT NULL
RETURNING p.id;
"""


def rollback_run(run_id: str | uuid.UUID, user_id: int | None = None) -> SyncRun:
    """Відкотити прогін зі знімка (SYNC.md §5).

    Чіпаємо ЛИШЕ товари, у яких `last_seen_run` досі дорівнює цьому прогону — тобто тих,
    кого після нього не торкнувся ані наступний синк, ані інший прогін. Скільки товарів
    пропущено — видно у звіті відкату.
    """
    target = SyncRun.objects.get(pk=run_id)
    new_run = SyncRun.objects.create(
        kind=SyncRun.Kind.ROLLBACK,
        price_source=target.price_source,
        trigger=SyncRun.Trigger.MANUAL,
        rolled_back_run=target,
        started_by_id=user_id,
    )
    started = timezone.now()

    snapshot_total = ProductPriceSnapshot.objects.filter(run=target).count()
    try:
        with (
            transaction.atomic(),
            price_reason("rollback", str(new_run.id)),
            connection.cursor() as cur,
        ):
            cur.execute(_ROLLBACK_SQL, {"run_id": str(target.id), "run_uuid": str(target.id)})
            restored = cur.rowcount
    except Exception as exc:
        new_run.status = SyncRun.Status.FAILED
        new_run.traceback = str(exc)[:4000]
    else:
        new_run.status = SyncRun.Status.SUCCESS
        new_run.products_updated = restored
        new_run.stats = {
            "restored": restored,
            "snapshot_rows": snapshot_total,
            "skipped_touched_later": snapshot_total - restored,
        }

    new_run.finished_at = timezone.now()
    new_run.duration_ms = int((new_run.finished_at - started).total_seconds() * 1000)
    new_run.save()
    return new_run


# ---------------------------------------------------------------------------
# §4 — ПЕРЕРАХУНОК ЦІН
# ---------------------------------------------------------------------------

_RECALC_SQL = """
UPDATE catalog_product SET
    price     = complex_price_uah(base_price, source_currency, %(rate)s, markup_percent, %(rule)s),
    old_price = CASE
        WHEN complex_price_uah(base_old_price, source_currency, %(rate)s, markup_percent, %(rule)s)
             > complex_price_uah(base_price, source_currency, %(rate)s, markup_percent, %(rule)s)
        THEN complex_price_uah(base_old_price, source_currency, %(rate)s, markup_percent, %(rule)s)
    END,
    usd_rate_used    = CASE WHEN source_currency = 'USD' THEN %(rate)s END,
    price_updated_at = clock_timestamp(),
    updated_at       = clock_timestamp(),
    denorm_dirty     = true
WHERE NOT price_locked
  AND complex_price_uah(base_price, source_currency, %(rate)s, markup_percent, %(rule)s) IS NOT NULL
  {scope}
;
"""


def recalc_prices(
    scope: str = "usd",
    *,
    old_rate: Decimal | None = None,
    old_rounding: str | None = None,
    user_id: int | None = None,
) -> SyncRun:
    """Перерахунок цін ОДНІЄЮ формулою `complex_price_uah()` — тією самою, що й у синку.

    ⚠️ ТРИ ФІКСИ, кожен був окремим багом:
      1. **Націнка.** У v1 recalc рахував `base * rate` БЕЗ markup → перша ж зміна курсу
         стирала маржу на всіх USD-товарах. Тут `markup_percent` іде у формулу завжди.
      2. **`scope="all"`.** Зміна округлення перераховує ВЕСЬ каталог, не тільки USD —
         інакше половина каталогу лишиться в старому правилі до наступного синку.
      3. **Гонка з синком.** Береться ТОЙ САМИЙ advisory-lock, що й у `sync_prices`.
    """
    # ⚠️ Recalc бере ТІ САМІ per-source локи, що й синк (у детермінованому порядку —
    #    інакше два процеси, які беруть локи в різному порядку, дають дедлок).
    #    У v1 серіалізація трималась на недокументованому `-c 1` воркера: підняття
    #    конкурентності до 2 миттєво давало перезапис цін СТАРИМ курсом.
    keys = sorted(
        f"sync:prices:{code}"
        for code in PriceSource.objects.filter(is_active=True).values_list("code", flat=True)
    )
    with ExitStack() as stack:
        acquired = all(stack.enter_context(advisory_lock(key)) for key in keys)
        if not acquired:
            return SyncRun.objects.create(
                kind=SyncRun.Kind.PRICE_RECALC,
                trigger=SyncRun.Trigger.MANUAL,
                status=SyncRun.Status.SKIPPED,
                started_by_id=user_id,
                finished_at=timezone.now(),
                traceback="Синхронізація вже виконується — перерахунок пропущено.",
            )

        solo = SiteSettings.get_solo()
        run = SyncRun.objects.create(
            kind=SyncRun.Kind.PRICE_RECALC,
            trigger=SyncRun.Trigger.MANUAL,
            started_by_id=user_id,
        )
        started = timezone.now()
        scope_sql = "AND source_currency = 'USD'" if scope == "usd" else ""

        try:
            with (
                transaction.atomic(),
                price_reason("recalc", str(run.id)),
                connection.cursor() as cur,
            ):
                cur.execute(
                    _RECALC_SQL.format(scope=scope_sql),
                    {"rate": solo.usd_rate, "rule": solo.price_rounding},
                )
                n = cur.rowcount
        except Exception as exc:
            run.status = SyncRun.Status.FAILED
            run.traceback = str(exc)[:4000]
            n = 0
        else:
            run.status = SyncRun.Status.SUCCESS
            run.products_updated = n
            run.stats = {"scope": scope, "rate": str(solo.usd_rate), "rule": solo.price_rounding}

            from sync.models import UsdRateChange

            UsdRateChange.objects.create(
                old_rate=old_rate,
                new_rate=solo.usd_rate,
                old_rounding=old_rounding or "",
                new_rounding=solo.price_rounding,
                products_recalculated=n,
                run=run,
                changed_by_id=user_id,
            )

        run.finished_at = timezone.now()
        run.duration_ms = int((run.finished_at - started).total_seconds() * 1000)
        run.save()
        return run


# ---------------------------------------------------------------------------
# Денормалізація (rebuild_product_denorm)
# ---------------------------------------------------------------------------


def rebuild_denorm(product_ids: Sequence[int]) -> int:
    """`specs_json` / `filter_tokens` / `specs_num` / `main_image_url` + FTS-вектори.

    ⚠️ `denorm_dirty` знімається ТУТ і тільки тут. Виставляється він синхронно, в тій самій
    транзакції, що й зміна товару, — тому втрата on_commit-задачі не фатальна:
    `heal_denorm` добере товар за 5 хв (ADR-011).

    ⚠️ FTS-ВЕКТОРИ БУДУЄ `catalog.services.search_index.rebuild_search_vectors()`, а НЕ ця
       функція. Тут був справжній корінь скарги «пошук не працює»: замість канонічної
       функції стояв власний інлайн
           SearchVector("name_uk", config="uk")
       і він розходився з нею в ТРЬОХ місцях одночасно:
         1. у вектор ішла ТІЛЬКИ назва — без sku, бренду, категорії, характеристик і опису
            (тобто пошук за артикулом чи характеристикою не працював за визначенням);
         2. текст НЕ проганявся через `core.text.uk_stem`, хоча ЗАПИТ у search.py через нього
            проганяється — асиметрія стемера, від якої модуль search_index.py застерігає
            великими літерами («Порушиш симетрію — пошук замовкне мовчки»);
         3. не було `setweight` — усі лексеми лягали вагою D, і SearchRank ранжував сміттям.
       Товар, який пройшов синк, отримував ЗІПСОВАНИЙ вектор і мовчки випадав з пошуку —
       рівно доти, доки хтось руками не запускав `manage.py rebuild_search_index`.
       Тепер денорм і ручна команда будують вектор ОДНІЄЮ функцією: розійтись їм більше нічим.
    """
    from catalog.services.search_index import rebuild_search_vectors

    products = (
        Product.objects.filter(id__in=list(product_ids))
        .select_related("brand", "country", "category")
        .prefetch_related("attr_values__attribute__group", "attr_values__option", "images")
    )
    n = 0
    for product in products:
        specs_uk: list[dict[str, Any]] = []
        specs_ru: list[dict[str, Any]] = []
        tokens: list[str] = []
        nums: dict[str, float] = {}

        for pav in sorted(
            product.attr_values.all(),
            key=lambda p: (p.attribute.group.sort_order, p.attribute.sort_order, p.attribute_id),
        ):
            attr = pav.attribute
            unit_uk = getattr(attr.unit, "name_uk", None) or (attr.unit.name if attr.unit else "")
            unit_ru = getattr(attr.unit, "name_ru", None) or unit_uk

            if pav.option_id:
                value_uk = pav.option.value
                value_ru = getattr(pav.option, "value_ru", None) or value_uk
                tokens.append(f"{attr.code}:{pav.option.slug}")
            elif pav.value_number is not None:
                value_uk = value_ru = _fmt_number(pav.value_number)
                nums[attr.code] = float(pav.value_number)
            elif pav.value_bool is not None:
                value_uk, value_ru = ("Так", "Да") if pav.value_bool else ("Ні", "Нет")
                if pav.value_bool:
                    tokens.append(f"{attr.code}:1")
            else:
                value_uk = value_ru = pav.value_string

            if not value_uk:
                continue

            base = {"code": attr.code, "g": attr.group.name, "gs": attr.group.sort_order}
            specs_uk.append(
                base
                | {
                    "n": getattr(attr, "name_uk", None) or attr.name,
                    "u": unit_uk,
                    "v": value_uk,
                    "vn": float(pav.value_number) if pav.value_number is not None else None,
                }
            )
            specs_ru.append(
                base
                | {
                    "n": getattr(attr, "name_ru", None) or attr.name,
                    "u": unit_ru,
                    "v": value_ru,
                    "vn": float(pav.value_number) if pav.value_number is not None else None,
                }
            )

        if product.brand_id:
            tokens.append(f"brand:{product.brand.slug}")
        if product.country_id:
            tokens.append(f"country:{product.country.slug}")
        tokens.append(f"avail:{product.availability}")
        tokens.append(f"cond:{product.condition}")
        if product.installment_available:
            tokens.append("installment:1")

        main = next(
            (i for i in product.images.all() if i.is_main),
            next(iter(sorted(product.images.all(), key=lambda i: i.position)), None),
        )
        main_url = ""
        if main is not None:
            if main.file_card:
                main_url = main.file_card.url
            elif main.file:
                main_url = main.file.url

        Product.objects.filter(pk=product.pk).update(
            specs_json=specs_uk,
            specs_json_uk=specs_uk,
            specs_json_ru=specs_ru,
            filter_tokens=sorted(set(tokens)),
            specs_num=nums,
            main_image_url=main_url[:500],
            denorm_dirty=False,
            denorm_at=timezone.now(),
            updated_at=timezone.now(),
        )
        n += 1

    if n:
        # Та сама функція, що й у `manage.py rebuild_search_index`: sku+назва (A),
        # бренд+категорія (B), характеристики (C), опис (D), із коректним стемінгом.
        # Перечитуємо товари: specs_json уже оновлено вище, і вектор має бачити свіжі дані.
        rebuild_search_vectors(Product.objects.filter(id__in=list(product_ids)))
    return n


def _fmt_number(value: Decimal) -> str:
    normalized = value.normalize()
    text = f"{normalized:f}"
    return text


def heal_denorm(limit: int = 500) -> int:
    """Гарантія проти втраченої `on_commit`-задачі: `denorm_dirty=True` уже в БД."""
    from django.db.models import F, Q

    ids = list(
        Product.objects.filter(Q(denorm_dirty=True) | Q(denorm_at__lt=F("updated_at"))).values_list(
            "id", flat=True
        )[:limit]
    )
    return rebuild_denorm(ids) if ids else 0


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


def reap_stale_runs(older_than_minutes: int = 20) -> int:
    """`SyncRun`, що завис у RUNNING (OOM-kill воркера) → FAILED.

    Advisory-lock звільнився сам разом зі з'єднанням, а от рядок у БД лишився «Виконується»
    назавжди — і дашборд показує вічний зелений спінер замість аварії.
    """
    cutoff = timezone.now() - timedelta(minutes=older_than_minutes)
    stale = SyncRun.objects.filter(status=SyncRun.Status.RUNNING, started_at__lt=cutoff)
    return stale.update(
        status=SyncRun.Status.FAILED,
        finished_at=timezone.now(),
        traceback="reaped: прогін завис у RUNNING (найімовірніше — OOM-kill воркера).",
    )
