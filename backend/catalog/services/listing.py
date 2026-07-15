"""Лістинг категорії + фасети з лічильниками (ADR-007, ADR-008).

⚠️ ГОЛОВНА ІНВАРІАНТА ЦЬОГО МОДУЛЯ — ОДНЕ ДЖЕРЕЛО
--------------------------------------------------
Список товарів і лічильники фасетів рахуються з ОДНОГО І ТОГО САМОГО QuerySet.
Технічно це забезпечено так: `token_counts()` не будує свій запит, а бере ВЖЕ ЗІБРАНИЙ
QuerySet і підставляє його як підзапит:

    SELECT t.token, count(*)
    FROM catalog_product p, unnest(p.filter_tokens) AS t(token)
    WHERE p.id IN ( <ТОЧНО ТОЙ САМИЙ SQL, ЩО ВІДДАЄ СПИСОК> )
    GROUP BY t.token

Тобто розійтись вони не можуть навіть теоретично: другого джерела просто немає. Це фікс
critical-баги v1 (фільтрація по денормі + лічильники по EAV → «Bosch (12)», клік → 9 товарів).

DROP-SELF
---------
Лічильники ГРУПИ рахуються із застосованими всіма фільтрами, КРІМ фільтра цієї ж групи.
Без цього, обравши «Bosch», користувач бачив би «Gorenje (0)» і не міг би розширити вибір —
мультивибір усередині фасета перестав би мати сенс. Ціна: +1 запит на кожну АКТИВНУ групу
(зазвичай 0–3), кожен — індексований GIN-скан.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

from django.db import connection
from django.db.models import Max, Min, Q, QuerySet

from catalog.models import Category, Product
from catalog.services.facets import tokens_for

__all__ = [
    "SORT_KEYS",
    "CatalogFilters",
    "breadcrumbs",
    "list_catalog",
    "product_base_qs",
    "token_counts",
]

MAX_PAGE_SIZE: Final[int] = 96
DEFAULT_PAGE_SIZE: Final[int] = 24

# ⚠️ У КОЖНОМУ сортуванні є тайбрейк по `id`. Без нього PostgreSQL не гарантує стабільного
#    порядку між рівними ключами (напр. десяток товарів по 9999 грн), і той самий товар може
#    з'явитись і на 1-й, і на 2-й сторінці, а інший — зникнути з видачі зовсім.
SORT_KEYS: Final[dict[str, tuple[str, ...]]] = {
    # Метрики популярності (переглядів/продажів) у моделі немає — «популярне» = рекомендоване
    # вручну. Це чесний дефолт, а не імітація рейтингу.
    "popular": ("-is_featured", "-id"),
    "price_asc": ("price", "id"),
    "price_desc": ("-price", "id"),
    "new": ("-created_at", "-id"),
    "name": ("name", "id"),
}
DEFAULT_SORT: Final[str] = "popular"


@dataclass(slots=True)
class CatalogFilters:
    """Розібраний запит до каталогу."""

    category: Category | None = None
    selected: dict[str, list[str]] = field(default_factory=dict)
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    sort: str = DEFAULT_SORT
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    search_qs: QuerySet[Product] | None = None  # звужений пошуком набір (див. search.py)


def category_scope(category: Category) -> Q:
    """Категорія + УСІ її підкатегорії — через матеріалізований `path` (ADR-001).

    ⚠️ `path__startswith=cat.path` БЕЗ роздільника — помилка: шлях «5609710» так зачепив би і
       «56097100» (інша категорія верхнього рівня з довшим external_id). Тому точний збіг АБО
       префікс із явним «/».

    ⚠️ ВІРТУАЛЬНА КАТЕГОРІЯ («Уцінка», virtual_tokens=['cond:2']) власних товарів через FK не має
       взагалі — її лістинг будується по токенах (DATA_MODEL §2.1). Фільтрувати її по category_id
       означало б віддати порожню сторінку.
    """
    if category.virtual_tokens:
        return Q(filter_tokens__contains=category.virtual_tokens)

    return Q(category__path=category.path) | Q(category__path__startswith=f"{category.path}/")


def product_base_qs(filters: CatalogFilters) -> QuerySet[Product]:
    """Базовий набір: активність + категорія + пошук. БЕЗ ціни і БЕЗ токенів фасетів.

    Ціна й токени накладаються окремо, бо для drop-self їх треба вміти вимикати поодинці.
    """
    qs = filters.search_qs if filters.search_qs is not None else Product.objects.all()
    qs = qs.filter(is_active=True, category__is_active=True)

    if filters.category is not None:
        qs = qs.filter(category_scope(filters.category))

    return qs


def _apply_price(qs: QuerySet[Product], filters: CatalogFilters) -> QuerySet[Product]:
    if filters.price_min is not None:
        qs = qs.filter(price__gte=filters.price_min)
    if filters.price_max is not None:
        qs = qs.filter(price__lte=filters.price_max)
    return qs


def _apply_tokens(
    qs: QuerySet[Product],
    selected: dict[str, list[str]],
    *,
    skip: str | None = None,
) -> QuerySet[Product]:
    """OR у межах групи (`&&` = overlap), AND між групами — послідовними .filter().

    `skip` вимикає одну групу — це і є drop-self.
    """
    for code, values in selected.items():
        if code == skip or not values:
            continue
        qs = qs.filter(filter_tokens__overlap=tokens_for(code, values))
    return qs


def token_counts(qs: QuerySet[Product]) -> dict[str, dict[str, int]]:
    """`unnest(filter_tokens) + GROUP BY` по ТОМУ САМОМУ QuerySet (ADR-008).

    QuerySet компілюється у підзапит `WHERE p.id IN (…)`. Саме тому лічильники фізично не можуть
    розійтися зі списком: вони рахуються з його ж SQL, а не з паралельно зібраного запиту.
    """
    inner_sql, params = qs.order_by().values("pk").query.sql_with_params()

    sql = f"""
        SELECT t.token, count(*) AS c
        FROM catalog_product p, unnest(p.filter_tokens) AS t(token)
        WHERE p.id IN ({inner_sql})
        GROUP BY t.token
    """  # noqa: S608 — inner_sql згенерований Django ORM, значення йдуть параметрами

    counts: dict[str, dict[str, int]] = {}
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        for token, count in cursor.fetchall():
            code, _, value = token.partition(":")
            if not value:
                continue
            counts.setdefault(code, {})[value] = count

    return counts


def _facet_counts(base: QuerySet[Product], filters: CatalogFilters) -> dict[str, dict[str, int]]:
    """Лічильники всіх груп з drop-self."""
    priced = _apply_price(base, filters)
    selected = filters.selected

    # 1) Усі фільтри застосовано — це коректні лічильники для НЕОБРАНИХ груп
    #    («якщо додатково візьму No Frost, скільки лишиться»).
    full = _apply_tokens(priced, selected)
    counts = token_counts(full)
    result = {code: values for code, values in counts.items() if code not in selected}

    # 2) Для кожної АКТИВНОЇ групи — окремий запит без неї самої.
    for code in selected:
        dropped = _apply_tokens(priced, selected, skip=code)
        result[code] = token_counts(dropped).get(code, {})

    return result


def _price_range(base: QuerySet[Product], filters: CatalogFilters) -> dict[str, float]:
    """Межі повзунка ціни — з фасетними фільтрами, але БЕЗ самого фільтра ціни (drop-self).

    Інакше повзунок «схлопується» до вже обраного діапазону і його неможливо розтягнути назад.
    """
    qs = _apply_tokens(base, filters.selected)
    bounds = qs.aggregate(lo=Min("price"), hi=Max("price"))
    return {
        "min": float(bounds["lo"] or 0),
        "max": float(bounds["hi"] or 0),
    }


def breadcrumbs(category: Category | None) -> list[Category]:
    """Ланцюг від кореня до поточної категорії — по `path` (без рекурсії й без N+1)."""
    if category is None:
        return []

    ext_ids = category.path.split("/")
    by_ext = {c.external_id: c for c in Category.objects.filter(external_id__in=ext_ids)}
    return [by_ext[ext] for ext in ext_ids if ext in by_ext]


def list_catalog(filters: CatalogFilters) -> dict[str, Any]:
    """Товари + фасети + діапазон цін + хлібні крихти. Один вхід для /catalog і /search."""
    base = product_base_qs(filters)

    # ── товари ────────────────────────────────────────────────────────────────
    qs = _apply_tokens(_apply_price(base, filters), filters.selected)

    # ⚠️ N+1: бренд — через select_related (FK), свотчі — через prefetch ДО РІВНЯ `items`.
    #    Ланцюг мусить збігатися з cards.product_list_qs() (там пояснено, чому саме `__items`):
    #    `swatches()` читає group.items.all(), і без цього рівня кожен товар зі свотч-групою
    #    додає власний запит.
    qs = qs.select_related("brand").prefetch_related(
        "variant_memberships__group__items",
    )

    order = SORT_KEYS.get(filters.sort, SORT_KEYS[DEFAULT_SORT])
    if filters.sort == "name":
        # `name` — перекладна колонка; сортувати треба по мовній, а не по «базовій».
        order = ("name_uk", "id")
    qs = qs.order_by(*order)

    total = qs.count()
    page_size = max(1, min(filters.page_size, MAX_PAGE_SIZE))
    pages = max(1, -(-total // page_size))  # ceil
    page = max(1, min(filters.page, pages))
    offset = (page - 1) * page_size

    items = list(qs[offset : offset + page_size])

    # ── фасети (той самий базовий набір!) ─────────────────────────────────────
    counts = _facet_counts(base, filters)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "counts": counts,
        "price_range": _price_range(base, filters),
        "breadcrumbs": breadcrumbs(filters.category),
    }
