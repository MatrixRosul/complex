"""Pydantic-схеми каталогу (Ninja Schema / Pydantic v2).

⚠️ ГРОШІ — РЯДКАМИ, А НЕ float.
   `price: str` ("27445.00"), а не `float`. Ціна в БД — Decimal(12,2); JSON-число довіряти
   не можна: 0.1+0.2 у JS дає 0.30000000000000004, і сума кошика роз'їжджається з сервером на
   копійку. Фронт (types.ts) саме тому і чекає `price: string`.

Схеми ДЗЕРКАЛЯТЬ frontend/src/lib/api/types.ts — це не збіг, а вимога: там уже живуть
53 компоненти, і перейменування поля тут = мовчазний `undefined` там.
"""

from __future__ import annotations

from decimal import Decimal

from ninja import Schema
from pydantic import field_serializer

__all__ = [
    "BrandOut",
    "BrandTopOut",
    "BulkItemIn",
    "BulkOut",
    "CatalogOut",
    "CategoryCollectionOut",
    "CategoryTreeOut",
    "CollectionsOut",
    "CompareOut",
    "FacetGroupOut",
    "ProductDetailOut",
    "ProductListItemOut",
    "SearchOut",
]


# ---------------------------------------------------------------------------
# Довідники
# ---------------------------------------------------------------------------
class BrandOut(Schema):
    id: int
    name: str
    slug: str
    logo_url: str | None = None


class BrandTopOut(BrandOut):
    """Рядок брендів на головній (GET /brands).

    ⚠️ ПОСИЛАННЯ БУДУЄТЬСЯ ПО `slug`, А НЕ ПО `name`: фасет бренду фільтрує по токену
       `brand:{slug}`, тобто працює лише `/catalog?brand=bosch`. `?brand=Bosch` дасть нуль
       товарів (див. services/brands.py).

    ⚠️ `logo_url` майже завжди None — у жодного з 49 брендів логотипа немає. Фронт малює бренд
       типографікою; це не «дірка, яку треба чимось заповнити».
    """

    products_count: int


class CountryOut(Schema):
    id: int
    code: str
    name: str
    slug: str


class BreadcrumbOut(Schema):
    id: int
    external_id: str
    name: str
    slug: str


class CategoryTreeOut(Schema):
    id: int
    external_id: str
    name: str
    slug: str
    parent_id: int | None = None
    depth: int
    products_count: int
    icon_url: str | None = None
    image_url: str | None = None
    children: list[CategoryTreeOut] = []


class ImageOut(Schema):
    id: int
    url: str
    alt: str = ""
    width: int | None = None
    height: int | None = None
    sort_order: int = 0
    is_main: bool = False


class SwatchOut(Schema):
    hex: str
    product_id: int


# ---------------------------------------------------------------------------
# Товар
# ---------------------------------------------------------------------------
class ProductListItemOut(Schema):
    """Картка в сітці. Мінімум полів — без specs і без опису."""

    id: int
    sku: str
    name: str
    slug: str
    price: Decimal
    old_price: Decimal | None = None
    availability: str
    order_lead_days: int | None = None
    condition: int
    is_featured: bool
    installment_available: bool
    installment_max_payments: int | None = None
    main_image_url: str | None = None
    brand: BrandOut | None = None
    swatches: list[SwatchOut] = []

    @field_serializer("price", "old_price")
    def _money(self, value: Decimal | None) -> str | None:
        return None if value is None else f"{value:.2f}"


class SpecRowOut(Schema):
    """Один рядок характеристик.

    ⚠️ `display` — ГОТОВА права колонка («284 мм»). Одиниця вже приклеєна до ЗНАЧЕННЯ
       (INPUTS §2). `name` — ліва колонка, БЕЗ одиниці («Висота», не «Висота (мм)»).
       Фронт бере `name` + `display` і не конкатенує нічого сам.
    """

    code: str
    name: str
    value: str
    unit: str
    display: str
    vn: float | None = None


class SpecGroupOut(Schema):
    """«Основні», «Габаритні розміри» — підзаголовки в блоці характеристик."""

    group: str
    group_sort: int
    rows: list[SpecRowOut]


class VariantItemOut(Schema):
    product_id: int
    label: str
    swatch_hex: str | None = None
    slug: str
    is_active: bool
    availability: str
    is_current: bool = False


class VariantGroupOut(Schema):
    id: int
    axis_label: str
    widget: str
    items: list[VariantItemOut]


