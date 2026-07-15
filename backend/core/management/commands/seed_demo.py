"""Демо-дані для локальної розробки:  `manage.py seed_demo [--flush]`.

Наповнює порожню БД шматком РЕАЛЬНОГО магазину Complex (INPUTS §1–§4), достатнім, щоб побачити
живими фільтри, фасети, порівняння, варіанти, супутні товари, чергу перекладу, замовлення,
CMS і журнал синхронізацій:

  • дерево категорій Complex (9 коренів + підкатегорії, external_id як у прайсі, міні-емблеми);
  • ~65 товарів: UAH і USD (видно перерахунок курсу), «в наявності»/«під замовлення»/«немає»,
    уцінені (condition=2), товари з оплатою частинами;
  • характеристики З ГРУПАМИ І ОДИНИЦЯМИ, числові — з value_number (працюють range-фільтри);
  • ДВА типи варіантів: BUTTONS (діагональ ТВ 50/55/65/75/85) і SWATCHES (колір чайника/НВЧ);
  • RelatedGroup — комплект вбудованої техніки (взаємний: кожен бачить двох інших);
  • переклади: словник (характеристики/значення/одиниці) — повністю ru; товари — частково,
    решта чекає в черзі TranslationEntry (PENDING/MACHINE) — саме так, як у бойовому пайплайні;
  • замовлення в різних статусах, банери, новини, статичні сторінки, точка самовивозу;
  • SyncRun + SyncLogEntry (успішний і з попередженнями).

⚠️ ЦІНУ РАХУЄ БАЗА, А НЕ PYTHON (ADR-005). Команда викликає ту саму SQL-функцію
   `complex_price_uah(base, currency, rate, markup, rule)`, що й синк, і recalc_prices. Другого
   двійника формули тут свідомо немає: демо-дані мусять бути точно такими, якими їх зробив би
   бойовий шлях, інакше на них не можна перевіряти ані фасети, ані фід.

⚠️ PriceHistory НЕ пишеться руками: рядки вставляє PostgreSQL-тригер `price_history_trg`
   (catalog/0002). Команда лише показує лічильник — це і є перевірка, що тригер живий.
   Повторний прогін НЕ плодить історію: тригер мовчить, коли price і availability не змінились.

⚠️ Денормалізацію (specs_json / filter_tokens / specs_num) команда рахує САМА, бо
   catalog.tasks.rebuild_product_denorm — етап 3, його ще немає. Формат — рівно той, що описаний
   біля Product.specs_json: одиниця клеїться до ЗНАЧЕННЯ («331 л»), а не до назви.

ІДЕМПОТЕНТНІСТЬ: усе через get_or_create/update_or_create по натуральних ключах
(sku, external_id, code, key, slug_uk, номер замовлення, uuid5-прогону). Повторний запуск
нічого не дублює. `--flush` зносить ТІЛЬКИ демо-об'єкти — за префіксами SKU / external_id /
номера замовлення / batch_id перекладу. Чужі дані не чіпає.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings as dj_settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import ProtectedError, Q
from django.utils import timezone
from django.utils import translation as django_translation

from catalog.models import (
    Attribute,
    AttributeGroup,
    AttributeOption,
    Availability,
    Brand,
    Category,
    Condition,
    Country,
    PriceHistory,
    Product,
    ProductAttributeValue,
    ProductSource,
    RelatedGroup,
    RelatedGroupItem,
    SourceCurrency,
    Unit,
    VariantGroup,
    VariantItem,
)
from cms.models import Banner, NewsPost, PickupPoint, StaticPage
from core.models import SiteSettings, WorkingHours
from orders.models import Order, OrderItem, OrderStatusHistory
from sync.models import PriceSheet, PriceSource, SyncLogEntry, SyncRun
from translation.models import TranslationEntry, TranslationKind, TranslationStatus

# --- Мітки демо-об'єктів. ТІЛЬКИ їх зносить --flush. -------------------------------------
DEMO_SKU_PREFIX = "CMPX-1"  # усі демо-товари: CMPX-1001xx … CMPX-1008xx
DEMO_CAT_PREFIX = "56097"  # усі демо-категорії: 5609710 … 5609790
DEMO_ORDER_PREFIX = "CMPX-DEMO-"  # CMPX-DEMO-0001 … (звичайні номери йдуть з sequence)
DEMO_BATCH = "seed-demo"  # TranslationEntry.batch_id
DEMO_SOURCE_CODE = "complex-demo"  # sync.PriceSource.code

# ⚠️ Ребрендинг NISA → Complex (14.07.2026). Демо-об'єкти, засіяні ДО перейменування,
#    лежать під старими префіксами. `--flush` шукає за префіксом — тому без цих констант
#    він би їх просто не побачив і лишив у БД назавжди (65 товарів-сиріт + другий
#    PriceSource). Зносимо обидва покоління; засіваємо — тільки нове.
LEGACY_SKU_PREFIX = "NISA-1"
LEGACY_ORDER_PREFIX = "NISA-DEMO-"
LEGACY_SOURCE_CODE = "nisa-demo"
DEMO_NEWS_SLUGS = ("znyzhky-na-vbudovanu-tekhniku", "yak-obraty-kholodylnyk")
DEMO_PAGE_KEYS = ("payment-delivery", "warranty", "about", "contacts")

# VariantGroup / RelatedGroup не мають природного ключа — тримаємо їхні назви тут,
# інакше --flush не знав би, що саме з них демо.
DEMO_VARIANT_GROUPS = (
    "Samsung Crystal UHD DU7100",
    "Bosch TWK3P42 — кольори",
    "Samsung MS23K35 — кольори",
)
DEMO_RELATED_GROUPS = (
    "Комплект вбудованої техніки Bosch Serie 6",
    "Комплект вбудованої техніки Gorenje",
)

# Детерміновані UUID прогонів синку: uuid5 замість uuid4 — інакше кожен запуск створював би
# нові SyncRun, і «ідемпотентна» команда тихо засмічувала б журнал.
NS = uuid.UUID("6f1e6a3c-0000-4000-8000-000000000000")
RUN_OK = uuid.uuid5(NS, "run-success")
RUN_PARTIAL = uuid.uuid5(NS, "run-partial")


# ---------------------------------------------------------------------------
# Ціна — з БД, не з Python (ADR-005)
# ---------------------------------------------------------------------------
def db_price(
    base: Decimal | None, currency: str, rate: Decimal | None, markup: Decimal, rule: str
) -> Decimal | None:
    """Той самий complex_price_uah(), яким рахують синк і recalc_prices."""
    if base is None:
        return None
    with connection.cursor() as cur:
        cur.execute(
            "SELECT complex_price_uah(%s, %s, %s, %s, %s)", [base, currency, rate, markup, rule]
        )
        return cur.fetchone()[0]


def bucket_token(code: str, buckets: list[list[int | None]], value: float) -> str | None:
    """«obiem» + 331 → "obiem:300-399"  (ADR-007: бакети, а не сирі числа у фасетах)."""
    for lo, hi in buckets:
        lo_ = lo if lo is not None else float("-inf")
        if hi is None:
            if value >= lo_:
                return f"{code}:{lo}-plus"
        elif lo_ <= value <= hi:
            return f"{code}:{lo}-{hi}"
    return None


# --- транслітерація для slug'ів (uk/ru → латиниця) ---------------------------------------
_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "h",
    "ґ": "g",
    "д": "d",
    "е": "e",
    "є": "ie",
    "ж": "zh",
    "з": "z",
    "и": "y",
    "і": "i",
    "ї": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ь": "",
    "ю": "iu",
    "я": "ia",
    "ы": "y",
    "э": "e",
    "ё": "e",
    "ъ": "",
    "'": "",
    "’": "",
    "`": "",
}


def translit_slug(text: str, max_len: int = 180) -> str:
    """Кирилиця → латиниця. django.utils.text.slugify на кирилиці дає ПОРОЖНІЙ рядок."""
    out: list[str] = []
    for ch in text.lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum() and ch.isascii():
            out.append(ch)
        else:
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:max_len].strip("-")


def dims(
    h_mm: int, w_mm: int, d_mm: int, kg: float, *, pad_cm: float = 6.0, sep: str = "x"
) -> dict[str, Any]:
    """Шість рядків групи «Габаритні розміри» одним викликом.

    Габарити УПАКОВКИ — це габарити товару + запас на пакування, у САНТИМЕТРАХ і в порядку
    ВхШхГ (INPUTS §3.4). Формат рядка — як у реальній таблиці: десяткова КОМА, роздільник
    «x» (місцями кирилична «х» — це реальний випадок, і парсер зобов'язаний його пережити).
    """

    def cm(mm: int) -> str:
        v = round(mm / 10 + pad_cm, 1)
        return (str(int(v)) if v == int(v) else f"{v:.1f}").replace(".", ",")

    pack_kg = round(kg * 1.08 + 0.5, 1)
    return {
        "vysota": h_mm,
        "shyryna": w_mm,
        "hlybyna": d_mm,
        "vaga": kg,
        "gabaryty-upakovky": f"{cm(h_mm)} {sep} {cm(w_mm)} {sep} {cm(d_mm)}",
        "vaga-v-upakovtsi": pack_kg,
    }


def parse_package_dims(raw: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """«171,5 x 56 x 35» → (171.5, 56, 35). Порядок ВхШхГ — НЕ переставляти."""
    if not raw:
        return None, None, None
    parts = raw.replace("×", "x").replace("х", "x").split("x")
    if len(parts) != 3:
        return None, None, None
    h, w, d = (Decimal(p.strip().replace(",", ".")) for p in parts)
    return h, w, d


# ---------------------------------------------------------------------------
# Медіа-заглушки (щоб адмінка і мегаменю не показували битих картинок)
# ---------------------------------------------------------------------------
def ensure_icon(name: str, glyph_path: str, color: str) -> str:
    """SVG-емблема категорії. FileField + validate_icon дозволяє .svg (Pillow його не читає)."""
    rel = f"categories/icons/{name}.svg"
    path = Path(dj_settings.MEDIA_ROOT) / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="1.6" stroke-linecap="round" '
            f'stroke-linejoin="round">{glyph_path}</svg>',
            encoding="utf-8",
        )
    return rel


def ensure_png(rel: str, size: tuple[int, int], bg: tuple[int, int, int]) -> str:
    """Заглушка під банер/обкладинку: ImageField без файла = 404 у прев'ю адмінки."""
    path = Path(dj_settings.MEDIA_ROOT) / rel
    if not path.exists():
        from PIL import Image, ImageDraw

        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", size, bg)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, size[1] - max(6, size[1] // 40), size[0], size[1]], fill=(228, 92, 42))
        img.save(path, "PNG")
    return rel


class Command(BaseCommand):
    help = "Демо-дані для локальної розробки (категорії, товари, варіанти, замовлення, CMS)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Знести демо-об'єкти (SKU CMPX-1*, категорії 56097*, замовлення CMPX-DEMO-*, "
            "а також їхні до-ребрендингові двійники NISA-*) перед створенням.",
        )

    @transaction.atomic
    def handle(self, *args: Any, **opts: Any) -> None:
        # Мова-джерело: усі присвоєння без суфікса лягають у *_uk. Без activate() команда
        # залежала б від LANGUAGE_CODE процесу і мовчки писала б демо-назви в _ru.
        django_translation.activate("uk")

        if opts["flush"]:
            self._flush()

        site = self._seed_settings()
        cats = self._seed_categories()
        brands, countries = self._seed_dictionaries()
        units, groups = self._seed_units_and_groups()
        attrs, options = self._seed_attributes(units, groups, cats)
        products = self._seed_products(site, cats, brands, countries, attrs, options)
        self._seed_variants(attrs, products)
        self._seed_related(products)
        self._seed_translations(products)
        self._seed_orders(products)
        self._seed_cms()
        self._seed_sync(products)
        self._cleanup_stale_options(attrs, options)

        self._report()

    # -- flush ---------------------------------------------------------------
    def _flush(self) -> None:
        """Зносить ТІЛЬКИ демо-об'єкти. Порядок важливий: спершу те, що посилається."""
        # ⚠️ .delete() повертає (ВСЬОГО_рядків, {модель: скільки}), і «всього» включає каскад
        #    (OrderItem + OrderStatusHistory), тому беремо саме лічильник Order — інакше в лог
        #    іде «знесено 27 замовлень» замість шести.
        _, per_model = Order.objects.filter(
            Q(number__startswith=DEMO_ORDER_PREFIX) | Q(number__startswith=LEGACY_ORDER_PREFIX)
        ).delete()
        n_ord = per_model.get("orders.Order", 0)
        n_tr, _ = TranslationEntry.objects.filter(batch_id=DEMO_BATCH).delete()

        SyncLogEntry.objects.filter(run_id__in=[RUN_OK, RUN_PARTIAL]).delete()
        n_run, _ = SyncRun.objects.filter(pk__in=[RUN_OK, RUN_PARTIAL]).delete()

        # VariantItem / RelatedGroupItem зникнуть каскадом разом з товарами, але самі групи —
        # ні: у них немає FK на товар. Зносимо їх за іменами (див. DEMO_*_GROUPS).
        VariantGroup.objects.filter(name_uk__in=DEMO_VARIANT_GROUPS).delete()
        RelatedGroup.objects.filter(name_uk__in=DEMO_RELATED_GROUPS).delete()

        # .delete() повертає (ВСЬОГО_рядків, {модель: скільки}) — а «всього» включає каскад
        # (PAV + PriceHistory + фото), тому беремо саме лічильник Product.
        _, per_model = Product.objects.filter(
            Q(sku__startswith=DEMO_SKU_PREFIX) | Q(sku__startswith=LEGACY_SKU_PREFIX)
        ).delete()
        n_prod = per_model.get("catalog.Product", 0)

        # Категорії — тільки листові спершу (parent PROTECT), тому сортуємо за глибиною вниз.
        n_cat = 0
        for cat in Category.objects.filter(external_id__startswith=DEMO_CAT_PREFIX).order_by(
            "-depth"
        ):
            cat.delete()
            n_cat += 1

        NewsPost.objects.filter(slug_uk__in=DEMO_NEWS_SLUGS).delete()
        StaticPage.objects.filter(key__in=DEMO_PAGE_KEYS).delete()
        # ⚠️ БАНЕРИ --flush БІЛЬШЕ НЕ ЧІПАЄ. Було: `Banner.objects.filter(link_url__startswith=
        #    "/uk/").delete()` — і це вже не «зачистка демо-даних», а міна. Сід банерів більше
        #    не створює (див. _seed_cms), тож єдине, що міг би знести цей рядок, — СПРАВЖНІ
        #    банери замовника: будь-який банер із посиланням усередину сайту починається з «/uk/».
        #    Решта рядків тут б'ють по явному списку демо-ключів (DEMO_NEWS_SLUGS, DEMO_PAGE_KEYS,
        #    DEMO_SOURCE_CODE) — тобто по даних, які сід точно створив сам. Для банерів такого
        #    списку більше не існує, бо не існує й самих демо-банерів.
        PriceSource.objects.filter(code__in=[DEMO_SOURCE_CODE, LEGACY_SOURCE_CODE]).delete()

        self.stdout.write(
            self.style.WARNING(
                f"--flush: знесено товарів {n_prod}, категорій {n_cat}, замовлень {n_ord}, "
                f"записів перекладу {n_tr}, прогонів синку {n_run}"
            )
        )

    # -- settings ------------------------------------------------------------
    def _seed_settings(self) -> SiteSettings:
        # ⚠️ get_or_create, а НЕ save() зі зміною курсу: SiteSettings.save() на реальну зміну
        #    usd_rate ставить у чергу catalog.tasks.recalc_prices (етап 3, ще не існує).
        #    При першому створенні diff-механізм мовчить (old is None) — саме те, що треба.
        obj, created = SiteSettings.objects.get_or_create(
            pk=1,
            defaults={
                "usd_rate": Decimal("41.5000"),
                "usd_rate_updated_at": timezone.now(),
                "price_rounding": SiteSettings.Rounding.NINE,
                "phones": ["+380950915222", "+380991717925"],
                "email": "uzh.tehnika77@gmail.com",
                "address_uk": "м. Ужгород, вул. Гагаріна, 101",
                "address_ru": "г. Ужгород, ул. Гагарина, 101",
                "installment_badge_text_uk": "Оплата частинами до 24 платежів",
                "installment_badge_text_ru": "Оплата частями до 24 платежей",
                "installment_provider": "Monobank, Privatbank",
                "free_shipping_from": Decimal("15000"),
            },
        )
        SiteSettings.invalidate()

        # INPUTS §4: ПН–ПТ 09:00–20:00, СБ–НД 10:00–17:00.
        for weekday, _label in WorkingHours.WEEKDAYS:
            is_weekend = weekday >= 5
            WorkingHours.objects.update_or_create(
                weekday=weekday,
                defaults={
                    "is_day_off": False,
                    "open_time": "10:00" if is_weekend else "09:00",
                    "close_time": "17:00" if is_weekend else "20:00",
                },
            )

        self.stdout.write(
            f"  SiteSettings: {'створено' if created else 'вже було'} "
            f"(курс {obj.usd_rate}, округлення «{obj.get_price_rounding_display()}»), "
            f"WorkingHours: 7"
        )
        return obj

    # -- дерево категорій ----------------------------------------------------
    def _seed_categories(self) -> dict[str, Category]:
        # (ключ, external_id, назва_uk, назва_ru, батько, емблема)
        tree: list[tuple[str, str, str, str, str | None, str]] = [
            ("av", "5609710", "Аудіо-відео", "Аудио-видео", None, "M2 5h20v12H2zM8 21h8"),
            ("tv", "5609711", "Телевізори", "Телевизоры", "av", ""),
            ("audio", "5609712", "Аудіотехніка", "Аудиотехника", "av", ""),
            (
                "bi",
                "5609720",
                "Вбудована техніка",
                "Встраиваемая техника",
                None,
                "M4 3h16v18H4zM4 9h16M8 6h.01",
            ),
            ("hob", "5609721", "Варильні поверхні", "Варочные поверхности", "bi", ""),
            ("oven", "5609722", "Духові шафи", "Духовые шкафы", "bi", ""),
            ("hood", "5609723", "Витяжки", "Вытяжки", "bi", ""),
            (
                "bifridge",
                "5609724",
                "Вбудовані холодильники",
                "Встраиваемые холодильники",
                "bi",
                "",
            ),
            (
                "big",
                "5609730",
                "Велика побутова техніка",
                "Крупная бытовая техника",
                None,
                "M5 2h14v20H5zM5 10h14M9 6h.01M9 15h.01",
            ),
            ("fridges", "5609731", "Холодильники", "Холодильники", "big", ""),
            ("washers", "5609732", "Пральні машини", "Стиральные машины", "big", ""),
            ("dish", "5609733", "Посудомийні машини", "Посудомоечные машины", "big", ""),
            ("dryers", "5609734", "Сушильні машини", "Сушильные машины", "big", ""),
            (
                "small",
                "5609740",
                "Дрібна побутова техніка",
                "Мелкая бытовая техника",
                None,
                "M6 3h12l-2 18H8zM9 8h6",
            ),
            ("kettles", "5609741", "Чайники електричні", "Чайники электрические", "small", ""),
            ("micro", "5609742", "Мікрохвильові печі", "Микроволновые печи", "small", ""),
            ("vacuum", "5609743", "Пилососи", "Пылесосы", "small", ""),
            (
                "climate",
                "5609750",
                "Кліматичне обладнання",
                "Климатическое оборудование",
                None,
                "M3 6h18v7H3zM6 16h12M6 19h8",
            ),
            ("ac", "5609751", "Кондиціонери", "Кондиционеры", "climate", ""),
            ("heaters", "5609752", "Обігрівачі", "Обогреватели", "climate", ""),
            (
                "beauty",
                "5609760",
                "Краса і догляд",
                "Красота и уход",
                None,
                "M12 2a5 5 0 0 1 5 5v3H7V7a5 5 0 0 1 5-5zM9 10v12M15 10v12",
            ),
            (
                "cookware",
                "5609770",
                "Посуд",
                "Посуда",
                None,
                "M4 8h16v6a6 6 0 0 1-6 6h-4a6 6 0 0 1-6-6zM20 9h2v4h-2",
            ),
            (
                "gen",
                "5609780",
                "Генератори",
                "Генераторы",
                None,
                "M4 7h16v10H4zM8 17v3M16 17v3M12 9v6",
            ),
            (
                "discount",
                "5609790",
                "Уцінка",
                "Уценка",
                None,
                "M7 7h.01M7 3h5l9 9-9 9-9-9V5a2 2 0 0 1 2-2z",
            ),
        ]
        # Габарити за замовчуванням на категорію (ADR-021): якщо в товару немає своїх —
        # НП порахує доставку по них, а не по «2 кг без габаритів» (різниця буває 5.5×).
        default_dims = {
            "tv": (130.0, 80.0, 20.0, "25.000"),
            "fridges": (65.0, 190.0, 70.0, "70.000"),
            "bifridge": (62.0, 180.0, 60.0, "62.000"),
            "washers": (65.0, 90.0, 65.0, "72.000"),
            "dish": (65.0, 90.0, 65.0, "48.000"),
            "dryers": (65.0, 90.0, 65.0, "55.000"),
            "oven": (65.0, 65.0, 65.0, "38.000"),
            "hob": (65.0, 15.0, 55.0, "12.000"),
            "hood": (65.0, 30.0, 55.0, "14.000"),
            "kettles": (25.0, 30.0, 25.0, "1.500"),
            "micro": (55.0, 40.0, 45.0, "14.000"),
            "vacuum": (45.0, 40.0, 30.0, "8.000"),
            "ac": (100.0, 40.0, 40.0, "35.000"),
            "heaters": (80.0, 50.0, 25.0, "10.000"),
            "beauty": (30.0, 20.0, 15.0, "1.200"),
            "cookware": (35.0, 20.0, 35.0, "3.000"),
            "gen": (70.0, 60.0, 55.0, "60.000"),
        }

        cats: dict[str, Category] = {}
        for i, (key, ext_id, name_uk, name_ru, parent_key, glyph) in enumerate(tree):
            w, h, d, weight = default_dims.get(key, (None, None, None, None))
            icon = ensure_icon(key, f'<path d="{glyph}"/>', "#E45C2A") if glyph else ""
            cat, _ = Category.objects.update_or_create(
                external_id=ext_id,
                defaults={
                    "name_uk": name_uk,
                    "name_ru": name_ru,
                    "slug_uk": translit_slug(name_uk),
                    "slug_ru": translit_slug(name_ru),
                    "parent": cats.get(parent_key) if parent_key else None,
                    "is_active": True,
                    "sort_order": i * 10,
                    "icon": icon,
                    "show_in_megamenu": True,
                    # «Уцінка» — ВІРТУАЛЬНА категорія: власних товарів через FK не має,
                    # її лістинг = filter_tokens @> ["cond:2"] (ADR-023). Той самий уцінений
                    # холодильник лишається в «Холодильники» з усіма фільтрами.
                    "virtual_tokens": ["cond:2"] if key == "discount" else [],
                    "default_width_cm": w,
                    "default_height_cm": h,
                    "default_depth_cm": d,
                    "default_weight_kg": Decimal(weight) if weight else None,
                },
            )
            cats[key] = cat

        roots = sum(1 for c in cats.values() if c.depth == 0)
        self.stdout.write(
            f"  Категорії: {len(cats)} (коренів {roots}, "
            f"віртуальних {sum(1 for c in cats.values() if c.virtual_tokens)})"
        )
        return cats

    # -- бренди / країни -----------------------------------------------------
    def _seed_dictionaries(self) -> tuple[dict[str, Brand], dict[str, Country]]:
        brand_rows = [
            ("bosch", "Bosch", ["BOSCH", "Бош"]),
            ("samsung", "Samsung", ["SAMSUNG", "Самсунг"]),
            ("lg", "LG", ["LG Electronics", "ЛДЖИ", "Элджи"]),
            ("gorenje", "Gorenje", ["GORENJE", "Горенє", "Горенье"]),
            ("electrolux", "Electrolux", ["ELECTROLUX", "Електролюкс"]),
            ("whirlpool", "Whirlpool", ["WHIRLPOOL", "Вірлпул"]),
            ("hansa", "Hansa", ["HANSA", "Ханса"]),
            ("philips", "Philips", ["PHILIPS", "Філіпс", "Филипс"]),
            ("tiki", "Tiki", ["TIKI", "Тікі"]),
            ("dreame", "Dreame", ["DREAME", "Дрім"]),
        ]
        brands: dict[str, Brand] = {}
        for slug, name, aliases in brand_rows:
            brands[slug], _ = Brand.objects.update_or_create(
                slug=slug, defaults={"name": name, "aliases": aliases, "is_active": True}
            )

        country_rows = [
            ("SI", "Словенія", "Словения", ["Slovenia", "Словения"]),
            ("DE", "Німеччина", "Германия", ["Germany", "Германия"]),
            ("PL", "Польща", "Польша", ["Poland", "Польша"]),
            ("CN", "Китай", "Китай", ["China", "КНР"]),
            ("TR", "Туреччина", "Турция", ["Turkey", "Türkiye", "Турция"]),
            ("KR", "Південна Корея", "Южная Корея", ["Korea", "Республіка Корея"]),
            ("HU", "Угорщина", "Венгрия", ["Hungary", "Венгрия"]),
            ("RS", "Сербія", "Сербия", ["Serbia", "Сербия"]),
            ("IT", "Італія", "Италия", ["Italy", "Италия"]),
            ("SK", "Словаччина", "Словакия", ["Slovakia", "Словакия"]),
        ]
        countries: dict[str, Country] = {}
        for code, name_uk, name_ru, aliases in country_rows:
            countries[code], _ = Country.objects.update_or_create(
                code=code,
                defaults={
                    "slug": translit_slug(name_uk),
                    "name_uk": name_uk,
                    "name_ru": name_ru,
                    "aliases": aliases,
                },
            )

        self.stdout.write(f"  Бренди: {len(brands)} | Країни: {len(countries)}")
        return brands, countries

    # -- одиниці / групи -----------------------------------------------------
    def _seed_units_and_groups(self) -> tuple[dict[str, Unit], dict[str, AttributeGroup]]:
        # Unit.name — перекладний СЛОВНИК (~25 рядків на весь каталог): перекладаємо один раз,
        # інакше в RU-версії поїде «12 міс» замість «12 мес» і «1200 об/хв» замість «об/мин».
        unit_rows = [
            ("l", "л", "л", ["літр", "литр", "l"]),
            ("mm", "мм", "мм", ["mm", "миллиметр", "мілім"]),
            ("cm", "см", "см", ["cm", "сантиметр"]),
            ("kg", "кг", "кг", ["kg", "килограмм"]),
            ("month", "міс", "мес", ["месяц", "міс.", "мес."]),
            ("w", "Вт", "Вт", ["W", "ватт", "Вт."]),
            ("inch", "дюйм", "дюйм", ['"', "in", "дюймів"]),
            ("rpm", "об/хв", "об/мин", ["rpm", "об/мин", "обертів за хвилину"]),
            ("db", "дБ", "дБ", ["dB", "децибел"]),
            ("pcs", "шт", "шт", ["штук", "pcs", "шт."]),
            ("m2", "м²", "м²", ["кв.м", "m2", "кв. м"]),
        ]
        units: dict[str, Unit] = {}
        for code, name_uk, name_ru, aliases in unit_rows:
            units[code], _ = Unit.objects.update_or_create(
                code=code,
                defaults={"name_uk": name_uk, "name_ru": name_ru, "aliases": aliases},
            )

        # Групи рендеряться як підзаголовки блоку «Характеристики» (INPUTS §2, скріни 169/170).
        group_rows = [
            ("main", "Основні", "Основные", 1),
            ("features", "Функції та можливості", "Функции и возможности", 2),
            ("dimensions", "Габаритні розміри", "Габаритные размеры", 3),
        ]
        groups: dict[str, AttributeGroup] = {}
        for code, name_uk, name_ru, order in group_rows:
            groups[code], _ = AttributeGroup.objects.update_or_create(
                code=code,
                defaults={"name_uk": name_uk, "name_ru": name_ru, "sort_order": order},
            )

        self.stdout.write(f"  Одиниці: {len(units)} | Групи характеристик: {len(groups)}")
        return units, groups

    # -- характеристики ------------------------------------------------------
    def _seed_attributes(
        self,
        units: dict[str, Unit],
        groups: dict[str, AttributeGroup],
        cats: dict[str, Category],
    ) -> tuple[dict[str, Attribute], dict[str, AttributeOption]]:
        """EAV-словник. Назва — БЕЗ одиниці ("Висота", unit=мм), а не "Висота (мм)".

        ⚠️ «Бренд» і «Країна виробництва» — характеристики value_type=STRING і НЕ фільтровані:
           фільтр по бренду/країні працює з токенів brand:*/country:* (вони походять з FK на
           Product). Якби вони були ще й фільтрованими характеристиками, у фасетах з'явилось би
           ДВА «Виробники» з різними лічильниками — класична хвороба старого сайту.
        """
        AW = Attribute.FilterWidget
        AV = Attribute.ValueType
        AR = Attribute.Role
        MAIN, FEAT, DIM = groups["main"], groups["features"], groups["dimensions"]

        # (code, name_uk, name_ru, unit, group, type, filterable, widget, buckets, role, sort,
        #  категорії)
        rows: list[dict[str, Any]] = [
            # --- Основні ---
            {
                "code": "brend",
                "name_uk": "Бренд",
                "name_ru": "Бренд",
                "group": MAIN,
                "value_type": AV.STRING,
                "sort_order": 10,
            },
            {
                "code": "tip-tv",
                "name_uk": "Тип",
                "name_ru": "Тип",
                "group": MAIN,
                "value_type": AV.OPTION,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 20,
                "cats": ["tv"],
            },
            {
                "code": "tip-kholodylnyka",
                "name_uk": "Тип",
                "name_ru": "Тип",
                "group": MAIN,
                "value_type": AV.OPTION,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 20,
                "cats": ["fridges", "bifridge"],
            },
            {
                "code": "tip-pralnoi",
                "name_uk": "Тип",
                "name_ru": "Тип",
                "group": MAIN,
                "value_type": AV.OPTION,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 20,
                "cats": ["washers"],
            },
            {
                "code": "tip-pylososa",
                "name_uk": "Тип",
                "name_ru": "Тип",
                "group": MAIN,
                "value_type": AV.OPTION,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 20,
                "cats": ["vacuum"],
            },
            {
                "code": "tip-poverkhni",
                "name_uk": "Тип",
                "name_ru": "Тип",
                "group": MAIN,
                "value_type": AV.OPTION,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 20,
                "cats": ["hob"],
            },
            {
                "code": "color",
                "name_uk": "Колір виробу",
                "name_ru": "Цвет изделия",
                "group": MAIN,
                "value_type": AV.OPTION,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "role": AR.COLOR,
                "sort_order": 30,
            },
            {
                "code": "control",
                "name_uk": "Тип управління",
                "name_ru": "Тип управления",
                "group": MAIN,
                "value_type": AV.OPTION,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 40,
            },
            {
                "code": "diahonal",
                "name_uk": "Діагональ екрану",
                "name_ru": "Диагональ экрана",
                "unit": units["inch"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "is_filterable": True,
                "filter_widget": AW.RANGE_BUCKETS,
                "range_buckets": [[0, 42], [43, 50], [51, 55], [56, 65], [66, 75], [76, None]],
                "sort_order": 50,
                "cats": ["tv"],
            },
            {
                "code": "rozdilna",
                "name_uk": "Роздільна здатність",
                "name_ru": "Разрешение экрана",
                "group": MAIN,
                "value_type": AV.OPTION,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 52,
                "cats": ["tv"],
            },
            {
                "code": "obiem",
                "name_uk": "Загальний об'єм",
                "name_ru": "Общий объём",
                "unit": units["l"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "is_filterable": True,
                "filter_widget": AW.RANGE_BUCKETS,
                "range_buckets": [[0, 199], [200, 299], [300, 399], [400, None]],
                "sort_order": 54,
                "cats": ["fridges", "bifridge"],
            },
            {
                "code": "obiem-kamery",
                "name_uk": "Об'єм камери",
                "name_ru": "Объём камеры",
                "unit": units["l"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "is_filterable": True,
                "filter_widget": AW.RANGE_BUCKETS,
                "range_buckets": [[0, 19], [20, 24], [25, None]],
                "sort_order": 55,
                "cats": ["micro", "oven"],
            },
            {
                "code": "kamer",
                "name_uk": "Кількість камер",
                "name_ru": "Количество камер",
                "unit": units["pcs"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 56,
                "cats": ["fridges", "bifridge"],
            },
            {
                "code": "energiia",
                "name_uk": "Клас енергоефективності",
                "name_ru": "Класс энергоэффективности",
                "group": MAIN,
                "value_type": AV.OPTION,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 58,
                "cats": ["fridges", "bifridge", "washers", "dish", "dryers", "oven", "ac"],
            },
            {
                "code": "vidzhym",
                "name_uk": "Максимальна швидкість віджиму",
                "name_ru": "Максимальная скорость отжима",
                "unit": units["rpm"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "is_filterable": True,
                "filter_widget": AW.RANGE_BUCKETS,
                "range_buckets": [[800, 1000], [1001, 1200], [1201, 1400], [1401, None]],
                "sort_order": 60,
                "cats": ["washers"],
            },
            {
                "code": "zavantazhennia",
                "name_uk": "Максимальне завантаження білизни",
                "name_ru": "Максимальная загрузка белья",
                "unit": units["kg"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "is_filterable": True,
                "filter_widget": AW.RANGE_BUCKETS,
                "range_buckets": [[0, 6], [7, 8], [9, None]],
                "sort_order": 62,
                "cats": ["washers", "dryers"],
            },
            {
                "code": "programy",
                "name_uk": "Кількість програм",
                "name_ru": "Количество программ",
                "unit": units["pcs"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "sort_order": 64,
                "cats": ["washers", "dish", "dryers"],
            },
            {
                "code": "komplekty",
                "name_uk": "Кількість комплектів посуду",
                "name_ru": "Количество комплектов посуды",
                "unit": units["pcs"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 66,
                "cats": ["dish"],
            },
            {
                "code": "konforky",
                "name_uk": "Кількість конфорок",
                "name_ru": "Количество конфорок",
                "unit": units["pcs"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 68,
                "cats": ["hob"],
            },
            {
                "code": "potuzhnist",
                "name_uk": "Потужність",
                "name_ru": "Мощность",
                "unit": units["w"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "is_filterable": True,
                "filter_widget": AW.RANGE_BUCKETS,
                "range_buckets": [[0, 999], [1000, 1799], [1800, 2199], [2200, None]],
                "sort_order": 70,
                "cats": ["kettles", "micro", "vacuum", "heaters", "beauty", "gen"],
            },
            {
                "code": "shum",
                "name_uk": "Рівень шуму",
                "name_ru": "Уровень шума",
                "unit": units["db"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "is_filterable": True,
                "filter_widget": AW.RANGE_BUCKETS,
                "range_buckets": [[0, 49], [50, 59], [60, None]],
                "sort_order": 72,
                "cats": ["fridges", "washers", "dish", "ac", "vacuum"],
            },
            {
                "code": "ploshcha",
                "name_uk": "Площа приміщення",
                "name_ru": "Площадь помещения",
                "unit": units["m2"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "is_filterable": True,
                "filter_widget": AW.RANGE_BUCKETS,
                "range_buckets": [[0, 20], [21, 35], [36, None]],
                "sort_order": 74,
                "cats": ["ac", "heaters"],
            },
            {
                "code": "krayina-vyrobnytstva",
                "name_uk": "Країна виробництва",
                "name_ru": "Страна производства",
                "group": MAIN,
                "value_type": AV.STRING,
                "sort_order": 90,
            },
            {
                "code": "garantiia",
                "name_uk": "Гарантійний термін",
                "name_ru": "Гарантийный срок",
                "unit": units["month"],
                "group": MAIN,
                "value_type": AV.NUMBER,
                "role": AR.WARRANTY_MONTHS,
                "sort_order": 95,
            },
            # --- Функції та можливості ---
            {
                "code": "no-frost",
                "name_uk": "No Frost",
                "name_ru": "No Frost",
                "group": FEAT,
                "value_type": AV.BOOL,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 10,
                "cats": ["fridges", "bifridge"],
            },
            {
                "code": "invertor",
                "name_uk": "Інверторний двигун",
                "name_ru": "Инверторный двигатель",
                "group": FEAT,
                "value_type": AV.BOOL,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 20,
                "cats": ["fridges", "washers", "ac", "vacuum"],
            },
            {
                "code": "wifi",
                "name_uk": "Wi-Fi / Smart",
                "name_ru": "Wi-Fi / Smart",
                "group": FEAT,
                "value_type": AV.BOOL,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 30,
                "cats": ["tv", "ac", "washers", "vacuum"],
            },
            {
                "code": "dysplei",
                "name_uk": "Дисплей",
                "name_ru": "Дисплей",
                "group": FEAT,
                "value_type": AV.BOOL,
                "is_filterable": True,
                "filter_widget": AW.CHECKBOX,
                "sort_order": 40,
            },
            # --- Габаритні розміри ---
            {
                "code": "vysota",
                "name_uk": "Висота",
                "name_ru": "Высота",
                "unit": units["mm"],
                "group": DIM,
                "value_type": AV.NUMBER,
                "sort_order": 10,
            },
            {
                "code": "shyryna",
                "name_uk": "Ширина",
                "name_ru": "Ширина",
                "unit": units["mm"],
                "group": DIM,
                "value_type": AV.NUMBER,
                "sort_order": 20,
            },
            {
                "code": "hlybyna",
                "name_uk": "Глибина",
                "name_ru": "Глубина",
                "unit": units["mm"],
                "group": DIM,
                "value_type": AV.NUMBER,
                "sort_order": 30,
            },
            {
                "code": "vaga",
                "name_uk": "Вага",
                "name_ru": "Вес",
                "unit": units["kg"],
                "group": DIM,
                "value_type": AV.NUMBER,
                "sort_order": 40,
            },
            # ⚠️ role=PACKAGE_DIMS / PACKAGE_WEIGHT — саме через них (а НЕ через хардкод назв)
            #    заповнюються Product.package_* для калькулятора Нової Пошти.
            {
                "code": "gabaryty-upakovky",
                "name_uk": "Габарити упаковки (ВхШхГ)",
                "name_ru": "Габариты упаковки (ВхШхГ)",
                "unit": units["cm"],
                "group": DIM,
                "value_type": AV.STRING,
                "role": AR.PACKAGE_DIMS,
                "sort_order": 50,
            },
            {
                "code": "vaga-v-upakovtsi",
                "name_uk": "Вага в упаковці",
                "name_ru": "Вес в упаковке",
                "unit": units["kg"],
                "group": DIM,
                "value_type": AV.NUMBER,
                "role": AR.PACKAGE_WEIGHT,
                "sort_order": 60,
            },
        ]

        attrs: dict[str, Attribute] = {}
        for row in rows:
            row = dict(row)
            code = row.pop("code")
            cat_keys = row.pop("cats", None)
            attrs[code], _ = Attribute.objects.update_or_create(code=code, defaults=row)
            # Порожньо = у всіх категоріях (бренд, колір, гарантія, габарити).
            attrs[code].categories.set([cats[k] for k in cat_keys] if cat_keys else [])

        # (attr, slug, value_uk, value_ru, swatch_hex, sort)
        option_rows = [
            ("tip-tv", "led", "LED", "LED", "", 10),
            ("tip-tv", "qled", "QLED", "QLED", "", 20),
            ("tip-tv", "oled", "OLED", "OLED", "", 30),
            ("tip-kholodylnyka", "odnokamernyi", "Однокамерний", "Однокамерный", "", 10),
            ("tip-kholodylnyka", "dvokamernyi", "Двокамерний", "Двухкамерный", "", 20),
            ("tip-kholodylnyka", "side-by-side", "Side-by-Side", "Side-by-Side", "", 30),
            ("tip-pralnoi", "frontalna", "Фронтальна", "Фронтальная", "", 10),
            ("tip-pralnoi", "vertykalna", "Вертикальна", "Вертикальная", "", 20),
            ("tip-pylososa", "robot", "Робот-пилосос", "Робот-пылесос", "", 10),
            ("tip-pylososa", "bezdrotovyi", "Бездротовий", "Беспроводной", "", 20),
            ("tip-pylososa", "z-mishkom", "З мішком", "С мешком", "", 30),
            ("tip-poverkhni", "gazova", "Газова", "Газовая", "", 10),
            ("tip-poverkhni", "elektrychna", "Електрична", "Электрическая", "", 20),
            ("tip-poverkhni", "induktsiina", "Індукційна", "Индукционная", "", 30),
            ("color", "bilyi", "Білий", "Белый", "#FFFFFF", 10),
            ("color", "chornyi", "Чорний", "Чёрный", "#1A1A1A", 20),
            ("color", "sriblyastyi", "Сріблястий", "Серебристый", "#C0C0C0", 30),
            ("color", "nerzhaviiuca-stal", "Нержавіюча сталь", "Нержавеющая сталь", "#B5B8BB", 40),
            ("color", "hrafitovyi", "Графітовий", "Графитовый", "#4A4A4A", 50),
            ("color", "slonova-kistka", "Слонова кістка", "Слоновая кость", "#F2E7D5", 60),
            ("control", "sensorne", "Сенсорне", "Сенсорное", "", 10),
            ("control", "elektronne", "Електронне", "Электронное", "", 20),
            ("control", "mekhanichne", "Механічне", "Механическое", "", 30),
            ("control", "knopkove", "Кнопкове", "Кнопочное", "", 40),
            ("control", "povorotni", "Поворотні перемикачі", "Поворотные переключатели", "", 50),
            ("rozdilna", "8k-uhd", "8K UHD", "8K UHD", "", 10),
            ("rozdilna", "4k-uhd", "4K UHD", "4K UHD", "", 20),
            ("rozdilna", "full-hd", "Full HD", "Full HD", "", 30),
            ("energiia", "a-plus-plus-plus", "A+++", "A+++", "", 10),
            ("energiia", "a-plus-plus", "A++", "A++", "", 20),
            ("energiia", "a-plus", "A+", "A+", "", 30),
            ("energiia", "a", "A", "A", "", 40),
            ("energiia", "b", "B", "B", "", 50),
            ("energiia", "c", "C", "C", "", 60),
            ("energiia", "d", "D", "D", "", 70),
            ("energiia", "e", "E", "E", "", 80),
            ("energiia", "f", "F", "F", "", 90),
        ]
        options: dict[str, AttributeOption] = {}
        for attr_code, slug, value_uk, value_ru, hex_, order in option_rows:
            opt, _ = AttributeOption.objects.update_or_create(
                attribute=attrs[attr_code],
                slug=slug,
                defaults={
                    "value_uk": value_uk,
                    "value_ru": value_ru,
                    "swatch_hex": hex_,
                    "sort_order": order,
                },
            )
            options[f"{attr_code}:{slug}"] = opt

        self.stdout.write(
            f"  Характеристики: {len(attrs)} "
            f"(фільтрованих {sum(1 for a in attrs.values() if a.is_filterable)}, "
            f"числових {sum(1 for a in attrs.values() if a.value_type == AV.NUMBER)}), "
            f"значень-опцій: {len(options)}"
        )
        return attrs, options

    # -- товари --------------------------------------------------------------
    def _product_rows(self) -> list[dict[str, Any]]:
        """~65 товарів. Валюта, наявність, стан, оплата частинами — свідомо різні.

        Ціни в USD (`cur="USD"`) — щоб було ВИДНО перерахунок курсу: price рахує
        complex_price_uah() з usd_rate і markup_percent, а usd_rate_used фіксує курс.
        """
        D = Decimal
        IN, ON, OUT = Availability.IN_STOCK, Availability.ON_ORDER, Availability.OUT_OF_STOCK

        vac_desc = (
            "<h2>Робот-пилосос із лазерною навігацією</h2>"
            "<p>Будує карту приміщення за один прохід і прибирає кімнату за кімнатою, "
            "не пропускаючи ділянок під меблями.</p>"
            '<figure><img src="/media/editor/demo/vacuum-map.png" alt="Карта приміщення"/>'
            "<figcaption>Карта будується автоматично</figcaption></figure>"
            "<h3>Вологе прибирання</h3>"
            "<p>Модуль швабри вмикається без зняття контейнера — достатньо долити води.</p>"
            '<figure><img src="/media/editor/demo/vacuum-mop.png" alt="Вологе прибирання"/>'
            "</figure>"
            "<ul><li>Автоматичне очищення бази</li><li>Керування зі смартфона</li>"
            "<li>Робота до 180 хвилин</li></ul>"
        )
        tv_desc = (
            "<h2>Crystal UHD — чистий колір без напівтонів</h2>"
            "<p>Процесор Crystal 4K аналізує сцену й підсилює контраст у темних ділянках, "
            "тому нічні сцени лишаються деталізованими.</p>"
            "<h3>Smart Hub</h3>"
            "<p>Усі сервіси — Netflix, YouTube, Megogo — на одному екрані.</p>"
        )

        rows: list[dict[str, Any]] = [
            # ================= ТЕЛЕВІЗОРИ =================
            {
                "sku": "CMPX-100101",
                "cat": "tv",
                "brand": "samsung",
                "country": "PL",
                "cur": "UAH",
                "base": D("15999"),
                "avail": IN,
                "qty": 6,
                "name": "Телевізор Samsung UE43CU7100UXUA",
                "name_ru": "Телевизор Samsung UE43CU7100UXUA",
                "short": "43 дюйми, 4K UHD, Tizen OS.",
                "short_ru": "43 дюйма, 4K UHD, Tizen OS.",
                "specs": {
                    "tip-tv": "led",
                    "diahonal": 43,
                    "rozdilna": "4k-uhd",
                    "color": "chornyi",
                    "wifi": True,
                    "garantiia": 24,
                    **dims(560, 964, 60, 8.2, pad_cm=12),
                },
            },
            # --- VariantGroup BUTTONS: одна модель у п'яти діагоналях ---
            {
                "sku": "CMPX-100110",
                "cat": "tv",
                "brand": "samsung",
                "country": "PL",
                "cur": "UAH",
                "base": D("18999"),
                "old": D("21999"),
                "avail": IN,
                "qty": 4,
                "inst": True,
                "inst_max": 6,
                "name": "Телевізор Samsung UE50DU7100UXUA",
                "name_ru": "Телевизор Samsung UE50DU7100UXUA",
                "short": "50 дюймів, 4K UHD, HDR10+.",
                "short_ru": "50 дюймов, 4K UHD, HDR10+.",
                "desc": tv_desc,
                "specs": {
                    "tip-tv": "led",
                    "diahonal": 50,
                    "rozdilna": "4k-uhd",
                    "color": "chornyi",
                    "wifi": True,
                    "garantiia": 24,
                    **dims(650, 1118, 60, 10.4, pad_cm=12),
                },
            },
            {
                "sku": "CMPX-100111",
                "cat": "tv",
                "brand": "samsung",
                "country": "PL",
                "cur": "UAH",
                "base": D("21999"),
                "avail": IN,
                "qty": 3,
                "inst": True,
                "inst_max": 6,
                "name": "Телевізор Samsung UE55DU7100UXUA",
                "name_ru": "Телевизор Samsung UE55DU7100UXUA",
                "short": "55 дюймів, 4K UHD, HDR10+.",
                "short_ru": "55 дюймов, 4K UHD, HDR10+.",
                "specs": {
                    "tip-tv": "led",
                    "diahonal": 55,
                    "rozdilna": "4k-uhd",
                    "color": "chornyi",
                    "wifi": True,
                    "garantiia": 24,
                    **dims(712, 1233, 60, 12.6, pad_cm=12),
                },
            },
            {
                "sku": "CMPX-100112",
                "cat": "tv",
                "brand": "samsung",
                "country": "PL",
                "cur": "UAH",
                "base": D("27999"),
                "old": D("31999"),
                "avail": IN,
                "qty": 2,
                "featured": True,
                "inst": True,
                "inst_max": 12,
                "name": "Телевізор Samsung UE65DU7100UXUA",
                "name_ru": "Телевизор Samsung UE65DU7100UXUA",
                "short": "65 дюймів, 4K UHD, HDR10+.",
                "short_ru": "65 дюймов, 4K UHD, HDR10+.",
                "specs": {
                    "tip-tv": "led",
                    "diahonal": 65,
                    "rozdilna": "4k-uhd",
                    "color": "chornyi",
                    "wifi": True,
                    "garantiia": 24,
                    **dims(836, 1452, 60, 18.5, pad_cm=14),
                },
            },
            {
                "sku": "CMPX-100113",
                "cat": "tv",
                "brand": "samsung",
                "country": "PL",
                "cur": "UAH",
                "base": D("41999"),
                "avail": ON,
                "lead": 5,
                "qty": 0,
                "inst": True,
                "inst_max": 12,
                "name": "Телевізор Samsung UE75DU7100UXUA",
                "short": "75 дюймів, 4K UHD, HDR10+.",
                "specs": {
                    "tip-tv": "led",
                    "diahonal": 75,
                    "rozdilna": "4k-uhd",
                    "color": "chornyi",
                    "wifi": True,
                    "garantiia": 24,
                    **dims(964, 1673, 60, 26.8, pad_cm=16),
                },
            },
            {
                "sku": "CMPX-100114",
                "cat": "tv",
                "brand": "samsung",
                "country": "PL",
                "cur": "UAH",
                "base": D("62999"),
                "avail": OUT,
                "qty": 0,
                "name": "Телевізор Samsung UE85DU7100UXUA",
                "short": "85 дюймів, 4K UHD, HDR10+.",
                "specs": {
                    "tip-tv": "led",
                    "diahonal": 85,
                    "rozdilna": "4k-uhd",
                    "color": "chornyi",
                    "wifi": True,
                    "garantiia": 24,
                    **dims(1090, 1895, 60, 35.1, pad_cm=18),
                },
            },
            {
                "sku": "CMPX-100102",
                "cat": "tv",
                "brand": "lg",
                "country": "KR",
                "cur": "USD",
                "base": D("449.00"),
                "markup": D("12.00"),
                "avail": IN,
                "qty": 5,
                "name": "Телевізор LG 55UT81006LA",
                "name_ru": "Телевизор LG 55UT81006LA",
                "short": "55 дюймів, 4K UHD, webOS 24.",
                "short_ru": "55 дюймов, 4K UHD, webOS 24.",
                "specs": {
                    "tip-tv": "led",
                    "diahonal": 55,
                    "rozdilna": "4k-uhd",
                    "color": "chornyi",
                    "wifi": True,
                    "garantiia": 24,
                    **dims(714, 1240, 80, 12.9, pad_cm=12),
                },
            },
            {
                "sku": "CMPX-100103",
                "cat": "tv",
                "brand": "lg",
                "country": "KR",
                "cur": "USD",
                "base": D("1249.00"),
                "markup": D("10.00"),
                "avail": IN,
                "qty": 1,
                "featured": True,
                "inst": True,
                "inst_max": 24,
                "name": "Телевізор LG OLED55C4RLA",
                "name_ru": "Телевизор LG OLED55C4RLA",
                "short": "55 дюймів, OLED evo, 144 Гц.",
                "short_ru": "55 дюймов, OLED evo, 144 Гц.",
                "specs": {
                    "tip-tv": "oled",
                    "diahonal": 55,
                    "rozdilna": "4k-uhd",
                    "color": "hrafitovyi",
                    "wifi": True,
                    "garantiia": 24,
                    **dims(707, 1226, 45, 13.9, pad_cm=12),
                },
            },
            {
                "sku": "CMPX-100104",
                "cat": "tv",
                "brand": "philips",
                "country": "HU",
                "cur": "USD",
                "base": D("349.00"),
                "markup": D("12.00"),
                "avail": ON,
                "lead": 7,
                "qty": 0,
                "name": "Телевізор Philips 43PUS8009/12",
                "short": "43 дюйми, 4K UHD, Ambilight.",
                "specs": {
                    "tip-tv": "led",
                    "diahonal": 43,
                    "rozdilna": "4k-uhd",
                    "color": "chornyi",
                    "wifi": True,
                    "garantiia": 24,
                    **dims(566, 963, 70, 8.9, pad_cm=12),
                },
            },
            # ================= АУДІОТЕХНІКА =================
            {
                "sku": "CMPX-100120",
                "cat": "audio",
                "brand": "samsung",
                "country": "CN",
                "cur": "UAH",
                "base": D("7999"),
                "old": D("9499"),
                "avail": IN,
                "qty": 7,
                "name": "Саундбар Samsung HW-B550",
                "name_ru": "Саундбар Samsung HW-B550",
                "short": "2.1 канали, 410 Вт, бездротовий сабвуфер.",
                "short_ru": "2.1 канала, 410 Вт, беспроводной сабвуфер.",
                "specs": {
                    "color": "chornyi",
                    "control": "knopkove",
                    "dysplei": True,
                    "garantiia": 12,
                    **dims(64, 860, 85, 4.9),
                },
            },
            {
                "sku": "CMPX-100121",
                "cat": "audio",
                "brand": "philips",
                "country": "CN",
                "cur": "UAH",
                "base": D("1299"),
                "avail": IN,
                "qty": 15,
                "name": "Навушники Philips TAH4205BK",
                "short": "Бездротові, до 29 годин роботи.",
                "specs": {
                    "color": "chornyi",
                    "garantiia": 12,
                    **dims(190, 165, 75, 0.19, pad_cm=3),
                },
            },
            {
                "sku": "CMPX-100122",
                "cat": "audio",
                "brand": "lg",
                "country": "CN",
                "cur": "USD",
                "base": D("129.00"),
                "markup": D("15.00"),
                "avail": IN,
                "qty": 4,
                "name": "Портативна колонка LG XBOOM Go XG7",
                "short": "Захист IP67, до 24 годин автономності.",
                "specs": {
                    "color": "chornyi",
                    "dysplei": False,
                    "garantiia": 12,
                    **dims(233, 105, 105, 1.4, pad_cm=3),
                },
            },
            # ================= ВБУДОВАНА: ВАРИЛЬНІ ПОВЕРХНІ =================
            {
                "sku": "CMPX-100201",
                "cat": "hob",
                "brand": "bosch",
                "country": "DE",
                "cur": "UAH",
                "base": D("12999"),
                "avail": IN,
                "qty": 3,
                "featured": True,
                "name": "Варильна поверхня Bosch PUE611BB5E",
                "name_ru": "Варочная поверхность Bosch PUE611BB5E",
                "short": "Індукційна, 4 конфорки, TouchSelect.",
                "short_ru": "Индукционная, 4 конфорки, TouchSelect.",
                "specs": {
                    "tip-poverkhni": "induktsiina",
                    "konforky": 4,
                    "color": "chornyi",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(51, 592, 522, 10.2, pad_cm=8),
                },
            },
            {
                "sku": "CMPX-100202",
                "cat": "hob",
                "brand": "gorenje",
                "country": "SI",
                "cur": "UAH",
                "base": D("6499"),
                "avail": IN,
                "qty": 5,
                "name": "Варильна поверхня Gorenje GKT641SYW",
                "short": "Газова, 4 конфорки, чавунні решітки.",
                "specs": {
                    "tip-poverkhni": "gazova",
                    "konforky": 4,
                    "color": "bilyi",
                    "control": "povorotni",
                    "garantiia": 24,
                    **dims(95, 600, 510, 9.6, pad_cm=8),
                },
            },
            {
                "sku": "CMPX-100203",
                "cat": "hob",
                "brand": "hansa",
                "country": "PL",
                "cur": "UAH",
                "base": D("8999"),
                "avail": ON,
                "lead": 10,
                "qty": 0,
                "name": "Варильна поверхня Hansa BHIS67303",
                "short": "Індукційна, 3 конфорки, Booster.",
                "specs": {
                    "tip-poverkhni": "induktsiina",
                    "konforky": 3,
                    "color": "chornyi",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(52, 590, 520, 8.8, pad_cm=8),
                },
            },
            # ================= ВБУДОВАНА: ДУХОВІ ШАФИ =================
            {
                "sku": "CMPX-100211",
                "cat": "oven",
                "brand": "bosch",
                "country": "DE",
                "cur": "UAH",
                "base": D("21999"),
                "old": D("24999"),
                "avail": IN,
                "qty": 2,
                "featured": True,
                "inst": True,
                "inst_max": 12,
                "name": "Духова шафа Bosch HBG5780S6",
                "name_ru": "Духовой шкаф Bosch HBG5780S6",
                "short": "71 л, піроліз, конвекція 4D.",
                "short_ru": "71 л, пиролиз, конвекция 4D.",
                "specs": {
                    "obiem-kamery": 71,
                    "energiia": "a-plus",
                    "color": "nerzhaviiuca-stal",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(595, 594, 548, 37.5, pad_cm=8),
                },
            },
            {
                "sku": "CMPX-100212",
                "cat": "oven",
                "brand": "electrolux",
                "country": "IT",
                "cur": "UAH",
                "base": D("13499"),
                "avail": IN,
                "qty": 4,
                "name": "Духова шафа Electrolux OEF5C50Z",
                "short": "72 л, гаряче повітря, каталітична емаль.",
                "specs": {
                    "obiem-kamery": 72,
                    "energiia": "a",
                    "color": "nerzhaviiuca-stal",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(594, 594, 568, 32.0, pad_cm=8),
                },
            },
            {
                "sku": "CMPX-100213",
                "cat": "oven",
                "brand": "gorenje",
                "country": "SI",
                "cur": "UAH",
                "base": D("11999"),
                "old": D("13999"),
                "avail": IN,
                "qty": 1,
                "cond": Condition.REFURBISHED,
                "note": "Відновлений виробником, повна гарантія 12 місяців.",
                "note_ru": "Восстановлен производителем, полная гарантия 12 месяцев.",
                "name": "Духова шафа Gorenje BO6737E02X",
                "short": "77 л, AquaClean, 8 режимів.",
                "specs": {
                    "obiem-kamery": 77,
                    "energiia": "a",
                    "color": "nerzhaviiuca-stal",
                    "control": "povorotni",
                    "garantiia": 12,
                    **dims(595, 597, 547, 31.4, pad_cm=8),
                },
            },
            # ================= ВБУДОВАНА: ВИТЯЖКИ =================
            {
                "sku": "CMPX-100221",
                "cat": "hood",
                "brand": "bosch",
                "country": "DE",
                "cur": "UAH",
                "base": D("9999"),
                "avail": IN,
                "qty": 3,
                "name": "Витяжка Bosch DWB67IM50",
                "name_ru": "Вытяжка Bosch DWB67IM50",
                "short": "Похила, 730 м³/год, сенсорне керування.",
                "short_ru": "Наклонная, 730 м³/ч, сенсорное управление.",
                "specs": {
                    "color": "nerzhaviiuca-stal",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(628, 600, 499, 14.8, pad_cm=8),
                },
            },
            {
                "sku": "CMPX-100222",
                "cat": "hood",
                "brand": "electrolux",
                "country": "IT",
                "cur": "UAH",
                "base": D("4299"),
                "avail": IN,
                "qty": 6,
                "name": "Витяжка Electrolux LFP316FB",
                "short": "Телескопічна, 320 м³/год.",
                "specs": {
                    "color": "chornyi",
                    "control": "mekhanichne",
                    "garantiia": 24,
                    **dims(180, 600, 300, 5.6, pad_cm=6),
                },
            },
            {
                "sku": "CMPX-100223",
                "cat": "hood",
                "brand": "hansa",
                "country": "PL",
                "cur": "UAH",
                "base": D("3199"),
                "old": D("4199"),
                "avail": IN,
                "qty": 1,
                "cond": Condition.DISCOUNTED,
                "note": "Вітринний зразок: подряпина на боковій панелі.",
                "note_ru": "Витринный образец: царапина на боковой панели.",
                "name": "Витяжка Hansa OTP6231IH",
                "short": "Купольна, 380 м³/год.",
                "specs": {
                    "color": "nerzhaviiuca-stal",
                    "control": "knopkove",
                    "garantiia": 12,
                    **dims(1130, 600, 500, 8.9, pad_cm=6),
                },
            },
            # ================= ВБУДОВАНІ ХОЛОДИЛЬНИКИ =================
            {
                "sku": "CMPX-100231",
                "cat": "bifridge",
                "brand": "bosch",
                "country": "DE",
                "cur": "USD",
                "base": D("899.00"),
                "markup": D("12.00"),
                "avail": IN,
                "qty": 2,
                "inst": True,
                "inst_max": 12,
                "name": "Вбудований холодильник Bosch KIV87NSE0",
                "name_ru": "Встраиваемый холодильник Bosch KIV87NSE0",
                "short": "270 л, LowFrost, 2 камери.",
                "short_ru": "270 л, LowFrost, 2 камеры.",
                "specs": {
                    "tip-kholodylnyka": "dvokamernyi",
                    "obiem": 270,
                    "kamer": 2,
                    "energiia": "a-plus-plus",
                    "no-frost": False,
                    "invertor": True,
                    "color": "bilyi",
                    "control": "elektronne",
                    "garantiia": 24,
                    **dims(1772, 541, 545, 58.0),
                },
            },
            {
                "sku": "CMPX-100232",
                "cat": "bifridge",
                "brand": "whirlpool",
                "country": "IT",
                "cur": "UAH",
                "base": D("24999"),
                "avail": ON,
                "lead": 14,
                "qty": 0,
                "name": "Вбудований холодильник Whirlpool ART 9811/A++/SF",
                "short": "303 л, No Frost, 2 камери.",
                "specs": {
                    "tip-kholodylnyka": "dvokamernyi",
                    "obiem": 303,
                    "kamer": 2,
                    "energiia": "a-plus-plus",
                    "no-frost": True,
                    "invertor": False,
                    "color": "bilyi",
                    "control": "elektronne",
                    "garantiia": 24,
                    **dims(1770, 540, 545, 61.5),
                },
            },
            # ================= ХОЛОДИЛЬНИКИ =================
            {
                "sku": "CMPX-100301",
                "cat": "fridges",
                "brand": "gorenje",
                "country": "SI",
                "cur": "UAH",
                "base": D("27445"),
                "old": D("31900"),
                "avail": IN,
                "qty": 4,
                "featured": True,
                "inst": True,
                "inst_max": 6,
                "name": "Холодильник Gorenje NRK6202AW4",
                "name_ru": "Холодильник Gorenje NRK6202AW4",
                "short": "Двокамерний холодильник з No Frost, 331 л.",
                "short_ru": "Двухкамерный холодильник с No Frost, 331 л.",
                "specs": {
                    "tip-kholodylnyka": "dvokamernyi",
                    "obiem": 331,
                    "kamer": 2,
                    "energiia": "a-plus-plus",
                    "no-frost": True,
                    "invertor": True,
                    "shum": 39,
                    "color": "bilyi",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(2000, 600, 594, 68.0),
                },
            },
            {
                "sku": "CMPX-100302",
                "cat": "fridges",
                "brand": "bosch",
                "country": "DE",
                "cur": "USD",
                "base": D("620.00"),
                "markup": D("12.00"),
                "avail": IN,
                "qty": 2,
                "name": "Холодильник Bosch KGN39VLCT",
                "name_ru": "Холодильник Bosch KGN39VLCT",
                "short": "No Frost, 363 л, нержавіюча сталь.",
                "short_ru": "No Frost, 363 л, нержавеющая сталь.",
                "specs": {
                    "tip-kholodylnyka": "dvokamernyi",
                    "obiem": 363,
                    "kamer": 2,
                    "energiia": "a-plus-plus",
                    "no-frost": True,
                    "invertor": True,
                    "shum": 39,
                    "color": "nerzhaviiuca-stal",
                    "control": "elektronne",
                    "garantiia": 24,
                    **dims(2030, 600, 660, 75.0),
                },
            },
            {
                "sku": "CMPX-100303",
                "cat": "fridges",
                "brand": "samsung",
                "country": "PL",
                "cur": "UAH",
                "base": D("32999"),
                "old": D("36999"),
                "avail": IN,
                "qty": 3,
                "inst": True,
                "inst_max": 12,
                "name": "Холодильник Samsung RB38T676FSA/UA",
                "name_ru": "Холодильник Samsung RB38T676FSA/UA",
                "short": "385 л, No Frost, інверторний компресор.",
                "short_ru": "385 л, No Frost, инверторный компрессор.",
                "specs": {
                    "tip-kholodylnyka": "dvokamernyi",
                    "obiem": 385,
                    "kamer": 2,
                    "energiia": "a-plus-plus-plus",
                    "no-frost": True,
                    "invertor": True,
                    "shum": 36,
                    "color": "nerzhaviiuca-stal",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(2030, 595, 650, 78.0),
                },
            },
            {
                "sku": "CMPX-100304",
                "cat": "fridges",
                "brand": "lg",
                "country": "KR",
                "cur": "USD",
                "base": D("1099.00"),
                "markup": D("10.00"),
                "avail": ON,
                "lead": 7,
                "qty": 0,
                "inst": True,
                "inst_max": 24,
                "name": "Холодильник LG GC-B257JEYV",
                "short": "Side-by-Side, 647 л, Total No Frost.",
                "specs": {
                    "tip-kholodylnyka": "side-by-side",
                    "obiem": 647,
                    "kamer": 2,
                    "energiia": "a-plus",
                    "no-frost": True,
                    "invertor": True,
                    "shum": 40,
                    "color": "sriblyastyi",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(1790, 914, 736, 116.0, pad_cm=8),
                },
            },
            {
                "sku": "CMPX-100305",
                "cat": "fridges",
                "brand": "whirlpool",
                "country": "IT",
                "cur": "UAH",
                "base": D("29999"),
                "avail": OUT,
                "qty": 0,
                "name": "Холодильник Whirlpool W7X 82O OX",
                "short": "335 л, No Frost, 6th Sense.",
                "specs": {
                    "tip-kholodylnyka": "dvokamernyi",
                    "obiem": 335,
                    "kamer": 2,
                    "energiia": "a-plus-plus",
                    "no-frost": True,
                    "invertor": True,
                    "shum": 38,
                    "color": "nerzhaviiuca-stal",
                    "control": "elektronne",
                    "garantiia": 24,
                    **dims(1914, 595, 655, 74.0),
                },
            },
            {
                "sku": "CMPX-100306",
                "cat": "fridges",
                "brand": "gorenje",
                "country": "RS",
                "cur": "UAH",
                "base": D("19450"),
                "old": D("24300"),
                "avail": IN,
                "qty": 1,
                "cond": Condition.DISCOUNTED,
                "note": "Вітринний зразок, подряпина на боковій панелі.",
                "note_ru": "Витринный образец, царапина на боковой панели.",
                "name": "Холодильник Gorenje RK6192EW4",
                "name_ru": "Холодильник Gorenje RK6192EW4",
                "short": "Двокамерний холодильник, 324 л.",
                "short_ru": "Двухкамерный холодильник, 324 л.",
                "specs": {
                    "tip-kholodylnyka": "dvokamernyi",
                    "obiem": 324,
                    "kamer": 2,
                    "energiia": "a-plus",
                    "no-frost": False,
                    "invertor": False,
                    "shum": 41,
                    "color": "bilyi",
                    "control": "mekhanichne",
                    "garantiia": 12,
                    **dims(1850, 600, 640, 58.0),
                },
            },
            {
                "sku": "CMPX-100307",
                "cat": "fridges",
                "brand": "electrolux",
                "country": "HU",
                "cur": "UAH",
                "base": D("34999"),
                "avail": IN,
                "qty": 2,
                "name": "Холодильник Electrolux LNT7ME34G1",
                "short": "360 л, TwinTech No Frost, інвертор.",
                "specs": {
                    "tip-kholodylnyka": "dvokamernyi",
                    "obiem": 360,
                    "kamer": 2,
                    "energiia": "a-plus-plus",
                    "no-frost": True,
                    "invertor": True,
                    "shum": 37,
                    "color": "hrafitovyi",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(1860, 595, 650, 71.0),
                },
            },
            # ================= ПРАЛЬНІ МАШИНИ =================
            {
                "sku": "CMPX-100311",
                "cat": "washers",
                "brand": "samsung",
                "country": "PL",
                "cur": "UAH",
                "base": D("18999"),
                "avail": ON,
                "lead": 7,
                "qty": 0,
                "name": "Пральна машина Samsung WW70AGP24AE",
                "name_ru": "Стиральная машина Samsung WW70AGP24AE",
                "short": "Завантаження 7 кг, 1200 об/хв.",
                "short_ru": "Загрузка 7 кг, 1200 об/мин.",
                "specs": {
                    "tip-pralnoi": "frontalna",
                    "zavantazhennia": 7,
                    "vidzhym": 1200,
                    "programy": 14,
                    "energiia": "a-plus-plus",
                    "invertor": True,
                    "wifi": False,
                    "shum": 54,
                    "color": "bilyi",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(850, 600, 550, 62.0),
                },
            },
            {
                "sku": "CMPX-100312",
                "cat": "washers",
                "brand": "bosch",
                "country": "DE",
                "cur": "USD",
                "base": D("449.00"),
                "markup": D("12.00"),
                "avail": IN,
                "qty": 5,
                "featured": True,
                "inst": True,
                "inst_max": 12,
                "name": "Пральна машина Bosch WAN2415GPL",
                "name_ru": "Стиральная машина Bosch WAN2415GPL",
                "short": "Завантаження 8 кг, 1200 об/хв, EcoSilence Drive.",
                "short_ru": "Загрузка 8 кг, 1200 об/мин, EcoSilence Drive.",
                "specs": {
                    "tip-pralnoi": "frontalna",
                    "zavantazhennia": 8,
                    "vidzhym": 1200,
                    "programy": 15,
                    "energiia": "a-plus-plus-plus",
                    "invertor": True,
                    "wifi": False,
                    "shum": 52,
                    "color": "bilyi",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(848, 598, 590, 66.0),
                },
            },
            {
                "sku": "CMPX-100313",
                "cat": "washers",
                "brand": "lg",
                "country": "PL",
                "cur": "UAH",
                "base": D("21999"),
                "old": D("24999"),
                "avail": IN,
                "qty": 3,
                "inst": True,
                "inst_max": 12,
                "name": "Пральна машина LG F2WV3S7S3E",
                "short": "Завантаження 7 кг, 1200 об/хв, AI DD, Wi-Fi.",
                "specs": {
                    "tip-pralnoi": "frontalna",
                    "zavantazhennia": 7,
                    "vidzhym": 1200,
                    "programy": 14,
                    "energiia": "a-plus-plus-plus",
                    "invertor": True,
                    "wifi": True,
                    "shum": 51,
                    "color": "bilyi",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(850, 600, 475, 58.0),
                },
            },
            {
                "sku": "CMPX-100314",
                "cat": "washers",
                "brand": "gorenje",
                "country": "SI",
                "cur": "UAH",
                "base": D("15499"),
                "avail": IN,
                "qty": 4,
                "name": "Пральна машина Gorenje WNEI84BS",
                "short": "Завантаження 8 кг, 1400 об/хв, інвертор.",
                "specs": {
                    "tip-pralnoi": "frontalna",
                    "zavantazhennia": 8,
                    "vidzhym": 1400,
                    "programy": 12,
                    "energiia": "a-plus-plus",
                    "invertor": True,
                    "wifi": False,
                    "shum": 55,
                    "color": "bilyi",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(850, 600, 545, 64.0),
                },
            },
            {
                "sku": "CMPX-100315",
                "cat": "washers",
                "brand": "electrolux",
                "country": "PL",
                "cur": "UAH",
                "base": D("13999"),
                "old": D("17499"),
                "avail": IN,
                "qty": 1,
                "cond": Condition.DISCOUNTED,
                "note": "Уцінка: вм'ятина на задній стінці, на роботу не впливає.",
                "note_ru": "Уценка: вмятина на задней стенке, на работу не влияет.",
                "name": "Пральна машина Electrolux EW6SN406WI",
                "short": "Завантаження 6 кг, 1000 об/хв.",
                "specs": {
                    "tip-pralnoi": "frontalna",
                    "zavantazhennia": 6,
                    "vidzhym": 1000,
                    "programy": 12,
                    "energiia": "a-plus",
                    "invertor": True,
                    "wifi": False,
                    "shum": 58,
                    "color": "bilyi",
                    "control": "elektronne",
                    "garantiia": 12,
                    **dims(850, 600, 380, 52.0),
                },
            },
            # ================= ПОСУДОМИЙНІ МАШИНИ =================
            {
                "sku": "CMPX-100321",
                "cat": "dish",
                "brand": "bosch",
                "country": "DE",
                "cur": "UAH",
                "base": D("17999"),
                "avail": IN,
                "qty": 3,
                "inst": True,
                "inst_max": 6,
                "name": "Посудомийна машина Bosch SMS2ITI04E",
                "name_ru": "Посудомоечная машина Bosch SMS2ITI04E",
                "short": "12 комплектів, 5 програм, InfoLight.",
                "short_ru": "12 комплектов, 5 программ, InfoLight.",
                "specs": {
                    "komplekty": 12,
                    "programy": 5,
                    "energiia": "e",
                    "shum": 48,
                    "color": "nerzhaviiuca-stal",
                    "control": "knopkove",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(845, 600, 600, 42.0),
                },
            },
            {
                "sku": "CMPX-100322",
                "cat": "dish",
                "brand": "electrolux",
                "country": "IT",
                "cur": "UAH",
                "base": D("14999"),
                "avail": ON,
                "lead": 10,
                "qty": 0,
                "name": "Посудомийна машина Electrolux ESA47200SW",
                "short": "9 комплектів, AirDry, 6 програм.",
                "specs": {
                    "komplekty": 9,
                    "programy": 6,
                    "energiia": "e",
                    "shum": 49,
                    "color": "bilyi",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(845, 450, 550, 35.0),
                },
            },
            {
                "sku": "CMPX-100323",
                "cat": "dish",
                "brand": "hansa",
                "country": "PL",
                "cur": "UAH",
                "base": D("11499"),
                "avail": IN,
                "qty": 6,
                "name": "Посудомийна машина Hansa ZWM616IH",
                "short": "12 комплектів, 6 програм.",
                "specs": {
                    "komplekty": 12,
                    "programy": 6,
                    "energiia": "f",
                    "shum": 52,
                    "color": "nerzhaviiuca-stal",
                    "control": "knopkove",
                    "garantiia": 24,
                    **dims(850, 598, 600, 40.0),
                },
            },
            {
                "sku": "CMPX-100324",
                "cat": "dish",
                "brand": "whirlpool",
                "country": "IT",
                "cur": "USD",
                "base": D("379.00"),
                "markup": D("12.00"),
                "avail": IN,
                "qty": 2,
                "name": "Посудомийна машина Whirlpool WSFO 3O34 PF X",
                "short": "10 комплектів, 3rd рівень, PowerClean.",
                "specs": {
                    "komplekty": 10,
                    "programy": 9,
                    "energiia": "d",
                    "shum": 47,
                    "color": "nerzhaviiuca-stal",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(845, 450, 600, 38.0),
                },
            },
            # ================= СУШИЛЬНІ МАШИНИ =================
            {
                "sku": "CMPX-100331",
                "cat": "dryers",
                "brand": "bosch",
                "country": "DE",
                "cur": "USD",
                "base": D("599.00"),
                "markup": D("12.00"),
                "avail": ON,
                "lead": 14,
                "qty": 0,
                "inst": True,
                "inst_max": 12,
                "name": "Сушильна машина Bosch WTH85V0PPL",
                "name_ru": "Сушильная машина Bosch WTH85V0PPL",
                "short": "8 кг, тепловий насос, AutoDry.",
                "short_ru": "8 кг, тепловой насос, AutoDry.",
                "specs": {
                    "zavantazhennia": 8,
                    "programy": 15,
                    "energiia": "a-plus-plus",
                    "color": "bilyi",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(842, 598, 599, 48.0),
                },
            },
            {
                "sku": "CMPX-100332",
                "cat": "dryers",
                "brand": "electrolux",
                "country": "IT",
                "cur": "UAH",
                "base": D("25999"),
                "avail": IN,
                "qty": 2,
                "name": "Сушильна машина Electrolux EW6C428WP",
                "short": "8 кг, тепловий насос, DelicateCare.",
                "specs": {
                    "zavantazhennia": 8,
                    "programy": 14,
                    "energiia": "a-plus-plus",
                    "color": "bilyi",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(850, 600, 580, 47.0),
                },
            },
            # ================= ЧАЙНИКИ (SWATCHES: 3 кольори однієї моделі) =================
            {
                "sku": "CMPX-100401",
                "cat": "kettles",
                "brand": "bosch",
                "country": "TR",
                "cur": "UAH",
                "base": D("1799"),
                "avail": IN,
                "qty": 12,
                "name": "Чайник електричний Bosch TWK3P423 (чорний)",
                "name_ru": "Чайник электрический Bosch TWK3P423 (чёрный)",
                "short": "1.7 л, 2400 Вт, прихований нагрівальний елемент.",
                "short_ru": "1.7 л, 2400 Вт, скрытый нагревательный элемент.",
                "specs": {
                    "potuzhnist": 2400,
                    "color": "chornyi",
                    "control": "mekhanichne",
                    "garantiia": 24,
                    **dims(250, 220, 170, 1.2, pad_cm=3),
                },
            },
            {
                "sku": "CMPX-100402",
                "cat": "kettles",
                "brand": "bosch",
                "country": "TR",
                "cur": "UAH",
                "base": D("1799"),
                "avail": IN,
                "qty": 9,
                "name": "Чайник електричний Bosch TWK3P421 (білий)",
                "name_ru": "Чайник электрический Bosch TWK3P421 (белый)",
                "short": "1.7 л, 2400 Вт, прихований нагрівальний елемент.",
                "short_ru": "1.7 л, 2400 Вт, скрытый нагревательный элемент.",
                "specs": {
                    "potuzhnist": 2400,
                    "color": "bilyi",
                    "control": "mekhanichne",
                    "garantiia": 24,
                    **dims(250, 220, 170, 1.2, pad_cm=3),
                },
            },
            {
                "sku": "CMPX-100403",
                "cat": "kettles",
                "brand": "bosch",
                "country": "TR",
                "cur": "UAH",
                "base": D("2099"),
                "avail": IN,
                "qty": 5,
                "name": "Чайник електричний Bosch TWK3P420 (нержавіюча сталь)",
                "short": "1.7 л, 2400 Вт, корпус з нержавіючої сталі.",
                "specs": {
                    "potuzhnist": 2400,
                    "color": "nerzhaviiuca-stal",
                    "control": "mekhanichne",
                    "garantiia": 24,
                    **dims(250, 220, 170, 1.4, pad_cm=3),
                },
            },
            {
                "sku": "CMPX-100404",
                "cat": "kettles",
                "brand": "gorenje",
                "country": "CN",
                "cur": "UAH",
                "base": D("1249"),
                "avail": IN,
                "qty": 17,
                "name": "Чайник електричний Gorenje K17CLI",
                "name_ru": "Чайник электрический Gorenje K17CLI",
                "short": "1.7 л, 2200 Вт, підсвітка.",
                "short_ru": "1.7 л, 2200 Вт, подсветка.",
                "specs": {
                    "potuzhnist": 2200,
                    "color": "sriblyastyi",
                    "control": "mekhanichne",
                    "garantiia": 12,
                    **dims(250, 220, 170, 1.2, pad_cm=3, sep="х"),
                },
            },
            # ================= МІКРОХВИЛЬОВІ (SWATCHES: 3 кольори) =================
            {
                "sku": "CMPX-100411",
                "cat": "micro",
                "brand": "samsung",
                "country": "CN",
                "cur": "UAH",
                "base": D("4499"),
                "avail": IN,
                "qty": 8,
                "name": "Мікрохвильова піч Samsung MS23K3513AK (чорна)",
                "name_ru": "Микроволновая печь Samsung MS23K3513AK (чёрная)",
                "short": "23 л, 800 Вт, керамічна емаль.",
                "short_ru": "23 л, 800 Вт, керамическая эмаль.",
                "specs": {
                    "obiem-kamery": 23,
                    "potuzhnist": 800,
                    "color": "chornyi",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 12,
                    **dims(275, 489, 374, 12.6, pad_cm=5),
                },
            },
            {
                "sku": "CMPX-100412",
                "cat": "micro",
                "brand": "samsung",
                "country": "CN",
                "cur": "UAH",
                "base": D("4399"),
                "avail": IN,
                "qty": 6,
                "name": "Мікрохвильова піч Samsung MS23K3513AW (біла)",
                "name_ru": "Микроволновая печь Samsung MS23K3513AW (белая)",
                "short": "23 л, 800 Вт, керамічна емаль.",
                "short_ru": "23 л, 800 Вт, керамическая эмаль.",
                "specs": {
                    "obiem-kamery": 23,
                    "potuzhnist": 800,
                    "color": "bilyi",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 12,
                    **dims(275, 489, 374, 12.6, pad_cm=5),
                },
            },
            {
                "sku": "CMPX-100413",
                "cat": "micro",
                "brand": "samsung",
                "country": "CN",
                "cur": "UAH",
                "base": D("4799"),
                "avail": ON,
                "lead": 5,
                "qty": 0,
                "name": "Мікрохвильова піч Samsung MS23K3515AS (срібляста)",
                "short": "23 л, 800 Вт, керамічна емаль.",
                "specs": {
                    "obiem-kamery": 23,
                    "potuzhnist": 800,
                    "color": "sriblyastyi",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 12,
                    **dims(275, 489, 374, 12.8, pad_cm=5),
                },
            },
            {
                "sku": "CMPX-100414",
                "cat": "micro",
                "brand": "gorenje",
                "country": "CN",
                "cur": "UAH",
                "base": D("2999"),
                "avail": IN,
                "qty": 10,
                "name": "Мікрохвильова піч Gorenje MO20E1S",
                "short": "20 л, 800 Вт, механічне керування.",
                "specs": {
                    "obiem-kamery": 20,
                    "potuzhnist": 800,
                    "color": "sriblyastyi",
                    "control": "mekhanichne",
                    "dysplei": False,
                    "garantiia": 24,
                    **dims(262, 452, 350, 10.5, pad_cm=5),
                },
            },
            # ================= ПИЛОСОСИ =================
            {
                "sku": "CMPX-100421",
                "cat": "vacuum",
                "brand": "dreame",
                "country": "CN",
                "cur": "USD",
                "base": D("279.00"),
                "markup": D("12.00"),
                "avail": IN,
                "qty": 6,
                "featured": True,
                "inst": True,
                "inst_max": 12,
                "name": "Робот-пилосос Dreame D10 Plus",
                "name_ru": "Робот-пылесос Dreame D10 Plus",
                "short": "LiDAR-навігація, база самоочищення, 4000 Па.",
                "short_ru": "LiDAR-навигация, база самоочистки, 4000 Па.",
                "desc": vac_desc,
                "specs": {
                    "tip-pylososa": "robot",
                    "potuzhnist": 60,
                    "invertor": True,
                    "wifi": True,
                    "shum": 62,
                    "color": "bilyi",
                    "control": "sensorne",
                    "garantiia": 12,
                    **dims(97, 350, 350, 3.6, pad_cm=6),
                },
            },
            {
                "sku": "CMPX-100422",
                "cat": "vacuum",
                "brand": "dreame",
                "country": "CN",
                "cur": "USD",
                "base": D("599.00"),
                "markup": D("10.00"),
                "avail": IN,
                "qty": 2,
                "inst": True,
                "inst_max": 12,
                "name": "Робот-пилосос Dreame L10s Ultra",
                "short": "5300 Па, самоочищення швабри, автозаповнення води.",
                "specs": {
                    "tip-pylososa": "robot",
                    "potuzhnist": 75,
                    "invertor": True,
                    "wifi": True,
                    "shum": 65,
                    "color": "bilyi",
                    "control": "sensorne",
                    "garantiia": 12,
                    **dims(105, 353, 350, 4.2, pad_cm=6),
                },
            },
            {
                "sku": "CMPX-100423",
                "cat": "vacuum",
                "brand": "philips",
                "country": "CN",
                "cur": "UAH",
                "base": D("3299"),
                "avail": IN,
                "qty": 11,
                "name": "Пилосос Philips XB2125/09",
                "short": "Контейнерний, 850 Вт, PowerCyclone 5.",
                "specs": {
                    "tip-pylososa": "z-mishkom",
                    "potuzhnist": 850,
                    "invertor": False,
                    "wifi": False,
                    "shum": 76,
                    "color": "chornyi",
                    "control": "mekhanichne",
                    "garantiia": 24,
                    **dims(280, 300, 450, 5.4, pad_cm=5),
                },
            },
            {
                "sku": "CMPX-100424",
                "cat": "vacuum",
                "brand": "samsung",
                "country": "KR",
                "cur": "UAH",
                "base": D("8999"),
                "old": D("10499"),
                "avail": IN,
                "qty": 3,
                "name": "Пилосос Samsung VS15A6031R5",
                "name_ru": "Пылесос Samsung VS15A6031R5",
                "short": "Бездротовий, 150 Вт, до 40 хвилин роботи.",
                "short_ru": "Беспроводной, 150 Вт, до 40 минут работы.",
                "specs": {
                    "tip-pylososa": "bezdrotovyi",
                    "potuzhnist": 150,
                    "invertor": True,
                    "wifi": False,
                    "shum": 68,
                    "color": "sriblyastyi",
                    "control": "knopkove",
                    "garantiia": 24,
                    **dims(1130, 250, 220, 2.8, pad_cm=5),
                },
            },
            # ================= КОНДИЦІОНЕРИ =================
            {
                "sku": "CMPX-100501",
                "cat": "ac",
                "brand": "tiki",
                "country": "TR",
                "cur": "USD",
                "base": D("349.00"),
                "markup": D("15.00"),
                "avail": IN,
                "qty": 8,
                "featured": True,
                "inst": True,
                "inst_max": 12,
                "name": "Кондиціонер Tiki TKI-09INV",
                "name_ru": "Кондиционер Tiki TKI-09INV",
                "short": "Інверторний, до 25 м², обігрів до −15 °C.",
                "short_ru": "Инверторный, до 25 м², обогрев до −15 °C.",
                "specs": {
                    "ploshcha": 25,
                    "energiia": "a-plus",
                    "invertor": True,
                    "wifi": False,
                    "shum": 24,
                    "color": "bilyi",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 36,
                    **dims(280, 800, 195, 8.5, pad_cm=8),
                },
            },
            {
                "sku": "CMPX-100502",
                "cat": "ac",
                "brand": "samsung",
                "country": "KR",
                "cur": "USD",
                "base": D("549.00"),
                "markup": D("12.00"),
                "avail": IN,
                "qty": 3,
                "inst": True,
                "inst_max": 12,
                "name": "Кондиціонер Samsung AR09TXHQASINUA",
                "short": "Інверторний, до 25 м², Wind-Free.",
                "specs": {
                    "ploshcha": 25,
                    "energiia": "a-plus-plus",
                    "invertor": True,
                    "wifi": True,
                    "shum": 19,
                    "color": "bilyi",
                    "control": "sensorne",
                    "dysplei": True,
                    "garantiia": 36,
                    **dims(299, 889, 215, 9.6, pad_cm=8),
                },
            },
            {
                "sku": "CMPX-100503",
                "cat": "ac",
                "brand": "gorenje",
                "country": "CN",
                "cur": "USD",
                "base": D("429.00"),
                "markup": D("12.00"),
                "avail": ON,
                "lead": 10,
                "qty": 0,
                "name": "Кондиціонер Gorenje KAS35ORFDG",
                "short": "Інверторний, до 35 м².",
                "specs": {
                    "ploshcha": 35,
                    "energiia": "a-plus",
                    "invertor": True,
                    "wifi": True,
                    "shum": 26,
                    "color": "bilyi",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(295, 900, 200, 10.2, pad_cm=8),
                },
            },
            # ================= ОБІГРІВАЧІ =================
            {
                "sku": "CMPX-100511",
                "cat": "heaters",
                "brand": "tiki",
                "country": "TR",
                "cur": "UAH",
                "base": D("1499"),
                "avail": IN,
                "qty": 20,
                "name": "Обігрівач Tiki HC-2000",
                "name_ru": "Обогреватель Tiki HC-2000",
                "short": "Тепловентилятор, 2000 Вт, до 20 м².",
                "short_ru": "Тепловентилятор, 2000 Вт, до 20 м².",
                "specs": {
                    "potuzhnist": 2000,
                    "ploshcha": 20,
                    "color": "bilyi",
                    "control": "mekhanichne",
                    "garantiia": 12,
                    **dims(430, 250, 200, 2.1, pad_cm=4),
                },
            },
            {
                "sku": "CMPX-100512",
                "cat": "heaters",
                "brand": "electrolux",
                "country": "CN",
                "cur": "UAH",
                "base": D("2799"),
                "old": D("3299"),
                "avail": IN,
                "qty": 7,
                "name": "Конвектор Electrolux ECH/AG2-1500",
                "short": "1500 Вт, до 20 м², електронний термостат.",
                "specs": {
                    "potuzhnist": 1500,
                    "ploshcha": 20,
                    "color": "bilyi",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 24,
                    **dims(400, 750, 100, 4.3, pad_cm=5),
                },
            },
            # ================= КРАСА І ДОГЛЯД =================
            {
                "sku": "CMPX-100601",
                "cat": "beauty",
                "brand": "philips",
                "country": "CN",
                "cur": "UAH",
                "base": D("1699"),
                "avail": IN,
                "qty": 14,
                "name": "Фен Philips BHD350/10",
                "name_ru": "Фен Philips BHD350/10",
                "short": "2100 Вт, ThermoProtect, 6 режимів.",
                "short_ru": "2100 Вт, ThermoProtect, 6 режимов.",
                "specs": {
                    "potuzhnist": 2100,
                    "color": "chornyi",
                    "control": "mekhanichne",
                    "garantiia": 24,
                    **dims(230, 90, 250, 0.62, pad_cm=3),
                },
            },
            {
                "sku": "CMPX-100602",
                "cat": "beauty",
                "brand": "philips",
                "country": "CN",
                "cur": "UAH",
                "base": D("2299"),
                "avail": IN,
                "qty": 9,
                "name": "Праска Philips DST5030/20",
                "short": "2400 Вт, паровий удар 180 г, SteamGlide.",
                "specs": {
                    "potuzhnist": 2400,
                    "color": "sriblyastyi",
                    "control": "mekhanichne",
                    "garantiia": 24,
                    **dims(155, 305, 125, 1.5, pad_cm=4),
                },
            },
            {
                "sku": "CMPX-100603",
                "cat": "beauty",
                "brand": "philips",
                "country": "CN",
                "cur": "UAH",
                "base": D("2999"),
                "avail": OUT,
                "qty": 0,
                "name": "Епілятор Philips BRE255/00",
                "short": "Вологе і сухе застосування, 2 швидкості.",
                "specs": {
                    "potuzhnist": 12,
                    "color": "bilyi",
                    "control": "knopkove",
                    "garantiia": 24,
                    **dims(160, 70, 60, 0.35, pad_cm=3),
                },
            },
            # ================= ПОСУД =================
            {
                "sku": "CMPX-100701",
                "cat": "cookware",
                "brand": "tiki",
                "country": "TR",
                "cur": "UAH",
                "base": D("799"),
                "avail": IN,
                "qty": 25,
                "name": "Сковорода Tiki Granit 26 см",
                "name_ru": "Сковорода Tiki Granit 26 см",
                "short": "Гранітне антипригарне покриття, індукція.",
                "short_ru": "Гранитное антипригарное покрытие, индукция.",
                "specs": {"color": "chornyi", "garantiia": 12, **dims(70, 260, 450, 1.1, pad_cm=3)},
            },
            {
                "sku": "CMPX-100702",
                "cat": "cookware",
                "brand": "tiki",
                "country": "TR",
                "cur": "UAH",
                "base": D("2499"),
                "old": D("2999"),
                "avail": IN,
                "qty": 8,
                "name": "Набір каструль Tiki Steel Pro (6 предметів)",
                "short": "Нержавіюча сталь 18/10, кришки зі скла.",
                "specs": {
                    "color": "nerzhaviiuca-stal",
                    "garantiia": 24,
                    **dims(300, 350, 350, 4.6, pad_cm=4),
                },
            },
            {
                "sku": "CMPX-100703",
                "cat": "cookware",
                "brand": "tiki",
                "country": "TR",
                "cur": "UAH",
                "base": D("1899"),
                "avail": ON,
                "lead": 5,
                "qty": 0,
                "name": "Чавунна жаровня Tiki Cast 28 см",
                "short": "Чавун, емальоване покриття, для духовки.",
                "specs": {
                    "color": "chornyi",
                    "garantiia": 24,
                    **dims(120, 280, 380, 4.9, pad_cm=4, sep="×"),
                },
            },
            # ================= ГЕНЕРАТОРИ =================
            {
                "sku": "CMPX-100801",
                "cat": "gen",
                "brand": "tiki",
                "country": "CN",
                "cur": "UAH",
                "base": D("21999"),
                "old": D("25999"),
                "avail": IN,
                "qty": 4,
                "featured": True,
                "inst": True,
                "inst_max": 24,
                "name": "Генератор Tiki TG-3500",
                "name_ru": "Генератор Tiki TG-3500",
                "short": "Бензиновий, 3 кВт, ручний старт.",
                "short_ru": "Бензиновый, 3 кВт, ручной старт.",
                "specs": {
                    "potuzhnist": 3000,
                    "color": "chornyi",
                    "control": "mekhanichne",
                    "garantiia": 12,
                    **dims(450, 600, 450, 43.0, pad_cm=6),
                },
            },
            {
                "sku": "CMPX-100802",
                "cat": "gen",
                "brand": "hansa",
                "country": "CN",
                "cur": "UAH",
                "base": D("34999"),
                "avail": ON,
                "lead": 14,
                "qty": 0,
                "inst": True,
                "inst_max": 24,
                "name": "Генератор Hansa HG-6500E",
                "short": "Бензиновий, 5 кВт, електростарт.",
                "specs": {
                    "potuzhnist": 5000,
                    "color": "chornyi",
                    "control": "elektronne",
                    "dysplei": True,
                    "garantiia": 12,
                    **dims(560, 680, 520, 78.0, pad_cm=6),
                },
            },
            {
                "sku": "CMPX-100803",
                "cat": "gen",
                "brand": "tiki",
                "country": "CN",
                "cur": "UAH",
                "base": D("17999"),
                "old": D("19999"),
                "avail": IN,
                "qty": 6,
                "inst": True,
                "inst_max": 12,
                "name": "Інверторний генератор Tiki TGI-2000",
                "short": "Інверторний, 2 кВт, рівень шуму 58 дБ.",
                "specs": {
                    "potuzhnist": 2000,
                    "color": "sriblyastyi",
                    "control": "mekhanichne",
                    "dysplei": True,
                    "garantiia": 12,
                    **dims(430, 480, 280, 21.0, pad_cm=5),
                },
            },
        ]
        return rows

    def _seed_products(
        self,
        site: SiteSettings,
        cats: dict[str, Category],
        brands: dict[str, Brand],
        countries: dict[str, Country],
        attrs: dict[str, Attribute],
        options: dict[str, AttributeOption],
    ) -> dict[str, Product]:
        rule = site.price_rounding
        rate = site.usd_rate
        products: dict[str, Product] = {}

        for row in self._product_rows():
            specs: dict[str, Any] = dict(row["specs"])
            currency = row.get("cur", SourceCurrency.UAH)
            markup = row.get("markup", Decimal("0"))
            brand = brands[row["brand"]]
            country = countries[row["country"]]
            category = cats[row["cat"]]

            # ⚡ ЦІНУ РАХУЄ БАЗА (ADR-005). old_price — ПОХІДНА від base_old_price тією ж
            #    функцією: інакше після зростання курсу «стара» ціна стане нижчою за нову.
            used_rate = rate if currency == SourceCurrency.USD else None
            price = db_price(row["base"], currency, used_rate, markup, rule)
            old_price = db_price(row.get("old"), currency, used_rate, markup, rule)

            # Бренд і країна — ще й РЯДКИ характеристик (так вони лежать у таблиці замовника),
            # але джерело істини лишається FK: беремо значення саме з нього.
            specs["brend"] = brand.name
            specs["krayina-vyrobnytstva"] = (country.name_uk, country.name_ru)

            # Габарити упаковки — ПРОЄКЦІЯ з характеристики role=PACKAGE_DIMS (не ручне поле).
            h_cm, w_cm, d_cm = parse_package_dims(specs.get("gabaryty-upakovky", ""))

            name_uk = row["name"]
            name_ru = row.get("name_ru", "")
            defaults: dict[str, Any] = {
                "name_uk": name_uk,
                "name_ru": name_ru,
                "slug_uk": translit_slug(name_uk),
                "slug_ru": translit_slug(name_ru or name_uk),
                "short_description_uk": row.get("short", ""),
                "short_description_ru": row.get("short_ru", ""),
                "description_uk": row.get("desc", ""),
                "category": category,
                "brand": brand,
                "country": country,
                "source_currency": currency,
                "base_price": row["base"],
                "base_old_price": row.get("old"),
                "markup_percent": markup,
                "price": price,
                "old_price": old_price,
                "usd_rate_used": used_rate,
                "price_updated_at": timezone.now(),
                "availability": row.get("avail", Availability.IN_STOCK),
                "order_lead_days": row.get("lead"),
                "stock_qty": row.get("qty", 0),
                "condition": row.get("cond", Condition.NEW),
                "condition_note_uk": row.get("note", ""),
                "condition_note_ru": row.get("note_ru", ""),
                "is_featured": row.get("featured", False),
                "installment_available": row.get("inst", False),
                "installment_max_payments": row.get("inst_max"),
                "source": ProductSource.MANUAL,
                "source_category_ext_id": category.external_id,
                "package_height_cm": h_cm,
                "package_width_cm": w_cm,
                "package_depth_cm": d_cm,
                "package_dims_source": ProductSource.MANUAL if h_cm else "",
                "package_weight_kg": Decimal(str(specs.get("vaga-v-upakovtsi", "0"))),
                "warranty_months": specs.get("garantiia"),
                "main_image_url": f"https://picsum.photos/seed/{row['sku']}/700/700",
                "is_active": True,
                "hotline_enabled": True,
            }
            product, _ = Product.objects.update_or_create(sku=row["sku"], defaults=defaults)
            self._write_specs(product, specs, attrs, options)
            products[row["sku"]] = product

        self.stdout.write(
            f"  Товари: {len(products)} "
            f"(USD {sum(1 for p in products.values() if p.source_currency == 'USD')}, "
            f"уцінка {sum(1 for p in products.values() if p.condition == Condition.DISCOUNTED)}, "
            f"оплата частинами {sum(1 for p in products.values() if p.installment_available)})"
        )
        return products

    # -- EAV + денормалізація ------------------------------------------------
    def _write_specs(
        self,
        product: Product,
        specs: dict[str, Any],
        attrs: dict[str, Attribute],
        options: dict[str, AttributeOption],
    ) -> None:
        """EAV — джерело істини; specs_json / filter_tokens / specs_num — похідні від нього."""
        product.attr_values.all().delete()

        specs_uk: list[dict[str, Any]] = []
        specs_ru: list[dict[str, Any]] = []
        tokens: list[str] = []
        nums: dict[str, float] = {}

        for code, raw in specs.items():
            attr = attrs[code]
            pav = ProductAttributeValue(
                product=product,
                attribute=attr,
                source=ProductSource.MANUAL,
                sort_order=attr.sort_order,
                raw_value=str(raw),
            )
            display_uk = display_ru = ""
            vnum: float | None = None

            if attr.value_type == Attribute.ValueType.OPTION:
                opt = options[f"{code}:{raw}"]
                pav.option = opt
                display_uk, display_ru = opt.value_uk, opt.value_ru
                if attr.is_filterable:
                    tokens.append(f"{code}:{opt.slug}")
            elif attr.value_type == Attribute.ValueType.BOOL:
                pav.value_bool = bool(raw)
                display_uk, display_ru = ("Так", "Да") if raw else ("Ні", "Нет")
                if raw and attr.is_filterable:
                    tokens.append(f"{code}:1")
            elif attr.value_type == Attribute.ValueType.NUMBER:
                # ⚠️ value_number ОБОВ'ЯЗКОВИЙ для чисел: без нього не працюють ані range-фільтри,
                #    ані suggest_buckets, ані сортування в адмінських звітах.
                pav.value_number = Decimal(str(raw))
                vnum = float(raw)
                display_uk = display_ru = f"{vnum:g}"
                nums[code] = vnum
                if attr.is_filterable:
                    if attr.filter_widget == Attribute.FilterWidget.RANGE_BUCKETS:
                        tok = bucket_token(code, attr.range_buckets, vnum)
                        if tok:
                            tokens.append(tok)
                    elif attr.filter_widget == Attribute.FilterWidget.CHECKBOX:
                        tokens.append(f"{code}:{vnum:g}")
            else:  # STRING — може прийти парою (uk, ru): «Німеччина» / «Германия»
                val_uk, val_ru = raw if isinstance(raw, tuple) else (str(raw), str(raw))
                pav.value_string_uk, pav.value_string_ru = val_uk, val_ru
                pav.raw_value = val_uk
                display_uk, display_ru = val_uk, val_ru

            pav.save()

            # ⚠️ ФОРМАТ РЕНДЕРУ (жорстка вимога замовника, INPUTS §2):
            #    ліва колонка = n («Висота»), права = f"{v} {u}" («2000 мм»).
            #    Одиниця клеїться до ЗНАЧЕННЯ, а не до назви. Фронт НІКОЛИ не конкатенує n + u.
            unit_uk = attr.unit.name_uk if attr.unit_id else ""
            unit_ru = attr.unit.name_ru if attr.unit_id else ""
            base = {"code": code, "s": attr.sort_order}
            specs_uk.append(
                {
                    **base,
                    "g": attr.group.name_uk,
                    "gs": attr.group.sort_order,
                    "n": attr.name_uk,
                    "u": unit_uk,
                    "v": display_uk,
                    "vn": vnum,
                }
            )
            specs_ru.append(
                {
                    **base,
                    "g": attr.group.name_ru,
                    "gs": attr.group.sort_order,
                    "n": attr.name_ru,
                    "u": unit_ru,
                    "v": display_ru,
                    "vn": vnum,
                }
            )

        # Токени, що НЕ походять з EAV, але живуть у тій самій колонці (ADR-008): бренд, країна,
        # наявність, стан, оплата частинами. Без мови — тільки slug'и.
        if product.brand_id:
            tokens.append(f"brand:{product.brand.slug}")
        if product.country_id:
            tokens.append(f"country:{product.country.slug}")
        tokens.append(f"avail:{product.availability}")
        if product.condition != Condition.NEW:
            tokens.append(f"cond:{product.condition}")
        if product.installment_available:
            tokens.append("installment:1")

        Product.objects.filter(pk=product.pk).update(
            specs_json_uk=sorted(specs_uk, key=lambda x: (x["gs"], x["s"])),
            specs_json_ru=sorted(specs_ru, key=lambda x: (x["gs"], x["s"])),
            filter_tokens=sorted(tokens),
            specs_num=nums,
            denorm_dirty=False,
            denorm_at=timezone.now(),
        )

    # -- варіанти ------------------------------------------------------------
    def _seed_variants(self, attrs: dict[str, Attribute], products: dict[str, Product]) -> None:
        """Обидва типи перемикача (на референсах замовника є обидва):

        BUTTONS  — вісь «Діагональ екрану»: один ТВ у 5 діагоналях (скрін 384);
        SWATCHES — вісь «Колір виробу»: та сама модель у 2–3 кольорах (скріни 346/347).

        Кожен варіант — ОКРЕМИЙ Product (у нього своя ціна, свій артикул, своя наявність),
        а перемикач — це VariantGroup + VariantItem. Активний елемент у картці — той,
        чий product == поточний товар.
        """
        groups: list[tuple[str, str, Attribute, str, list[tuple[str, str, str, str]]]] = [
            (
                "Samsung Crystal UHD DU7100",
                "Samsung Crystal UHD DU7100",
                attrs["diahonal"],
                VariantGroup.Widget.BUTTONS,
                [
                    ("CMPX-100110", '50" (127 см)', '50" (127 см)', ""),
                    ("CMPX-100111", '55" (139,7 см)', '55" (139,7 см)', ""),
                    ("CMPX-100112", '65" (163 см)', '65" (163 см)', ""),
                    ("CMPX-100113", '75" (189 см)', '75" (189 см)', ""),
                    ("CMPX-100114", '85" (214 см)', '85" (214 см)', ""),
                ],
            ),
            (
                "Bosch TWK3P42 — кольори",
                "Bosch TWK3P42 — цвета",
                attrs["color"],
                VariantGroup.Widget.SWATCHES,
                [
                    ("CMPX-100401", "Чорний", "Чёрный", "#1A1A1A"),
                    ("CMPX-100402", "Білий", "Белый", "#FFFFFF"),
                    ("CMPX-100403", "Нержавіюча сталь", "Нержавеющая сталь", "#B5B8BB"),
                ],
            ),
            (
                "Samsung MS23K35 — кольори",
                "Samsung MS23K35 — цвета",
                attrs["color"],
                VariantGroup.Widget.SWATCHES,
                [
                    ("CMPX-100411", "Чорна", "Чёрная", "#1A1A1A"),
                    ("CMPX-100412", "Біла", "Белая", "#FFFFFF"),
                    ("CMPX-100413", "Срібляста", "Серебристая", "#C0C0C0"),
                ],
            ),
        ]

        n_items = 0
        for name_uk, name_ru, axis, widget, items in groups:
            group, _ = VariantGroup.objects.update_or_create(
                name_uk=name_uk,
                defaults={
                    "name_ru": name_ru,
                    "axis_attribute": axis,
                    "widget": widget,
                    "is_active": True,
                },
            )
            for i, (sku, label_uk, label_ru, hex_) in enumerate(items):
                VariantItem.objects.update_or_create(
                    group=group,
                    product=products[sku],
                    defaults={
                        "label_uk": label_uk,
                        "label_ru": label_ru,
                        "swatch_hex": hex_,
                        "sort_order": i * 10,
                    },
                )
                n_items += 1

        self.stdout.write(
            f"  Варіанти: груп {len(groups)} (BUTTONS 1 / SWATCHES 2), товарів у групах {n_items}"
        )

    # -- супутні (комплекти) -------------------------------------------------
    def _seed_related(self, products: dict[str, Product]) -> None:
        """Комплект вбудованої техніки: духова шафа + варильна поверхня + витяжка.

        ⚠️ ВЗАЄМНІСТЬ — властивість структури, а не робота коду: група одна, членів троє,
        тому на картці КОЖНОГО автоматично видно двох інших. Асиметрія неможлива в принципі.
        """
        sets: list[tuple[str, str, list[tuple[str, str, str]]]] = [
            (
                "Комплект вбудованої техніки Bosch Serie 6",
                "Комплект встраиваемой техники Bosch Serie 6",
                [
                    ("CMPX-100211", "Духова шафа", "Духовой шкаф"),
                    ("CMPX-100201", "Варильна поверхня", "Варочная поверхность"),
                    ("CMPX-100221", "Витяжка", "Вытяжка"),
                ],
            ),
            (
                "Комплект вбудованої техніки Gorenje",
                "Комплект встраиваемой техники Gorenje",
                [
                    ("CMPX-100213", "Духова шафа", "Духовой шкаф"),
                    ("CMPX-100202", "Варильна поверхня", "Варочная поверхность"),
                    ("CMPX-100223", "Витяжка", "Вытяжка"),
                ],
            ),
        ]

        n_items = 0
        for i, (name_uk, name_ru, items) in enumerate(sets):
            group, _ = RelatedGroup.objects.update_or_create(
                name_uk=name_uk,
                defaults={
                    "name_ru": name_ru,
                    "kind": RelatedGroup.Kind.SET,
                    "is_active": True,
                    "sort_order": i * 10,
                },
            )
            for j, (sku, role_uk, role_ru) in enumerate(items):
                RelatedGroupItem.objects.update_or_create(
                    group=group,
                    product=products[sku],
                    defaults={"role_uk": role_uk, "role_ru": role_ru, "sort_order": j * 10},
                )
                n_items += 1

        self.stdout.write(f"  Комплекти (RelatedGroup): {len(sets)}, позицій {n_items}")

    # -- черга перекладу -----------------------------------------------------
    def _seed_translations(self, products: dict[str, Product]) -> None:
        """TranslationEntry — ДЖЕРЕЛО ПРАВДИ черги, `*_ru`-колонка — вітрина.

        Словник (характеристики / значення / одиниці / категорії) уже перекладений вище — саме
        так і має бути за архітектурою: перекладаємо ~4 900 рядків СЛОВНИКА один раз, а не
        10 000 товарів по черзі. Тут — сама ЧЕРГА: товари, у яких `name_ru` порожній,
        чекають машинного перекладу і ручного схвалення.
        """
        ct_product = ContentType.objects.get_for_model(Product)
        created = 0

        for _sku, product in sorted(products.items()):
            if product.name_ru:  # уже перекладено (approve() колись відпрацював)
                continue
            TranslationEntry.objects.update_or_create(
                content_type=ct_product,
                object_id=product.pk,
                field="name",
                target_lang="ru",
                defaults={
                    "kind": TranslationKind.PRODUCT_NAME,
                    "source_text": product.name_uk,
                    "status": TranslationStatus.PENDING,
                    "batch_id": DEMO_BATCH,
                },
            )
            created += 1
            if product.short_description_uk and not product.short_description_ru:
                TranslationEntry.objects.update_or_create(
                    content_type=ct_product,
                    object_id=product.pk,
                    field="short_description",
                    target_lang="ru",
                    defaults={
                        "kind": TranslationKind.PRODUCT_SHORT_DESCRIPTION,
                        "source_text": product.short_description_uk,
                        "status": TranslationStatus.PENDING,
                        "batch_id": DEMO_BATCH,
                    },
                )
                created += 1

        # Два записи в статусі MACHINE — це і є «черга схвалення» в адмінці: модель переклала,
        # published_text порожній, тобто на сайт цей текст ЩЕ НЕ ПОТРАПИВ.
        machine = [
            (
                "CMPX-100421",
                "description",
                TranslationKind.PRODUCT_DESCRIPTION,
                "<h2>Робот-пылесос с лазерной навигацией</h2><p>Строит карту помещения "
                "за один проход и убирает комнату за комнатой.</p>",
                "Проверить термин «база самоочистки» по глоссарию.",
            ),
            (
                "CMPX-100113",
                "name",
                TranslationKind.PRODUCT_NAME,
                "Телевизор Samsung UE75DU7100UXUA",
                "",
            ),
        ]
        for sku, field, kind, target, note in machine:
            product = products[sku]
            source = getattr(product, f"{field}_uk", "") or ""
            entry, _ = TranslationEntry.objects.update_or_create(
                content_type=ct_product,
                object_id=product.pk,
                field=field,
                target_lang="ru",
                defaults={
                    "kind": kind,
                    "source_text": source,
                    "target_text": target,
                    "status": TranslationStatus.MACHINE,
                    "model_note": note,
                    "engine_model": "claude-sonnet-4-5",
                    "prompt_version": "v1",
                    "glossary_version": "2026-07-01",
                    "batch_id": DEMO_BATCH,
                    "input_tokens": 180,
                    "output_tokens": 90,
                    "cost_usd": Decimal("0.000420"),
                },
            )
            created += 1 if entry else 0

        pending = TranslationEntry.objects.filter(
            batch_id=DEMO_BATCH, status=TranslationStatus.PENDING
        ).count()
        machine_n = TranslationEntry.objects.filter(
            batch_id=DEMO_BATCH, status=TranslationStatus.MACHINE
        ).count()
        self.stdout.write(
            f"  Черга перекладу: PENDING {pending}, MACHINE (очікує схвалення) {machine_n}"
        )

    # -- замовлення ----------------------------------------------------------
    def _seed_orders(self, products: dict[str, Product]) -> None:
        """Шість замовлень у різних статусах — щоб адмінка замовлень не була порожня.

        ⚠️ Номер тут ЗАДАЄТЬСЯ ЯВНО (CMPX-DEMO-*), а не береться з order_number_seq: інакше
        повторний прогін seed_demo не був би ідемпотентним (sequence не відкочується).
        Бойовий шлях — Order.generate_number(), він і далі ходить у sequence.
        """
        st_ = Order.Status
        dm = Order.DeliveryMethod
        pm = Order.PaymentMethod
        ps = Order.PaymentStatus
        KYIV = (
            "71508128-9b87-11de-822f-000c2965ae0e",
            "Київська обл.",
            "8d5a980d-391c-11dd-90d9-001a92567626",
            "Київ",
        )
        LVIV = (
            "71508128-9b87-11de-822f-000c2965ae0d",
            "Львівська обл.",
            "db5c88f0-391c-11dd-90d9-001a92567626",
            "Львів",
        )

        rows: list[dict[str, Any]] = [
            {
                "n": "0001",
                "status": st_.NEW,
                "last": "Ковальчук",
                "first": "Андрій",
                "phone": "+380671234501",
                "email": "a.kovalchuk@example.com",
                "delivery": dm.NP_WAREHOUSE,
                "city": KYIV,
                "wh": (
                    "1ec09d88-e1c2-11e3-8c4a-0050568002cf",
                    "Відділення №1: вул. Пирогівський шлях, 135",
                ),
                "service": "WarehouseWarehouse",
                "pay": pm.COD,
                "pay_status": ps.NOT_REQUIRED,
                "ship": Decimal("180.00"),
                "items": [("CMPX-100401", 1), ("CMPX-100411", 1)],
                "comment": "Подзвоніть за годину до доставки.",
            },
            {
                "n": "0002",
                "status": st_.CONFIRMED,
                "last": "Мельник",
                "first": "Оксана",
                "phone": "+380502345602",
                "email": "o.melnyk@example.com",
                "delivery": dm.NP_WAREHOUSE,
                "city": LVIV,
                "wh": (
                    "1ec09d88-e1c2-11e3-8c4a-0050568002d1",
                    "Відділення №12: вул. Городоцька, 359",
                ),
                "service": "WarehouseWarehouse",
                "pay": pm.ONLINE,
                "pay_status": ps.PAID,
                "paid": True,
                "ship": Decimal("420.00"),
                "items": [("CMPX-100301", 1)],
            },
            {
                "n": "0003",
                "status": st_.PACKED,
                "last": "Гриценко",
                "first": "Ігор",
                "phone": "+380933456703",
                "email": "i.grytsenko@example.com",
                "delivery": dm.NP_POSTOMAT,
                "city": KYIV,
                "wh": ("6a3e2f11-4b1c-11e5-9a2c-005056887b8d", "Поштомат №5241: вул. Хрещатик, 22"),
                "service": "WarehousePostomat",
                "pay": pm.PREPAY,
                "pay_status": ps.PENDING,
                "ship": Decimal("95.00"),
                "items": [("CMPX-100601", 1), ("CMPX-100701", 2)],
            },
            {
                "n": "0004",
                "status": st_.SHIPPED,
                "last": "Сидоренко",
                "first": "Марія",
                "phone": "+380674567804",
                "email": "m.sydorenko@example.com",
                "delivery": dm.NP_COURIER,
                "city": KYIV,
                "address": "вул. Січових Стрільців, 14, кв. 7",
                "pay": pm.INSTALLMENT,
                "pay_status": ps.PENDING,
                "ship": Decimal("540.00"),
                "ttn": "20450912345678",
                # ⚠️ Оплата частинами — ЛИШЕ якщо ВСІ позиції її підтримують (інваріант
                #    orders/services/checkout.py::validate_installment). Тут обидві — так.
                "items": [("CMPX-100312", 1), ("CMPX-100421", 1)],
            },
            {
                "n": "0005",
                "status": st_.DELIVERED,
                "last": "Бондар",
                "first": "Василь",
                "phone": "+380995678905",
                "delivery": dm.PICKUP,
                "pay": pm.COD,
                "pay_status": ps.NOT_REQUIRED,
                "ship": Decimal("0.00"),
                "items": [("CMPX-100202", 1), ("CMPX-100404", 1)],
            },
            {
                "n": "0006",
                "status": st_.CANCELLED,
                "last": "Ткаченко",
                "first": "Ольга",
                "phone": "+380636789006",
                "delivery": dm.LOCAL_COURIER,
                "address": "м. Ужгород, вул. Собранецька, 92",
                "pay": pm.COD,
                "pay_status": ps.NOT_REQUIRED,
                "ship": Decimal("120.00"),
                "items": [("CMPX-100306", 1)],
                "manager": "Клієнт передумав: знайшов дешевше в іншому місті.",
            },
        ]

        now = timezone.now()
        for row in rows:
            number = f"{DEMO_ORDER_PREFIX}{row['n']}"
            area_ref = area_name = city_ref = city_name = ""
            if row.get("city"):
                area_ref, area_name, city_ref, city_name = row["city"]
            wh_ref, wh_name = row.get("wh", ("", ""))

            items = [(products[sku], qty) for sku, qty in row["items"]]
            subtotal = sum((p.price * qty for p, qty in items), Decimal("0"))

            order, _ = Order.objects.update_or_create(
                number=number,
                defaults={
                    # uuid5, а не uuid4: повторний прогін мусить потрапити в ТОЙ САМИЙ рядок.
                    "idempotency_key": uuid.uuid5(NS, number),
                    "status": row["status"],
                    "last_name": row["last"],
                    "first_name": row["first"],
                    "phone": row["phone"],
                    "email": row.get("email", ""),
                    "comment": row.get("comment", ""),
                    "delivery_method": row["delivery"],
                    "np_area_ref": area_ref,
                    "np_area_name": area_name,
                    "np_city_ref": city_ref,
                    "np_city_name": city_name,
                    "np_warehouse_ref": wh_ref,
                    "np_warehouse_name": wh_name,
                    "np_service_type": row.get("service", ""),
                    "delivery_address": row.get("address", ""),
                    "delivery_cost_estimate": row["ship"],
                    "ttn": row.get("ttn", ""),
                    "payment_method": row["pay"],
                    "payment_status": row["pay_status"],
                    "paid_at": now if row.get("paid") else None,
                    "subtotal": subtotal,
                    "discount": Decimal("0"),
                    "total": subtotal,
                    "manager_note": row.get("manager", ""),
                    "utm": {"utm_source": "google", "utm_medium": "cpc"},
                    "ip": "192.0.2.10",
                },
            )

            order.items.all().delete()
            for product, qty in items:
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    sku=product.sku,
                    name=product.name_uk,
                    price=product.price,
                    quantity=qty,
                    image_url=product.main_image_url,
                    # СНАПШОТ тумблера: інваріант перевіряється по стану НА МОМЕНТ замовлення.
                    installment_available=product.installment_available,
                    weight_kg=product.package_weight_kg,
                    dims_source="product" if product.package_weight_kg else "category",
                )

            order.history.all().delete()
            chain = [st_.NEW]
            if row["status"] != st_.NEW:
                chain.append(row["status"])
            prev = ""
            for st in chain:
                OrderStatusHistory.objects.create(
                    order=order,
                    from_status=prev,
                    to_status=st,
                    comment="Демо-дані (seed_demo)",
                )
                prev = st

        self.stdout.write(
            f"  Замовлення: {len(rows)} ({', '.join(sorted({r['status'] for r in rows}))})"
        )

    # -- CMS -----------------------------------------------------------------
    def _seed_cms(self) -> None:
        # ⚠️ БАНЕРИ ТУТ БІЛЬШЕ НЕ СТВОРЮЮТЬСЯ — І ЦЕ НАВМИСНО.
        #
        # Раніше сід заводив три банери («Вбудована техніка Bosch / Комплект зі знижкою до 15%»,
        # «Великі холодильники», «Уцінка / Вітринні зразки з гарантією»). Це були ВИГАДАНІ
        # обіцянки: жодної знижки 15% у прайсі не існує, уцінених товарів у БД нуль
        # (`condition = Новий` у всіх 678 позицій). Демо-дані, які брешуть клієнтові магазину,
        # — гірші за відсутність демо-даних: їх ніхто не помічає, поки хтось не прийде за
        # знижкою, якої немає.
        #
        # Модель Banner, адмінка і GET /cms/banners ЛИШАЮТЬСЯ: замовник може завести справжній
        # банер під справжню акцію. Головна коректно працює з порожнім списком банерів —
        # секція просто не рендериться (frontend/src/app/[locale]/page.tsx).
        #
        # Контент головної тепер дають ДОБІРКИ ПО РЕАЛЬНИХ ДАНИХ (catalog/services/collections.py):
        # «Хіти продажів» (is_featured), «Акції» (old_price), «Новинки» (created_at),
        # «Уцінка» (condition) — жодного вигаданого тексту, лише те, що справді є в БД.

        news = [
            (
                "znyzhky-na-vbudovanu-tekhniku",
                "Знижки на вбудовану техніку Bosch",
                "Скидки на встраиваемую технику Bosch",
                "До кінця місяця — комплект вбудованої техніки Bosch Serie 6 зі знижкою.",
                "<p>Купуючи духову шафу, варильну поверхню й витяжку Bosch Serie 6 разом, "
                "ви отримуєте знижку 15% на весь комплект.</p><p>Пропозиція діє до кінця місяця "
                "або поки товар є в наявності.</p>",
            ),
            (
                "yak-obraty-kholodylnyk",
                "Як обрати холодильник: 5 критеріїв",
                "",
                "Об'єм, No Frost, клас енергоефективності, рівень шуму й тип компресора.",
                "<h2>1. Об'єм</h2><p>На двох достатньо 250–300 л, на родину з дітьми — від 350 л.</p>"
                "<h2>2. No Frost</h2><p>Не треба розморожувати, але корисний об'єм трохи менший.</p>"
                "<h2>3. Клас енергоефективності</h2><p>Різниця між A+ і A+++ — до 40% споживання.</p>",
            ),
        ]
        for slug, t_uk, t_ru, excerpt, body in news:
            NewsPost.objects.update_or_create(
                slug_uk=slug,
                defaults={
                    "title_uk": t_uk,
                    "title_ru": t_ru,
                    "slug_ru": slug,
                    "excerpt_uk": excerpt,
                    "body_uk": body,
                    "cover": ensure_png(f"news/{slug}.png", (1200, 630), (32, 34, 38)),
                    "is_published": True,
                    "published_at": timezone.now(),
                },
            )

        pages = [
            (
                "payment-delivery",
                "Доставка й оплата",
                "Доставка и оплата",
                "<h2>Доставка</h2><p>Нова Пошта по всій Україні: відділення, поштомат, кур'єр. "
                "Безкоштовно від 15 000 грн.</p><p>Самовивіз — м. Ужгород, вул. Гагаріна, 101.</p>"
                "<h2>Оплата</h2><p>Накладений платіж, повна передоплата, оплата карткою онлайн, "
                "оплата частинами до 24 платежів.</p>",
            ),
            (
                "warranty",
                "Гарантія",
                "Гарантия",
                "<p>На всю техніку діє <strong>гарантія від виробника</strong>. Строк указано в "
                "характеристиках кожного товару (рядок «Гарантійний термін»).</p>"
                "<p>Уцінені товари мають гарантію 12 місяців, якщо не вказано інше.</p>",
            ),
            (
                "about",
                "Про нас",
                "О нас",
                "<p>Complex — магазин побутової техніки в Ужгороді. Працюємо з 2014 року, "
                "возимо техніку напряму з Європи.</p>",
            ),
            (
                "contacts",
                "Контакти",
                "Контакты",
                "<p>м. Ужгород, вул. Гагаріна, 101</p><p>Телефони: 095 091 52 22, 099 171 79 25</p>"
                "<p>E-mail: uzh.tehnika77@gmail.com</p>"
                "<p>ПН–ПТ 09:00–20:00, СБ–НД 10:00–17:00</p>",
            ),
        ]
        for i, (key, t_uk, t_ru, body) in enumerate(pages):
            StaticPage.objects.update_or_create(
                key=key,
                defaults={
                    "title_uk": t_uk,
                    "title_ru": t_ru,
                    "body_uk": body,
                    "is_published": True,
                    "show_in_footer": True,
                    "sort_order": i * 10,
                },
            )

        PickupPoint.objects.update_or_create(
            name_uk="Магазин Complex (Ужгород)",
            defaults={
                "name_ru": "Магазин Complex (Ужгород)",
                "address_uk": "вул. Гагаріна, 101",
                "address_ru": "ул. Гагарина, 101",
                "city": "Ужгород",
                "phone": "+380950915222",
                "working_hours_uk": "ПН–ПТ 09:00–20:00, СБ–НД 10:00–17:00",
                "working_hours_ru": "ПН–ПТ 09:00–20:00, СБ–ВС 10:00–17:00",
                "latitude": Decimal("48.620800"),
                "longitude": Decimal("22.287900"),
                "is_active": True,
            },
        )

        self.stdout.write(
            f"  CMS: банерів 0 (демо-банери прибрано — див. коментар у _seed_cms), "
            f"новин {len(news)}, сторінок {len(pages)}, точок самовивозу 1"
        )

    # -- журнал синхронізацій ------------------------------------------------
    def _seed_sync(self, products: dict[str, Product]) -> None:
        source, _ = PriceSource.objects.update_or_create(
            code=DEMO_SOURCE_CODE,
            defaults={
                # ⚠️ «Nisa» тут — РЕАЛЬНА назва Google-таблиці замовника (INPUTS §3.1),
                #    а не назва нашого проєкту. НЕ перейменовувати: під цим іменем
                #    таблиця існує в Google Drive замовника.
                "name": "Прайс-лист Nisa (Для клієнтів)",
                "kind": PriceSource.Kind.GOOGLE_SHEET,
                "is_primary": True,
                "is_active": True,
                "priority": 10,
                "default_availability": Availability.IN_STOCK,
                "spreadsheet_id": "1qgMFRcTcH2Jt1q1kICmOYVJQiXSPzZpCVdP8UogqjDY",
            },
        )
        # column_map в адмінці = зміна заголовків у таблиці правиться БЕЗ деплою (INPUTS §3.1).
        col_map = {
            "sku": "Артикул",
            "name": "Найменування",
            "price": "Ціна",
            "qty": "К-сть",
            "currency": "Валюта",
            "category": "Категорія",
            "brand": "Виробник",
            "country": "Країна_виробник",
            "photo": "Фото",
        }
        for tab, currency in (("UAH", SourceCurrency.UAH), ("USD", SourceCurrency.USD)):
            PriceSheet.objects.update_or_create(
                price_source=source,
                tab_name=tab,
                defaults={"currency": currency, "column_map": col_map, "is_active": True},
            )

        now = timezone.now()
        runs = [
            (RUN_OK, SyncRun.Status.SUCCESS, 5100, 5100, 12, 4890, 3, 0, 0, {}),
            (
                RUN_PARTIAL,
                SyncRun.Status.PARTIAL,
                5098,
                5100,
                4,
                4902,
                7,
                6,
                2,
                {
                    "rows": 5098,
                    "alive": 5210,
                    "ratio": 0.98,
                    "min_ratio": 0.5,
                    "deactivate_pct": 0.13,
                    "median_price_delta_pct": 1.7,
                },
            ),
        ]
        for i, (
            rid,
            status,
            read,
            expected,
            created,
            updated,
            deact,
            failed,
            dup,
            guard,
        ) in enumerate(runs):
            SyncRun.objects.update_or_create(
                id=rid,
                defaults={
                    "kind": SyncRun.Kind.SHEETS_PRICES,
                    "price_source": source,
                    "trigger": SyncRun.Trigger.CRON if i == 0 else SyncRun.Trigger.MANUAL,
                    "status": status,
                    "rows_read": read,
                    "rows_expected": expected,
                    "products_created": created,
                    "products_updated": updated,
                    "products_deactivated": deact,
                    "rows_failed": failed,
                    "rows_duplicated": dup,
                    "images_queued": 34,
                    "specs_filled": 4700,
                    "categories_missing": [] if i == 0 else ["c50549829"],
                    "guard_report": guard,
                    "duration_ms": 41200 + i * 3000,
                },
            )
            # started_at — auto_now_add, тому «зістарюємо» прогони окремим UPDATE:
            # без цього обидва мали б час зараз і журнал виглядав би неправдоподібно.
            started = now - timedelta(hours=8 - i * 4)
            SyncRun.objects.filter(pk=rid).update(
                started_at=started, finished_at=started + timedelta(seconds=42)
            )

        SyncLogEntry.objects.filter(run_id__in=[RUN_OK, RUN_PARTIAL]).delete()
        L, A = SyncLogEntry.Level, SyncLogEntry.Action
        entries = [
            (RUN_OK, L.INFO, A.CREATED, "UAH", 118, "CMPX-100301", "Створено товар"),
            (RUN_OK, L.INFO, A.CREATED, "USD", 42, "CMPX-100302", "Створено товар"),
            (
                RUN_OK,
                L.INFO,
                A.DEACTIVATED,
                "UAH",
                260,
                "CMPX-100305",
                "Зник з прайсу → знято з наявності",
            ),
            (
                RUN_PARTIAL,
                L.WARN,
                A.CATEGORY_MISSING,
                "UAH",
                512,
                "CMPX-100999",
                "Категорії 'c50549829' немає в дереві → товар осів у «__unmapped__»",
            ),
            (
                RUN_PARTIAL,
                L.WARN,
                A.BRAND_CREATED,
                "UAH",
                604,
                "CMPX-100777",
                "Створено бренд «SHARP» (needs_review=True) — контент-менеджеру треба злити дублі",
            ),
            (
                RUN_PARTIAL,
                L.WARN,
                A.DUPLICATE_SKU,
                "USD",
                71,
                "CMPX-100302",
                "Артикул зустрівся двічі: виграв лист з меншим sort_order",
            ),
            (
                RUN_PARTIAL,
                L.ERROR,
                A.ROW_INVALID,
                "UAH",
                733,
                "",
                "Рядок без артикула: «Запчастина для духовки TEKA 83340602» — пропущено",
            ),
            (
                RUN_PARTIAL,
                L.ERROR,
                A.IMAGE_FAILED,
                "UAH",
                118,
                "CMPX-100301",
                "Фото не завантажилось: HTTP 403 (буде ретрай)",
            ),
        ]
        for run_id, level, action, sheet, rownum, sku, msg in entries:
            SyncLogEntry.objects.create(
                run_id=run_id,
                level=level,
                action=action,
                sheet=sheet,
                row_number=rownum,
                sku=sku,
                product=products.get(sku),
                message=msg,
            )

        self.stdout.write(
            f"  Синхронізація: джерел 1, листів 2, прогонів {len(runs)} "
            f"(success + partial), записів журналу {len(entries)}"
        )

    # -- прибирання ----------------------------------------------------------
    def _cleanup_stale_options(
        self, attrs: dict[str, Attribute], options: dict[str, AttributeOption]
    ) -> None:
        """Опції, що лишились від попередньої версії демо (напр. «Кількість камер» була OPTION,
        стала NUMBER). Видаляємо ТІЛЬКИ вільні: PAV.option має PROTECT, і це правильно —
        жива опція не має зникати мовчки.
        """
        kept = {o.pk for o in options.values()}
        stale = AttributeOption.objects.filter(attribute__in=list(attrs.values())).exclude(
            pk__in=kept
        )
        for opt in stale:
            with contextlib.suppress(ProtectedError):
                # ProtectedError = опцію використовує ЧУЖИЙ товар. Не наша справа: PAV.option
                # має PROTECT саме для того, щоб жива опція не зникала мовчки.
                opt.delete()

    # -- підсумок ------------------------------------------------------------
    def _report(self) -> None:
        A = Attribute.objects
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Демо-дані створено."))
        self.stdout.write(
            f"  Категорій: {Category.objects.count()}  |  "
            f"Брендів: {Brand.objects.count()}  |  Країн: {Country.objects.count()}"
        )
        self.stdout.write(
            f"  Товарів: {Product.objects.count()}  |  "
            f"Характеристик: {A.count()} (фільтрованих {A.filter(is_filterable=True).count()})  |  "
            f"Значень характеристик: {ProductAttributeValue.objects.count()}"
        )
        self.stdout.write(
            f"  Варіантів: {VariantGroup.objects.count()} груп / "
            f"{VariantItem.objects.count()} товарів  |  "
            f"Комплектів: {RelatedGroup.objects.count()} / "
            f"{RelatedGroupItem.objects.count()} позицій"
        )
        self.stdout.write(
            f"  Замовлень: {Order.objects.count()} "
            f"({OrderItem.objects.count()} позицій)  |  "
            f"Черга перекладу: {TranslationEntry.objects.count()}"
        )
        self.stdout.write(
            f"  CMS: банерів {Banner.objects.count()}, новин {NewsPost.objects.count()}, "
            f"сторінок {StaticPage.objects.count()}  |  "
            f"Прогонів синку: {SyncRun.objects.count()} "
            f"({SyncLogEntry.objects.count()} записів журналу)"
        )
        # PriceHistory наповнює ТРИГЕР, а не ця команда. Ненульовий лічильник = тригер живий.
        self.stdout.write(
            f"  PriceHistory (записав тригер price_history_trg): {PriceHistory.objects.count()}"
        )

        self.stdout.write("")
        self.stdout.write("  Перерахунок курсу (USD-товари, курс з SiteSettings):")
        for p in Product.objects.filter(source_currency=SourceCurrency.USD).order_by("sku")[:5]:
            self.stdout.write(
                f"    {p.sku}  {p.name_uk[:38]:<38} "
                f"{p.base_price} USD × {p.usd_rate_used} + {p.markup_percent}% → {p.price} грн"
            )

        self.stdout.write("")
        self.stdout.write("  Приклад характеристик (specs_json_uk, формат «назва | значення»):")
        sample = Product.objects.filter(sku="CMPX-100301").first()
        if sample:
            self.stdout.write(f"    {sample.sku} — {sample.name_uk}")
            for spec in sample.specs_json_uk[:6]:
                value = f"{spec['v']} {spec['u']}".strip()
                self.stdout.write(f"      [{spec['g']}] {spec['n']:<28} | {value}")
            self.stdout.write(f"      filter_tokens: {sample.filter_tokens[:8]} …")
