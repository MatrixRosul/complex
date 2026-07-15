"""Генератор XML-фіда Hotline.

Джерела: docs/research/HOTLINE.md (повна специфікація), INTEGRATIONS.md §2, ADR-013.

────────────────────────────────────────────────────────────────────────────
ЧОМУ XML, А НЕ XLS
────────────────────────────────────────────────────────────────────────────
Тільки XML має `<param>` (характеристики) і `<payment>` (оплата частинами). Правила Hotline
зобов'язують передавати характеристики для унікальних пропозицій — у XLS їх передати нічим,
залишалось би ручне заповнення шаблонів у кабінеті на 3000+ товарів назавжди.

────────────────────────────────────────────────────────────────────────────
🔴 <code> — АРТИКУЛ ВИРОБНИКА (MPN), І ЙОГО ВІДСУТНІСТЬ НЕ Є ПРИЧИНОЮ СКІПУ
────────────────────────────────────────────────────────────────────────────
У v1 порожній `mpn` був умовою скіпу, а заповнювати `mpn` НІЧИМ: колонки «Артикул виробника»
немає ні в прайсі, ні в SpecSheet. Тобто на старті фід був би порожній на 100%, і вся вимога
ТЗ «експорт на Hotline» не працювала б із коробки.

За специфікацією `<code>` — УМОВНО обов'язковий: фід без нього валідний, товари просто гірше
мержаться з картками. Тому: тег віддаємо ЛИШЕ якщо `mpn != ""`, а розмір проблеми показує
лічильник `FeedArtifact.no_mpn_count` (не `skipped_reasons`).

Внутрішній артикул (`sku`) у `<code>` класти ПРЯМО ЗАБОРОНЕНО специфікацією. Він іде в `<id>`.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from django.conf import settings
from django.utils import timezone
from lxml import etree

from catalog.enums import Availability
from catalog.models import Attribute, Product
from core.models import SiteSettings
from feeds.models import HotlineCategory

log = logging.getLogger(__name__)

__all__ = [
    "SKIP_INACTIVE",
    "SKIP_NO_HOTLINE_CATEGORY",
    "SKIP_NO_IMAGE",
    "SKIP_NO_PRICE",
    "SKIP_NO_VENDOR",
    "SKIP_OUT_OF_STOCK",
    "FeedStats",
    "build_feed",
    "feed_queryset",
    "skip_reason",
]

# ---------------------------------------------------------------------------
# Причини скіпу (ключі FeedArtifact.skipped_reasons)
# ---------------------------------------------------------------------------
SKIP_INACTIVE = "inactive"
SKIP_NO_HOTLINE_CATEGORY = "no_hotline_category"
SKIP_NO_PRICE = "no_price"
SKIP_NO_VENDOR = "no_vendor"
SKIP_NO_IMAGE = "no_image"
SKIP_OUT_OF_STOCK = "out_of_stock"
SKIP_BAD_ID = "bad_id"
# ⚠️ Ключа "no_mpn" тут НЕМАЄ і бути не може — див. шапку модуля.

# <stock>: рівно три допустимі значення, формулювання — як у специфікації (HOTLINE §6).
STOCK_LABEL: dict[str, str] = {
    Availability.IN_STOCK: "В наявності",
    Availability.ON_ORDER: "Під замовлення",
    Availability.OUT_OF_STOCK: "Немає",
}

# `Немає` Hotline на сайт не вивантажує → товар у фіді був би мертвим вантажем.
IN_STOCK_SHIPPING_DAYS = "0"  # самовивіз в Ужгороді того ж дня
DEFAULT_LEAD_DAYS = 3  # страховка: CheckConstraint prod_on_order_needs_lead_days це вже гарантує

# <payment type="installment" from="…"> — ФІКСОВАНИЙ список Hotline (HOTLINE §7).
# Провайдер у нас LiqPay = ПриватБанк, тому реально в SiteSettings має стояти "Privatbank".
# Значення поза списком Hotline не приймає — мовчки ігнорує тег.
INSTALLMENT_PROVIDERS = frozenset(
    {
        "Магазин",
        "Monobank",
        "Privatbank",
        "Sense bank",
        "A-bank",
        "PUMB",
        "Oschadbank",
        "OTPBank",
        "Izibank",
        "Ukrsibbank",
    }
)
# Нижній ліміт суми LiqPay: дешевший товар розстрочку не отримає фізично (INTEGRATIONS §2.2.3),
# тому обіцяти її у фіді — брехати покупцеві.
LIQPAY_MIN_INSTALLMENT = Decimal("300")

# ⚡ СЛУЖБОВІ характеристики, які у фід НЕ йдуть. Це не «зайві дані», а ВНУТРІШНЯ ЛОГІСТИКА:
#    «Габарити упаковки (ВхШхГ)» і «Вага в упаковці» існують ЗАРАДИ РОЗРАХУНКУ ДОСТАВКИ Новою
#    Поштою (ADR-021). У каталозі характеристик Hotline таких параметрів немає — модератор
#    отримав би сміття, яке нікуди не змержиться. Гарантія лишається: вона і в <guarantee>,
#    і в характеристиках картки — це справжня властивість товару.
EXCLUDED_PARAM_ROLES = frozenset({Attribute.Role.PACKAGE_DIMS, Attribute.Role.PACKAGE_WEIGHT})

# <id>: ≤20 символів, тільки [0-9a-zA-Z_-] (HOTLINE §1.3).
ITEM_ID_RE = re.compile(r"^[0-9a-zA-Z_-]{1,20}$")
# Заборонені ASCII 0–31, крім 9/10/13 (HOTLINE §1.5).
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# HTML-розмітка заборонена В УСІХ БЕЗ ВИНЯТКУ елементах.
# ⚠️ Патерн НАВМИСНО вузький (`<` + ім'я тега), а не `<[^>]*>`: жадібний варіант з'їдав би
#    звичайний текст із порівняннями — «Об'єм < 300 л» перетворювався на «Об'єм 300 л»
#    (перевірено тестом test_escaping_and_html_stripping). Голі `<` і `>` — легальний текст,
#    їх екранує lxml; вирізати треба саме РОЗМІТКУ.
HTML_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9:-]*(?:\s[^<>]*)?/?>")


@dataclass
class FeedStats:
    """Лічильники генерації. Лягають у FeedArtifact — без них діагностика зводиться до
    «у фіді 200 товарів замість 3000, і невідомо чому»."""

    items: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    no_mpn: int = 0
    default_dims: int = 0

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())

    def as_reasons(self) -> dict[str, int]:
        return dict(self.skipped)


# ---------------------------------------------------------------------------
# Хелпери
# ---------------------------------------------------------------------------
def clean_text(value: Any) -> str:
    """Плоский текст без HTML і без керуючих символів (HOTLINE §1.5).

    Екранування `<`, `>`, `&`, лапок робить lxml при серіалізації — руками цього робити НЕ треба
    (інакше отримаємо подвійне екранування виду `&amp;lt;`).
    """
    if value is None:
        return ""
    text = HTML_TAG_RE.sub(" ", str(value))
    text = CONTROL_CHARS_RE.sub("", text)
    return " ".join(text.split())


def absolute_url(url: str) -> str:
    """Абсолютний URL. Відносні шляхи (MEDIA_URL) склеюємо з SITE_URL."""
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(settings.SITE_URL.rstrip("/") + "/", url.lstrip("/"))


def format_price(price: Decimal) -> str:
    """`6 600,00` → `6600`. Роздільники розрядів у ціні ЗАБОРОНЕНІ (HOTLINE §1.3)."""
    quantized = Decimal(price).normalize()
    text = f"{quantized:f}"
    return text if "." not in text else text.rstrip("0").rstrip(".")


def product_url(product: Product) -> str:
    """Тільки HTTPS і тільки УКРАЇНОМОВНА сторінка (HOTLINE §1.3). Роут: /ua/p/{id}/{slug}."""
    slug = getattr(product, "slug_uk", None) or product.slug or ""
    return absolute_url(f"/ua/p/{product.pk}/{slug}".rstrip("/"))


def image_url(product: Product) -> str:
    """ОДНЕ зображення на товар — галереї у фіді немає в жодному форматі (HOTLINE §1.6).

    Джерело — денормалізований `main_image_url` (його пише rebuild_product_denorm з головного
    фото), з фолбеком на саме фото: денормалізація могла ще не добігти.
    """
    if product.main_image_url:
        return absolute_url(product.main_image_url)

    main = next((i for i in product.images.all() if i.is_main), None)
    if main is None:
        return ""
    for candidate in (main.file_card, main.file_large, main.file):
        if candidate:
            return absolute_url(candidate.url)
    return ""


def _vendor(product: Product) -> str:
    """`<vendor>` — рівно один виробник. Країну класти ЗАБОРОНЕНО (HOTLINE §1.3)."""
    return clean_text(product.brand.name) if product.brand_id else ""


def skip_reason(product: Product) -> str | None:
    """Чому товар НЕ потрапить у фід. None → потрапить.

    ⚡ Порожній `mpn` тут не перевіряється НІКОЛИ (див. шапку модуля).
    """
    category = product.category
    if not product.is_active or not category.is_active:
        return SKIP_INACTIVE
    if category.hotline_category_id is None:
        # Hotline прив'язує товар до картки по назві ЛИСТОВОЇ категорії свого рубрикатора.
        # Без прив'язки товар не приземлиться нікуди — сенсу віддавати його немає.
        return SKIP_NO_HOTLINE_CATEGORY
    if product.price is None or product.price <= 0:
        return SKIP_NO_PRICE
    if not _vendor(product):
        return SKIP_NO_VENDOR  # <vendor> обов'язковий — item без нього невалідний
    if not image_url(product):
        return SKIP_NO_IMAGE
    if product.availability == Availability.OUT_OF_STOCK:
        # `Немає` Hotline на сайт не вивантажує (HOTLINE §6) — тег був би холостим.
        return SKIP_OUT_OF_STOCK
    if not ITEM_ID_RE.match(str(product.pk)):
        return SKIP_BAD_ID
    return None


def hotline_param_names() -> dict[str, str | None]:
    """`Attribute.code` → назва параметра для Hotline.

    `hotline_param_name` заповнює контент-менеджер, коли назва в нашому каталозі не збігається
    з назвою в каталозі Hotline (а вони мають збігатися — інакше параметр не змержиться).
    Порожньо → віддаємо власну українську назву.

    Значення `None` = характеристику у фід НЕ віддавати (див. EXCLUDED_PARAM_ROLES).
    """
    rows = Attribute.objects.values_list("code", "hotline_param_name", "name_uk", "name", "role")
    return {
        code: (None if role in EXCLUDED_PARAM_ROLES else (hl or name_uk or name or ""))
        for code, hl, name_uk, name, role in rows
    }


def _append_params(
    item: etree._Element, product: Product, param_names: dict[str, str | None]
) -> None:
    """`<param>` — характеристики. Саме заради них обрано XML.

    ⚠️ ПРАВИЛО РЕНДЕРУ (INPUTS §2): одиниця клеїться до ЗНАЧЕННЯ, а не до назви.
       `<param name="Висота">1635 мм</param>`, а НЕ `<param name="Висота (мм)">1635</param>`.
       Той самий формат — у картці, у порівнянні й тут. Джерело — денормалізований
       specs_json_uk (мова фіда — державна).
    """
    # Країна виготовлення — окремо задокументований параметр Hotline (§1.3).
    if product.country_id:
        country = clean_text(getattr(product.country, "name_uk", None) or product.country.name)
        if country:
            etree.SubElement(item, "param", name="Країна виготовлення").text = country

    specs = getattr(product, "specs_json_uk", None) or product.specs_json or []
    for row in specs:
        if not isinstance(row, dict):
            continue
        code = row.get("code", "")
        if code in param_names:
            raw_name = param_names[code]
            if raw_name is None:
                continue  # службова характеристика (габарити/вага упаковки) — у фід не йде
        else:
            # Характеристики немає в БД, але вона лишилась у застарілому specs_json.
            raw_name = row.get("n", "")
        name = clean_text(raw_name)
        value = clean_text(f"{row.get('v', '')} {row.get('u', '')}")
        if not name or not value:
            continue
        etree.SubElement(item, "param", name=name).text = value


def _append_guarantee(item: etree._Element, product: Product) -> None:
    """Гарантія — ЗАВЖДИ від виробника (замовник підтвердив, INPUTS §1).

    Термін — з характеристики «Гарантійний термін (міс)» (Attribute.role=WARRANTY_MONTHS
    проєктується в Product.warranty_months синком).

    ⚡ Немає характеристики → тег ОПУСКАЄМО. Дефолт 12 НЕ підставляємо: гарантія — це обіцянка
       покупцеві від імені магазину, вигадувати її не можна.
    """
    if not product.warranty_months:
        return
    el = etree.SubElement(item, "guarantee", type="manufacturer")
    el.text = str(product.warranty_months)


def _append_payments(item: etree._Element, product: Product, site: SiteSettings) -> None:
    """Оплата частинами + комісії (HOTLINE §7).

    ⚡ `fee="0"` на pay-card — не щедрість, а вимога LiqPay: surcharge (перекладання комісії
       еквайрингу на покупця) прямо заборонений умовами. Ціна у фіді = ціна на сайті.
    """
    if product.installment_available:
        providers = [
            p.strip()
            for p in (site.installment_provider or "").split(",")
            if p.strip() in INSTALLMENT_PROVIDERS
        ]
        min_payment = max(site.installment_min_payment or Decimal(0), LIQPAY_MIN_INSTALLMENT)
        # Товар дешевший за мінімальний платіж розстрочку не отримає фізично → не обіцяємо її.
        if providers and product.price >= min_payment:
            el = etree.SubElement(
                item,
                "payment",
                attrib={
                    "type": "installment",
                    "from": ", ".join(providers),  # 'from' — ключове слово Python, тому attrib=
                    "min_payment": format_price(min_payment),
                    "max_period": str(
                        product.installment_max_payments or site.installment_max_period
                    ),
                    "no_down_payment": "true",  # ОЧ від ПриватБанку — без першого внеску
                },
            )
            el.text = "true"

    etree.SubElement(item, "payment", attrib={"type": "pay-card", "fee": "0"}).text = "true"


def build_item(
    item: etree._Element,
    product: Product,
    *,
    site: SiteSettings,
    param_names: dict[str, str | None],
) -> None:
    """Наповнює `<item>`. Порядок тегів — як в офіційному прикладі Hotline."""
    etree.SubElement(item, "id").text = str(product.pk)
    etree.SubElement(item, "categoryId").text = str(product.category.hotline_category_id)

    # ⚡ <code> — ТІЛЬКИ MPN і ТІЛЬКИ якщо він є. Внутрішній артикул сюди класти заборонено.
    if product.mpn:
        etree.SubElement(item, "code").text = clean_text(product.mpn)
    if product.barcode:
        etree.SubElement(item, "barcode").text = clean_text(product.barcode)

    etree.SubElement(item, "vendor").text = _vendor(product)
    etree.SubElement(item, "name").text = clean_text(
        getattr(product, "name_uk", None) or product.name
    )
    etree.SubElement(item, "url").text = product_url(product)
    etree.SubElement(item, "image").text = image_url(product)
    etree.SubElement(item, "priceRUAH").text = format_price(product.price)

    etree.SubElement(item, "stock").text = STOCK_LABEL[product.availability]
    if product.availability == Availability.ON_ORDER:
        # Для «Під замовлення» <shipping> ОБОВ'ЯЗКОВИЙ (HOTLINE §6).
        days = product.order_lead_days or DEFAULT_LEAD_DAYS
        etree.SubElement(item, "shipping").text = str(days)
    else:
        etree.SubElement(item, "shipping").text = IN_STOCK_SHIPPING_DAYS

    _append_guarantee(item, product)
    etree.SubElement(item, "condition").text = str(int(product.condition))
    _append_params(item, product, param_names)
    _append_payments(item, product, site)


# ---------------------------------------------------------------------------
# Категорії — з РУБРИКАТОРА Hotline, а не з нашої маркетингової структури
# ---------------------------------------------------------------------------
def _category_chain(used_ids: set[int]) -> list[HotlineCategory]:
    """Використані листові категорії + УСІ їхні предки (parentId має бути розв'язним).

    `<category><name>` — назва з рубрикатора Hotline, `<id>` — pk нашого HotlineCategory
    (ціле, ≤18 знаків, унікальне і незмінне між вивантаженнями — саме це й дає bigserial).
    """
    resolved: dict[int, HotlineCategory] = {}
    pending = set(used_ids)
    while pending:
        nodes = HotlineCategory.objects.filter(pk__in=pending)
        pending = set()
        for node in nodes:
            resolved[node.pk] = node
            if node.parent_id and node.parent_id not in resolved:
                pending.add(node.parent_id)
    return sorted(resolved.values(), key=lambda c: (c.depth, c.pk))


def _append_shop_blocks(root: etree._Element, site: SiteSettings) -> None:
    """`<delivery>` і `<store>` рівня магазину (HOTLINE §1.4).

    ⚠️ carrier — тільки з enum `SLF|ME|NP|UP`. Перевізника «Delivery» в enum НЕМАЄ, тому у фід
       він не йде взагалі (спроба вигадати код = мовчки відкинутий рядок доставки).
    """
    from cms.models import PickupPoint  # локально: cms → feeds імпорту немає, циклу не буде

    etree.SubElement(root, "delivery", attrib={"id": "1", "type": "pickup", "cost": "0"})
    etree.SubElement(
        root,
        "delivery",
        attrib={"id": "2", "type": "warehouse", "carrier": "NP", "cost": "null"},
    )
    etree.SubElement(
        root,
        "delivery",
        attrib={"id": "3", "type": "pochtomat", "carrier": "NP", "cost": "null"},
    )

    for n, point in enumerate(PickupPoint.objects.filter(is_active=True).order_by("sort_order"), 1):
        attrib = {
            "id": str(n),
            "name": clean_text(getattr(point, "name_uk", None) or point.name),
            "address": clean_text(getattr(point, "address_uk", None) or point.address),
        }
        if point.latitude is not None and point.longitude is not None:
            attrib["coordinates"] = f"{point.latitude},{point.longitude}"
        etree.SubElement(root, "store", attrib=attrib).text = ""


def feed_queryset() -> Any:
    """Товари-кандидати: ТІЛЬКИ `hotline_enabled=True`.

    Вимкнений тумблер — не «пропуск», а свідоме рішення менеджера: у skipped_reasons він не
    рахується (інакше лічильник пропущених дорівнював би розміру каталогу і нічого не значив).
    """
    return (
        Product.objects.filter(hotline_enabled=True)
        .select_related("category", "category__hotline_category", "brand", "country")
        .prefetch_related("images")
        .order_by("pk")
    )


def build_feed(
    products: Iterable[Product] | None = None,
    *,
    site: SiteSettings | None = None,
    generated_at: datetime | None = None,
) -> tuple[bytes, FeedStats]:
    """Будує XML і повертає (bytes, лічильники). У БД нічого не пише — це чиста функція."""
    site = site or SiteSettings.get_solo()
    products = feed_queryset().iterator(chunk_size=500) if products is None else products
    generated_at = generated_at or timezone.localtime()
    param_names = hotline_param_names()

    stats = FeedStats()
    items: list[etree._Element] = []
    used_categories: set[int] = set()

    for product in products:
        reason = skip_reason(product)
        if reason:
            stats.skipped[reason] += 1
            log.debug("hotline_skip product=%s reason=%s", product.pk, reason)
            continue

        item = etree.Element("item")
        build_item(item, product, site=site, param_names=param_names)
        items.append(item)

        used_categories.add(product.category.hotline_category_id)
        stats.items += 1
        if not product.mpn:
            stats.no_mpn += 1  # ЛІЧИЛЬНИК, не причина скіпу
        if not all(
            (
                product.package_weight_kg,
                product.package_width_cm,
                product.package_height_cm,
                product.package_depth_cm,
            )
        ):
            # Категорія, де >20% товарів на дефолтних габаритах, — це категорія, де магазин
            # СИСТЕМНО недоплачує за доставку (INTEGRATIONS §1.7). Метрику треба міряти.
            stats.default_dims += 1

    root = etree.Element("price")
    etree.SubElement(root, "date").text = generated_at.strftime("%Y-%m-%d %H:%M")  # строго так
    if site.hotline_firm_name:
        etree.SubElement(root, "firmName").text = clean_text(site.hotline_firm_name)
    if site.hotline_firm_id:
        etree.SubElement(root, "firmId").text = clean_text(site.hotline_firm_id)

    _append_shop_blocks(root, site)

    categories_el = etree.SubElement(root, "categories")  # обов'язковий, навіть порожній
    # Ланцюг ВЖЕ містить усіх предків (див. _category_chain), тому parentId завжди розв'язний.
    # Сортування по depth гарантує, що батько оголошений РАНІШЕ за дитину.
    for node in _category_chain(used_categories):
        cat_el = etree.SubElement(categories_el, "category")
        etree.SubElement(cat_el, "id").text = str(node.pk)
        if node.parent_id:
            etree.SubElement(cat_el, "parentId").text = str(node.parent_id)
        etree.SubElement(cat_el, "name").text = clean_text(node.name)

    items_el = etree.SubElement(root, "items")  # обов'язковий
    for item in items:
        items_el.append(item)

    body = etree.tostring(root, encoding="UTF-8", pretty_print=True, xml_declaration=False)
    # Декларацію пишемо самі: lxml ставить одинарні лапки (version='1.0'), а в специфікації
    # Hotline перший рядок — рівно `<?xml version="1.0" encoding="UTF-8"?>`, з нульового символу.
    xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + body

    log.info(
        "hotline_feed_built items=%s skipped=%s no_mpn=%s size=%s",
        stats.items,
        stats.as_reasons(),
        stats.no_mpn,
        len(xml),
    )
    return xml, stats
