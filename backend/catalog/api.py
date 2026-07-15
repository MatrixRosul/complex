"""API каталогу (Django Ninja).

⚠️ БІЗНЕС-ЛОГІКИ ТУТ НЕМАЄ. В'юхи роблять рівно три речі: розбирають запит, кличуть сервіс,
   серіалізують. Уся логіка — в `catalog/services/*` (facets, listing, cards, search, tree).
   Це не естетика: ті самі сервіси зватимуть Celery-задачі (фід Hotline), адмінка і майбутній
   checkout. Логіка, що живе у в'юсі, недоступна нікому, крім HTTP.

⚠️ ПОРЯДОК РЕЄСТРАЦІЇ МАРШРУТІВ КРИТИЧНИЙ.
   `/catalog/{category_ref}` — це catch-all по рядку. Якби він стояв перед `/catalog/products`,
   то запит `/catalog/products` шукав би КАТЕГОРІЮ зі slug'ом «products» і чесно віддавав 404.
   Django резолвить URL у порядку реєстрації → статичні шляхи ОГОЛОШЕНІ ВИЩЕ за динамічні.
   Те саме з `/products/bulk` проти `/products/{id}`.

Дві групи шляхів — свідомо:
  * **основні** (`/categories/tree`, `/catalog/{ref}`, `/products/{id}`, `/products/bulk`,
    `/search`, `/compare`) — контракт із ТЗ;
  * **сумісні** (`/catalog/categories`, `/catalog/products…`, `/cart/preview`) — рівно ті шляхи,
    які вже зашиті у `frontend/src/lib/api/http.ts` (53 компоненти на моках). Це ті самі сервіси,
    інший URL. Без них перемикання фронту з моків на API = переписування http.ts.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from catalog.models import Category, Product
from catalog.schemas import (
    BrandTopOut,
    BulkItemIn,
    BulkOut,
    CatalogOut,
    CategoryTreeOut,
    CollectionsOut,
    CompareOut,
    ProductDetailOut,
    ProductListItemOut,
    SearchOut,
)
from catalog.services import brands, cards, collections, listing, search, tree
from catalog.services.facets import build_facet_groups, get_facet_meta, parse_facet_params
from catalog.services.lang import normalize_lang, tr

router = Router(tags=["catalog"])

MAX_COMPARE = 8
MAX_BULK = 200


# ---------------------------------------------------------------------------
# Серіалізація моделей → dict (мова застосовується ТУТ, явним аргументом)
# ---------------------------------------------------------------------------
def _list_item(product: Product, lang: str) -> dict[str, Any]:
    """Тонкий алias. Сама серіалізація живе в `cards.list_item` — її ділять з добірками для
    головної, які кешуються вже серіалізованими (див. services/collections.py)."""
    return cards.list_item(product, lang)


def _crumb(category: Category, lang: str) -> dict[str, Any]:
    return {
        "id": category.pk,
        "external_id": category.external_id,
        "name": tr(category, "name", lang),
        "slug": tr(category, "slug", lang),
    }


def _images(product: Product, lang: str) -> list[dict[str, Any]]:
    """Галерея.

    ⚠️ ФОЛБЕК НА `main_image_url`. У БД зараз НУЛЬ рядків `ProductImage` (задача завантаження
       фото ще не написана), а `Product.main_image_url` заповнений. Без цього фолбеку картка
       товару віддавала б порожню галерею — тобто товар без жодного фото.
    """
    images: list[dict[str, Any]] = []

    for image in product.images.all():
        url = ""
        for candidate in (image.file_large, image.file_card, image.file):
            try:
                if candidate:
                    url = candidate.url
                    break
            except ValueError:
                continue
        if not url:
            continue
        images.append(
            {
                "id": image.pk,
                "url": url,
                "alt": tr(image, "alt", lang) or tr(product, "name", lang),
                "width": image.width,
                "height": image.height,
                "sort_order": image.position,
                "is_main": image.is_main,
            }
        )

    if not images and product.main_image_url:
        images.append(
            {
                "id": 0,
                "url": product.main_image_url,
                "alt": tr(product, "name", lang),
                "width": None,
                "height": None,
                "sort_order": 0,
                "is_main": True,
            }
        )

    return images


def _detail(product: Product, lang: str) -> dict[str, Any]:
    data = _list_item(product, lang)

    crumbs = [_crumb(c, lang) for c in listing.breadcrumbs(product.category)]

    data.update(
        {
            "description": tr(product, "description", lang) or "",
            "short_description": tr(product, "short_description", lang) or "",
            "mpn": product.mpn,
            "barcode": product.barcode,
            "condition_note": tr(product, "condition_note", lang) or "",
            "stock_qty": product.stock_qty,
            "warranty_months": product.warranty_months,
            "category": _crumb(product.category, lang),
            "breadcrumbs": crumbs,
            "country": (
                {
                    "id": product.country.pk,
                    "code": product.country.code,
                    "name": tr(product.country, "name", lang),
                    "slug": product.country.slug,
                }
                if product.country_id
                else None
            ),
            "images": _images(product, lang),
            "specs": cards.grouped_specs(product, lang),
            "variant_groups": cards.variant_groups(product, lang),
            "related": [_list_item(p, lang) for p in cards.related_products(product, lang)],
            "package_weight_kg": product.package_weight_kg,
            "package_width_cm": product.package_width_cm,
            "package_height_cm": product.package_height_cm,
            "package_depth_cm": product.package_depth_cm,
            "seo_title": tr(product, "seo_title", lang) or "",
            "seo_description": tr(product, "seo_description", lang) or "",
        }
    )
    return data


# ---------------------------------------------------------------------------
# Розбір query-string
# ---------------------------------------------------------------------------
def _decimal(raw: str | None) -> Decimal | None:
    if raw in (None, ""):
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        # Сміття в ?price_min=abc не має валити каталог 500-ю — просто ігноруємо фільтр.
        return None


def _int(raw: str | None, default: int) -> int:
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _build_filters(
    request: HttpRequest,
    lang: str,
    category: Category | None,
    *,
    search_qs=None,
) -> tuple[listing.CatalogFilters, dict[str, Any]]:
    meta = get_facet_meta(lang)
    # .lists() (а не .dict()) — інакше з ?brand=bosch&brand=gorenje вижило б лише останнє
    # значення, і мультивибір у фасеті мовчки перетворився б на одиночний.
    params = dict(request.GET.lists())
    selected = parse_facet_params(params, meta)

    filters = listing.CatalogFilters(
        category=category,
        selected=selected,
        price_min=_decimal(request.GET.get("price_min")),
        price_max=_decimal(request.GET.get("price_max")),
        sort=request.GET.get("sort") or listing.DEFAULT_SORT,
        page=max(1, _int(request.GET.get("page"), 1)),
        page_size=_int(request.GET.get("page_size"), listing.DEFAULT_PAGE_SIZE),
        search_qs=search_qs,
    )
    return filters, meta


def _catalog_payload(
    request: HttpRequest, lang: str, category: Category | None, *, search_qs=None
) -> dict[str, Any]:
    filters, meta = _build_filters(request, lang, category, search_qs=search_qs)
    result = listing.list_catalog(filters)

    subcategories: list[dict[str, Any]] = []
    category_out = None
    if category is not None:
        category_out = {
            **_crumb(category, lang),
            "parent_id": category.parent_id,
            "depth": category.depth,
            "products_count": result["total"],
            "icon_url": tree.icon_url(category),
            "image_url": tree.image_url(category),
            "children": [],
        }
        # ⚠️ Чипси підкатегорій беремо з КЕШОВАНОГО дерева, а не окремим запитом: там уже
        #    відсіяні порожні (правило «порожня категорія не показується ніде») і вже пораховані
        #    лічильники з плитками. Свій запит сюди повернув би в каталог рівно ті пункти, які ми
        #    щойно прибрали з меню.
        subcategories = tree.visible_children(lang, category)

    return {
        "items": [_list_item(p, lang) for p in result["items"]],
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
        "pages": result["pages"],
        "facets": build_facet_groups(result["counts"], filters.selected, meta, lang),
        "price_range": result["price_range"],
        "category": category_out,
        "breadcrumbs": [_crumb(c, lang) for c in result["breadcrumbs"]],
        "subcategories": subcategories,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 1. Дерево категорій
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/categories/tree", response=list[CategoryTreeOut], summary="Дерево категорій")
def categories_tree(request: HttpRequest, lang: str = "uk"):
    """Мегаменю: дерево з міні-емблемами і лічильниками. Кеш Redis, TTL 1 год."""
    return tree.get_category_tree(normalize_lang(lang))


@router.get("/catalog/categories", response=list[CategoryTreeOut], include_in_schema=False)
def categories_tree_compat(request: HttpRequest, lang: str = "uk"):
    """Сумісність: цей шлях зашитий у frontend/src/lib/api/http.ts::getCategoryTree."""
    return tree.get_category_tree(normalize_lang(lang))


# ═══════════════════════════════════════════════════════════════════════════
# 1b. Бренди (рядок логотипів на головній)
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/brands", response=list[BrandTopOut], summary="Топ брендів за кількістю товарів")
def brands_top(request: HttpRequest, limit: int = brands.DEFAULT_LIMIT):
    """Бренди, у яких Є активні товари, — за спаданням кількості.

    ⚠️ БЕЗ `lang`: назва бренду — власна і не перекладається (DATA_MODEL §2.2).

    ⚠️ Посилання з плитки бренду — `/catalog?brand={slug}`. Саме `slug`, бо фасет фільтрує по
       токену `brand:{slug}`; `?brand=Bosch` віддасть порожній каталог.
    """
    return brands.get_top_brands(limit)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Bulk — кошик / порівняння / бажання   (ДО /products/{id}!)
# ═══════════════════════════════════════════════════════════════════════════
@router.post("/products/bulk", response=BulkOut, summary="Актуальні ціни й наявність (кошик)")
def products_bulk(request: HttpRequest, payload: list[BulkItemIn], lang: str = "uk"):
    """[{id, qty}] → актуальні ціни, наявність, назви, фото, сума, оплата частинами.

    ⚠️ Це ЄДИНЕ джерело цін для кошика. У localStorage фронт тримає тільки {id, qty} —
       інакше показував би ціни, які синк перезаписав кілька годин тому.
    """
    if len(payload) > MAX_BULK:
        raise HttpError(400, f"Максимум {MAX_BULK} позицій за раз")

    result = cards.bulk_products([(item.id, item.qty) for item in payload], normalize_lang(lang))
    # Одне значення під двома іменами (див. коментар у schemas.BulkOut).
    result["installment_available_for_all"] = result["installment_allowed"]
    return result


@router.get("/products/bulk", response=BulkOut, summary="Те саме, але ?ids=1,2,3 (GET)")
def products_bulk_get(request: HttpRequest, ids: str = "", lang: str = "uk"):
    """GET-варіант для compare/wishlist — їх зручніше тримати в URL, ніж у POST-тілі.

    Толерантний до неіснуючих id: вони йдуть у `unavailable_items`, а не в 404.
    """
    parsed = _parse_ids(ids)
    result = cards.bulk_products([(i, 1) for i in parsed], normalize_lang(lang))
    result["installment_available_for_all"] = result["installment_allowed"]
    return result


@router.post("/cart/preview", response=BulkOut, include_in_schema=False)
def cart_preview_compat(request: HttpRequest, payload: list[BulkItemIn], lang: str = "uk"):
    """Сумісність: frontend/src/lib/api/http.ts::previewCart (ADR-017)."""
    return products_bulk(request, payload, lang)


# ═══════════════════════════════════════════════════════════════════════════
# Добірки для головної (ДО /products/{id}!)
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/products/collections", response=CollectionsOut, summary="Добірки для головної")
def products_collections(
    request: HttpRequest, lang: str = "uk", limit: int = collections.DEFAULT_LIMIT
):
    """{featured, sale, new, clearance} — усі чотири блоки головної ОДНИМ запитом.

    Головна раніше робила 2 окремі HTTP-виклики (featured + discounted) і не мала кешу взагалі.
    Тепер це один виклик, кешований у Redis на 5 хв.

    ⚠️ Порожня добірка — це `[]`, а не помилка. Фронт просто не рендерить блок (порожній блок
       з заголовком «Акції» і нічим під ним — гірше, ніж відсутній блок).
    """
    return collections.get_collections(normalize_lang(lang), limit)


@router.get("/products/featured", response=list[ProductListItemOut], summary="Рекомендовані")
def products_featured(
    request: HttpRequest, lang: str = "uk", limit: int = collections.DEFAULT_LIMIT
):
    """Легасі-роут (лишається заради http.ts::getFeaturedProducts). Дані — з того ж кешу."""
    return collections.get_collections(normalize_lang(lang), limit)["featured"]


@router.get("/products/discounted", response=list[ProductListItemOut], summary="Зі знижкою")
def products_discounted(
    request: HttpRequest, lang: str = "uk", limit: int = collections.DEFAULT_LIMIT
):
    """Легасі-роут: знижка = є `old_price`. Це добірка `sale`, а НЕ уцінка (`clearance`).

    ⚠️ Історична назва. «Discounted» тут означає «є перекреслена стара ціна», а не
       «Product.condition = Уцінений». Через цю двозначність у /products/collections блоки
       звуться `sale` і `clearance` — див. services/collections.py.
    """
    return collections.get_collections(normalize_lang(lang), limit)["sale"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. Картка товару
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/products/{int:product_id}", response=ProductDetailOut, summary="Картка товару")
def product_detail(request: HttpRequest, product_id: int, lang: str = "uk"):
    lang = normalize_lang(lang)
    product = cards.product_detail_qs().filter(pk=product_id, is_active=True).first()
    if product is None:
        raise HttpError(404, "Товар не знайдено")
    return _detail(product, lang)


@router.get(
    "/products/{int:product_id}/related",
    response=list[ProductListItemOut],
    summary="Супутні товари",
)
def product_related(request: HttpRequest, product_id: int, lang: str = "uk"):
    lang = normalize_lang(lang)
    product = cards.product_detail_qs().filter(pk=product_id, is_active=True).first()
    if product is None:
        raise HttpError(404, "Товар не знайдено")
    return [_list_item(p, lang) for p in cards.related_products(product, lang)]


# ═══════════════════════════════════════════════════════════════════════════
# 6. Порівняння
# ═══════════════════════════════════════════════════════════════════════════
def _parse_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if value not in ids:
            ids.append(value)
    return ids


@router.get("/compare", response=CompareOut, summary="Порівняльна таблиця")
def compare(request: HttpRequest, ids: str = "", lang: str = "uk"):
    """Спільні характеристики + позначка `is_different` там, де значення розходяться."""
    lang = normalize_lang(lang)
    parsed = _parse_ids(ids)[:MAX_COMPARE]
    if not parsed:
        return {"products": [], "groups": []}

    result = cards.compare_products(parsed, lang)
    return {
        "products": [_list_item(p, lang) for p in result["products"]],
        "groups": result["groups"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 5. Пошук
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/search", response=SearchOut, summary="Пошук (FTS + trigram)")
def search_view(request: HttpRequest, q: str = "", lang: str = "uk"):
    """Повнотекстовий пошук по `search_vector_{lang}` + trigram-фолбек на одруківки.

    Фасети працюють і тут: пошук — це просто інший спосіб звузити базовий набір.
    """
    lang = normalize_lang(lang)
    q = (q or "").strip()

    if not q:
        return {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": listing.DEFAULT_PAGE_SIZE,
            "pages": 1,
            "query": "",
            "facets": [],
            "price_range": {"min": 0.0, "max": 0.0},
        }

    payload = _catalog_payload(request, lang, None, search_qs=search.search_products(q, lang))
    return {
        "items": payload["items"],
        "total": payload["total"],
        "page": payload["page"],
        "page_size": payload["page_size"],
        "pages": payload["pages"],
        "query": q,
        "facets": payload["facets"],
        "price_range": payload["price_range"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 2. Лістинг категорії  (catch-all — РЕЄСТРУЄТЬСЯ ОСТАННІМ у групі /catalog/*)
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/catalog/products", response=CatalogOut, include_in_schema=False)
def catalog_products_compat(request: HttpRequest, lang: str = "uk"):
    """Сумісність: http.ts::getCatalog шле `?category={external_id}`, а не шлях."""
    lang = normalize_lang(lang)

    category = None
    ref = request.GET.get("category")
    if ref:
        category = tree.resolve_category(ref)
        if category is None:
            raise HttpError(404, "Категорію не знайдено")

    q = (request.GET.get("q") or "").strip()
    search_qs = search.search_products(q, lang) if q else None

    return _catalog_payload(request, lang, category, search_qs=search_qs)


# --- решта шляхів /catalog/products/* з http.ts (той самий порядок: bulk/featured/discounted
#     ОБОВ'ЯЗКОВО перед {product_id}, інакше «bulk» поїде в конвертер int і дасть 404) ---
@router.get("/catalog/products/bulk", response=BulkOut, include_in_schema=False)
def catalog_products_bulk_compat(request: HttpRequest, ids: str = "", lang: str = "uk"):
    """Сумісність: http.ts::getProductsBulk (compare / wishlist)."""
    return products_bulk_get(request, ids, lang)


@router.get("/catalog/products/collections", response=CollectionsOut, include_in_schema=False)
def catalog_products_collections_compat(
    request: HttpRequest, lang: str = "uk", limit: int = collections.DEFAULT_LIMIT
):
    """Сумісність: http.ts::getCollections (той самий префікс, що й решта /catalog/products/*)."""
    return products_collections(request, lang, limit)


@router.get("/catalog/products/featured", response=list[ProductListItemOut], include_in_schema=False)
def catalog_products_featured_compat(
    request: HttpRequest, lang: str = "uk", limit: int = collections.DEFAULT_LIMIT
):
    """Сумісність: http.ts::getFeaturedProducts."""
    return products_featured(request, lang, limit)


@router.get(
    "/catalog/products/discounted", response=list[ProductListItemOut], include_in_schema=False
)
def catalog_products_discounted_compat(
    request: HttpRequest, lang: str = "uk", limit: int = collections.DEFAULT_LIMIT
):
    """Сумісність: http.ts::getDiscountedProducts."""
    return products_discounted(request, lang, limit)


@router.get(
    "/catalog/products/{int:product_id}", response=ProductDetailOut, include_in_schema=False
)
def catalog_product_detail_compat(request: HttpRequest, product_id: int, lang: str = "uk"):
    """Сумісність: http.ts::getProduct."""
    return product_detail(request, product_id, lang)


@router.get(
    "/catalog/products/{int:product_id}/related",
    response=list[ProductListItemOut],
    include_in_schema=False,
)
def catalog_product_related_compat(request: HttpRequest, product_id: int, lang: str = "uk"):
    """Сумісність: http.ts::getRelatedProducts."""
    return product_related(request, product_id, lang)


@router.get("/catalog/{category_ref}", response=CatalogOut, summary="Лістинг категорії")
def catalog(request: HttpRequest, category_ref: str, lang: str = "uk"):
    """Товари категорії ВКЛЮЧНО З ПІДКАТЕГОРІЯМИ (path__startswith) + фасети з лічильниками.

    `category_ref` — slug АБО external_id (ADR-002: у канонічному URL стоїть саме ext_id).

    Фасети: мультивибір усередині групи = OR (`&&`), між групами = AND.
    Лічильники — drop-self і з ТОГО САМОГО QuerySet, що й товари (ADR-008).
    """
    lang = normalize_lang(lang)

    category = tree.resolve_category(category_ref)
    if category is None:
        raise HttpError(404, "Категорію не знайдено")

    q = (request.GET.get("q") or "").strip()
    search_qs = search.search_products(q, lang) if q else None

    return _catalog_payload(request, lang, category, search_qs=search_qs)