class ProductDetailOut(ProductListItemOut):
    """Повна картка."""

    description: str = ""  # rich HTML (санітизований), НЕ фільтрується
    short_description: str = ""
    mpn: str = ""
    barcode: str = ""
    condition_note: str = ""
    stock_qty: int | None = None
    warranty_months: int | None = None

    category: BreadcrumbOut | None = None
    breadcrumbs: list[BreadcrumbOut] = []
    country: CountryOut | None = None

    images: list[ImageOut] = []
    specs: list[SpecGroupOut] = []
    variant_groups: list[VariantGroupOut] = []
    related: list[ProductListItemOut] = []

    # --- габарити УПАКОВКИ: вхід калькулятора Нової Пошти (ADR-021) ---
    # ⚠️ Це НЕ габарити товару. У НП їде тільки упаковка; переплутати = недоплатити за
    #    доставку в 5.5× (виміряно: 2 кг без габаритів = 97.5 грн, з 50×50×60 = 539.5 грн).
    package_weight_kg: Decimal | None = None
    package_width_cm: Decimal | None = None
    package_height_cm: Decimal | None = None
    package_depth_cm: Decimal | None = None

    seo_title: str = ""
    seo_description: str = ""

    @field_serializer(
        "package_weight_kg", "package_width_cm", "package_height_cm", "package_depth_cm"
    )
    def _dims(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


# ---------------------------------------------------------------------------
# Фасети / лістинг
# ---------------------------------------------------------------------------
class FacetValueOut(Schema):
    value: str  # мовонезалежний суфікс токена: "bosch", "300-399", "1"
    label: str  # локалізований підпис: «Bosch», «300–399 л»
    count: int


class FacetGroupOut(Schema):
    code: str
    label: str
    widget: str  # checkbox | switch | range_slider
    unit: str = ""
    group: str = ""  # «Основні», «Габаритні розміри»
    values: list[FacetValueOut]


class PriceRangeOut(Schema):
    min: float
    max: float


class CatalogOut(Schema):
    items: list[ProductListItemOut]
    total: int
    page: int
    page_size: int
    pages: int
    facets: list[FacetGroupOut]
    price_range: PriceRangeOut
    category: CategoryTreeOut | None = None
    breadcrumbs: list[BreadcrumbOut] = []
    subcategories: list[CategoryTreeOut] = []


class SearchOut(Schema):
    items: list[ProductListItemOut]
    total: int
    page: int
    page_size: int
    pages: int
    query: str
    facets: list[FacetGroupOut] = []
    price_range: PriceRangeOut | None = None


# ---------------------------------------------------------------------------
# Bulk (кошик / порівняння / бажання)
# ---------------------------------------------------------------------------
class BulkItemIn(Schema):
    id: int
    qty: int = 1


class BulkLineOut(ProductListItemOut):
    """Рядок bulk-відповіді = картка товару + кількість.

    ⚠️ УСПАДКУВАННЯ, а не копія полів. /wishlist рендерить ту саму `ProductCard`, що й каталог,
       і бере дані з цієї відповіді: свотчі, `condition` (бейдж «Уцінений»), `order_lead_days`
       («Під замовлення 3 дні»). Копія списку полів рано чи пізно розійшлася б із
       ProductListItemOut — і той самий товар виглядав би в бажаннях інакше, ніж у каталозі.
    """

    qty: int
    line_total: Decimal
    is_active: bool

    @field_serializer("line_total")
    def _line_total(self, value: Decimal) -> str:
        return f"{value:.2f}"


class BulkOut(Schema):
    """Відповідь /products/bulk — ЄДИНЕ джерело цін для кошика/порівняння/бажань."""

    items: list[BulkLineOut]
    subtotal: Decimal

    # ⚠️ Чи ВСІ товари підтримують оплату частинами (AND по позиціях, правило з ТЗ).
    #    Рахує СЕРВЕР. Два імені — свідомо:
    #      installment_available_for_all — назва з ТЗ;
    #      installment_allowed           — назва, яку вже чекає фронт (types.ts::CartPreviewResponse).
    #    Одне значення, два ключі: перейменувати одне з них = зламати одного зі споживачів.
    installment_available_for_all: bool
    installment_allowed: bool

    changed_items: list[int] = []
    unavailable_items: list[int] = []

    @field_serializer("subtotal")
    def _money(self, value: Decimal) -> str:
        return f"{value:.2f}"


# ---------------------------------------------------------------------------
# Порівняння
# ---------------------------------------------------------------------------
class CompareRowOut(Schema):
    code: str
    name: str
    unit: str
    values: list[str]  # рядок на КОЖЕН товар, у порядку `products`
    is_different: bool  # ⚠️ де товари розходяться — фронт підсвічує


class CompareGroupOut(Schema):
    group: str
    group_sort: int
    rows: list[CompareRowOut]


class CompareOut(Schema):
    products: list[ProductListItemOut]
    groups: list[CompareGroupOut]


class CategoryCollectionOut(Schema):
    """Карусель однієї категорії на головній.

    `external_id` — не декорація: канонічний URL категорії за ADR-002 — `/{locale}/c/{ext_id}/
    {slug}`, тому кнопка «Дивитись усі» будується саме з нього, а не з `id`.
    """

    id: int
    external_id: str
    name: str
    slug: str
    products: list[ProductListItemOut]


class CollectionsOut(Schema):
    """Добірки для головної. Порожній список = блок на фронті НЕ рендериться.

    ⚠️ `sale` (є old_price) і `clearance` (condition != NEW) — РІЗНІ речі, не плутати:
       акційна ціна vs фізична уцінка товару. Чому не «discounted» — див. services/collections.py.
    """

    featured: list[ProductListItemOut]  # «Хіти продажів» — is_featured
    sale: list[ProductListItemOut]  # «Акції»          — old_price > price
    new: list[ProductListItemOut]  # «Новинки»        — created_at
    clearance: list[ProductListItemOut]  # «Уцінка»         — condition != NEW

    # ⚠️ Дефолт `[]` — щоб старий клієнт, який про це поле не знає, не впав, а новий не мусив
    #    перевіряти на None. Порожньо буває легально: у базі просто немає непорожніх категорій.
    by_category: list[CategoryCollectionOut] = []


class ErrorOut(Schema):
    detail: str
