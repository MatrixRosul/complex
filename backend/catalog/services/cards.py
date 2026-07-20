"""Картка товару, bulk (кошик/бажання), порівняння, супутні товари.

⚠️ ХАРАКТЕРИСТИКИ — ЗГРУПОВАНІ, ОДИНИЦЯ КЛЕЇТЬСЯ ДО ЗНАЧЕННЯ (INPUTS §2)
------------------------------------------------------------------------
Жорстка вимога замовника, яку легко порушити непомітно:

    ліва колонка  = n                  → «Висота»
    права колонка = f"{v} {u}".strip()  → «284 мм»          читається «Висота: 284 мм»

    НЕ «Висота (мм): 284». Одиниця НІКОЛИ не приклеюється до назви.

API віддає `n`, `v`, `u` окремими полями (як вони лежать у specs_json) + готове `display`
з уже склеєним значенням. Фронт бере `display` і не конкатенує нічого сам —
`formatSpecValue()` у lib/format.ts існує рівно для цього.

Рядки згруповані по `g` (AttributeGroup: «Основні», «Габаритні розміри») з порядком `gs`.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from django.db.models import Prefetch, QuerySet

from catalog.models import Product, RelatedGroupItem
from catalog.services.lang import tr

__all__ = [
    "brand_dict",
    "bulk_products",
    "compare_products",
    "grouped_specs",
    "list_item",
    "product_detail_qs",
    "product_list_qs",
    "related_products",
    "swatches",
]


# ---------------------------------------------------------------------------
# QuerySet-и без N+1
# ---------------------------------------------------------------------------
def product_list_qs() -> QuerySet[Product]:
    """Для сітки: бренд + свотчі кольорів (DESIGN_SYSTEM §1).

    ⚠️ Ланцюг `…__group__items` — ОБОВ'ЯЗКОВИЙ, а не «про запас». `swatches()` читає
       `membership.group.items.all()`; без цього рівня prefetch кожен товар зі свотч-групою
       додає СВІЙ запит, і сітка починає ходити в БД по колу. Виміряно на цій же базі
       (категорія «Чайники електричні»): 1 товар → 11 запитів, 3 → 12, 4 → 13. Класичний N+1,
       який ховається за тим, що товари БЕЗ свотчів його не тригерять (widget != swatches →
       ранній `continue`), тому на телевізорах усе виглядало «пласко».
       Тут `items` вистачає без `__product`: свотчу потрібен лише `item.product_id` (FK-колонка,
       вона вже в рядку) і `swatch_hex`.
    """
    return Product.objects.select_related("brand").prefetch_related(
        "variant_memberships__group__items",
    )


def product_detail_qs() -> QuerySet[Product]:
    """Для картки: усе, що рендериться, — одним набором prefetch.

    Без цього картка робить ~10 зайвих запитів: бренд, країна, категорія, фото, варіанти,
    супутні. `images` сортуємо тут же, щоб головне фото було першим у галереї.

    ⚠️ `…__group__items__product` — теж обов'язковий: `variant_groups()` для КОЖНОГО варіанта
       читає `item.product.slug / is_active / availability` (перемикач показує недоступні
       моделі перекресленими, а не ховає їх). Без `__product` перемикач на 5 діагоналей = 5
       зайвих запитів на одну картку.
    """
    return Product.objects.select_related("brand", "country", "category").prefetch_related(
        Prefetch("images", queryset=_gallery_qs()),
        "variant_memberships__group__axis_attribute",
        "variant_memberships__group__items__product",
        "related_memberships__group",
    )


def _gallery_qs():
    from catalog.models import ProductImage

    return ProductImage.objects.order_by("-is_main", "position", "id")


# ---------------------------------------------------------------------------
# Характеристики
# ---------------------------------------------------------------------------
def spec_display(row: dict[str, Any]) -> str:
    """`{v: "284", u: "мм"}` → «284 мм». Порожня одиниця → просто значення."""
    value = str(row.get("v") or "").strip()
    unit = str(row.get("u") or "").strip()
    return f"{value} {unit}".strip() if unit else value


def grouped_specs(product: Product, lang: str) -> list[dict[str, Any]]:
    """specs_json → [{group, group_sort, rows: [{code, name, value, unit, display, vn}]}].

    Групування робить БЕКЕНД, а не фронт: порядок груп (`gs`) і порядок усередині групи (`s`)
    — це дані з адмінки (AttributeGroup.sort_order / Attribute.sort_order), і фронт не має
    права їх вигадувати.
    """
    specs = tr(product, "specs_json", lang) or []
    if not isinstance(specs, list):
        return []

    buckets: dict[str, dict[str, Any]] = {}

    for index, row in enumerate(specs):
        if not isinstance(row, dict):
            continue
        group_name = str(row.get("g") or "")
        bucket = buckets.setdefault(
            group_name,
            {"group": group_name, "group_sort": row.get("gs") or 0, "rows": []},
        )
        bucket["rows"].append(
            {
                "code": row.get("code") or "",
                "name": row.get("n") or "",
                "value": str(row.get("v") or ""),
                "unit": str(row.get("u") or ""),
                # ⚠️ ГОТОВИЙ рядок для правої колонки — «284 мм». Фронт НЕ склеює n + u.
                "display": spec_display(row),
                "vn": row.get("vn"),
                "sort": row.get("s") or 0,
                # ⚡ позиція в specs_json — тайбрейк замість назви (див. нижче).
                "_pos": index,
            }
        )

    groups = sorted(buckets.values(), key=lambda g: (g["group_sort"], g["group"]))
    for group in groups:
        # ⚡ ТАЙБРЕЙК — ПОЗИЦІЯ, А НЕ НАЗВА. Раніше тут стояло (sort, name): у 95% характеристик
        #   sort_order лежить на дефолті 100, тож ключ вироджувався в (0, name) і група
        #   перевпорядковувалась ЗА АЛФАВІТОМ — «Висота для монтажу» пролізала поперед
        #   «Управління», хоча в адмінці порядок правильний. specs_json уже складений
        #   у потрібному порядку (sync.services.rebuild_denorm сортує по
        #   group.sort_order → attribute.sort_order → attribute_id) — його треба зберегти,
        #   а не вигадати новий (див. докстрінг).
        group["rows"].sort(key=lambda r: (r["sort"], r["_pos"]))
        for row in group["rows"]:
            del row["_pos"]
    return groups


# ---------------------------------------------------------------------------
# Варіанти / супутні
# ---------------------------------------------------------------------------
def variant_groups(product: Product, lang: str) -> list[dict[str, Any]]:
    """Перемикачі варіантів: кнопки (діагональ) і кружечки (колір) — INPUTS §4.

    ⚠️ Неактивні/відсутні варіанти НЕ ховаємо: фронт показує їх перекресленими
    (types.ts::VariantItemOut — «Товар існує, але зараз недоступний»). Приховати варіант —
    означає збрехати про існування моделі на 65".
    """
    groups: list[dict[str, Any]] = []

    for membership in product.variant_memberships.all():
        group = membership.group
        if not group.is_active:
            continue

        axis = group.axis_attribute
        items: list[dict[str, Any]] = []

        for item in sorted(group.items.all(), key=lambda i: (i.sort_order, i.id)):
            items.append(
                {
                    "product_id": item.product_id,
                    "label": tr(item, "label", lang),
                    "swatch_hex": item.swatch_hex or None,
                    "slug": tr(item.product, "slug", lang),
                    "is_active": item.product.is_active,
                    "availability": item.product.availability,
                    "is_current": item.product_id == product.pk,
                }
            )

        groups.append(
            {
                "id": group.id,
                "axis_label": tr(axis, "name", lang) if axis else tr(group, "name", lang),
                "widget": group.widget,
                "items": items,
            }
        )

    return groups


def related_products(product: Product, lang: str) -> list[Product]:
    """Супутні — ВЗАЄМНІ, через RelatedGroup (DATA_MODEL §2.7).

    Взаємність — властивість структури, а не робота коду: контент-менеджер збирає ОДНУ групу з
    трьох товарів, і всі троє автоматично бачать двох інших. Асиметрія («А бачить Б, Б не бачить
    А») тут неможлива в принципі — саме тому це група + membership, а не пара FK.
    """
    group_ids = [m.group_id for m in product.related_memberships.all() if m.group.is_active]
    if not group_ids:
        return []

    items = (
        RelatedGroupItem.objects.filter(group_id__in=group_ids, product__is_active=True)
        .exclude(product_id=product.pk)
        .select_related("product__brand")
        .order_by("group__sort_order", "sort_order", "id")
    )

    seen: set[int] = set()
    result: list[Product] = []
    for item in items:
        if item.product_id in seen:
            continue
        seen.add(item.product_id)
        result.append(item.product)
    return result


def brand_dict(product: Product) -> dict[str, Any] | None:
    """Бренд у форматі BrandOut. `logo.url` кидає ValueError, коли файлу немає — ловимо."""
    if not product.brand_id:
        return None
    brand = product.brand
    try:
        logo = brand.logo.url if brand.logo else None
    except ValueError:
        logo = None
    return {"id": brand.pk, "name": brand.name, "slug": brand.slug, "logo_url": logo}


def list_item(product: Product, lang: str) -> dict[str, Any]:
    """Товар → ProductListItemOut (картка в сітці / каруселі).

    ⚠️ ЖИВЕ В СЕРВІСІ, а не у в'юсі, бо серіалізувати картку треба не лише HTTP-шару: добірки
    для головної (`services/collections.py`) кешуються В УЖЕ СЕРІАЛІЗОВАНОМУ вигляді — інакше
    в Redis лягли б Product-моделі, і кожне читання кешу заново тягнуло б бренд і свотчі з БД
    (тобто кеш не економив би нічого, крім одного SELECT по товарах).

    ⚠️ Розраховує на prefetch з `product_list_qs()`. Викликати на «голому» Product.objects.get()
    можна, але кожна картка тоді = +2 запити (бренд, свотчі).
    """
    return {
        "id": product.pk,
        "sku": product.sku,
        "name": tr(product, "name", lang),
        "slug": tr(product, "slug", lang),
        "price": product.price,
        "old_price": product.old_price,
        "availability": product.availability,
        "order_lead_days": product.order_lead_days,
        "condition": product.condition,
        "is_featured": product.is_featured,
        "installment_available": product.installment_available,
        "installment_max_payments": product.installment_max_payments,
        "main_image_url": product.main_image_url or None,
        "brand": brand_dict(product),
        "swatches": swatches(product),
    }


def swatches(product: Product) -> list[dict[str, Any]]:
    """Кружечки кольорів прямо в сітці видачі (types.ts::ProductListItem.swatches)."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for membership in product.variant_memberships.all():
        if membership.group.widget != "swatches":
            continue
        for item in membership.group.items.all():
            hex_value = (item.swatch_hex or "").strip()
            if not hex_value or hex_value in seen:
                continue
            seen.add(hex_value)
            result.append({"hex": hex_value, "product_id": item.product_id})

    return result


# ---------------------------------------------------------------------------
# Bulk — кошик / порівняння / бажання (ADR-017)
# ---------------------------------------------------------------------------
def bulk_products(items: Iterable[tuple[int, int]], lang: str) -> dict[str, Any]:
    """[{id, qty}] → актуальні ціни, наявність, назви, фото + підсумки.

    ⚠️ ЦЕ І Є ЗАХИСТ ВІД ПРОТУХЛИХ ЦІН. У localStorage фронт тримає ТІЛЬКИ {id, qty} — ціни й
       наявність приходять звідси, з БД. Синк міняє ціни 4×/добу; без цього ендпоінта кошик
       показував би ціну, яку користувач бачив тиждень тому, а checkout виставив би іншу.

    ⚠️ `installment_allowed` — AND по ВСІХ позиціях і рахується САМЕ ТУТ, на сервері (ADR-017).
       Якби це рахував фронт з localStorage, користувач обирав би «оплату частинами» на
       протухлих даних і отримував помилку вже на checkout.

    Толерантний до неіснуючих id: вони не кидають 404, а повертаються в `unavailable_items`
    (це compare/wishlist — товар могли зняти з продажу, поки він лежав у localStorage).
    """
    wanted: dict[int, int] = {}
    for product_id, qty in items:
        # Дублі id у кошику складаємо, а не перезаписуємо.
        wanted[product_id] = wanted.get(product_id, 0) + max(1, qty)

    if not wanted:
        return {
            "items": [],
            "subtotal": Decimal("0"),
            "installment_allowed": False,
            "changed_items": [],
            "unavailable_items": [],
        }

    found = {p.pk: p for p in product_list_qs().filter(pk__in=wanted.keys())}

    rows: list[dict[str, Any]] = []
    subtotal = Decimal("0")
    unavailable: list[int] = []

    for product_id, qty in wanted.items():
        product = found.get(product_id)

        # Немає в БД / деактивований / немає в наявності → фронт прибирає з кошика.
        if product is None or not product.is_active:
            unavailable.append(product_id)
            continue

        line_total = product.price * qty
        subtotal += line_total

        rows.append(
            {
                "id": product.pk,
                "qty": qty,
                "sku": product.sku,
                "name": tr(product, "name", lang),
                "slug": tr(product, "slug", lang),
                "price": product.price,
                "old_price": product.old_price,
                "line_total": line_total,
                "availability": product.availability,
                "installment_available": product.installment_available,
                "installment_max_payments": product.installment_max_payments,
                "is_active": product.is_active,
                "main_image_url": product.main_image_url or None,
                # ⚠️ Ці п'ять полів — не «про запас». /wishlist рендерить ТУ САМУ картку
                #    (ProductCard), що й каталог, і бере дані звідси. Без них у бажаннях
                #    зникали б свотчі кольорів, бейдж «Уцінений» і строк «Під замовлення N днів» —
                #    той самий товар виглядав би по-різному в каталозі й у бажаннях.
                #    prefetch уже є в product_list_qs() — жодного зайвого запиту.
                "brand": brand_dict(product),
                "swatches": swatches(product),
                "condition": product.condition,
                "order_lead_days": product.order_lead_days,
                "is_featured": product.is_featured,
            }
        )

    # ⚠️ AND по всіх позиціях. Порожній кошик → False (а не «vacuous truth» від all([])):
    #    показувати бейдж «оплата частинами» на порожньому кошику — безглуздо.
    installment_allowed = bool(rows) and all(r["installment_available"] for r in rows)

    return {
        "items": rows,
        "subtotal": subtotal,
        "installment_allowed": installment_allowed,
        "changed_items": [],
        "unavailable_items": unavailable,
    }


# ---------------------------------------------------------------------------
# Порівняння
# ---------------------------------------------------------------------------
def compare_products(ids: list[int], lang: str) -> dict[str, Any]:
    """Таблиця порівняння: СПІЛЬНІ характеристики + позначка, де значення відрізняються.

    «Спільна» = присутня хоча б в одного товару (рядок з прочерком там, де характеристики
    немає — це теж інформація: «у Bosch є No Frost, у Gorenje — немає»). Показувати лише
    характеристики, які є У ВСІХ, — означало б викинути з таблиці саме ті рядки, заради яких
    порівняння й відкривають.

    `is_different` — прапорець «тут товари розходяться». Фронт по ньому робить фільтр
    «показати лише відмінності» (класика порівнялок) і підсвічує рядок.
    """
    products = list(product_detail_qs().filter(pk__in=ids, is_active=True))
    # Порядок — як у запиті (?ids=3,1,2 → колонки 3,1,2), а не як у БД.
    order = {pid: i for i, pid in enumerate(ids)}
    products.sort(key=lambda p: order.get(p.pk, 10**9))

    if not products:
        return {"products": [], "groups": []}

    # code → метадані рядка (беремо з першого товару, де він трапився)
    rows: dict[str, dict[str, Any]] = {}
    # code → {product_id: display}
    values: dict[str, dict[int, str]] = {}

    for product in products:
        for group in grouped_specs(product, lang):
            for row in group["rows"]:
                code = row["code"]
                if not code:
                    continue
                if code not in rows:
                    rows[code] = {
                        "code": code,
                        "name": row["name"],
                        "unit": row["unit"],
                        "group": group["group"],
                        "group_sort": group["group_sort"],
                        "sort": row["sort"],
                        # ⚡ як і в grouped_specs — тайбрейк позицією, не назвою.
                        "pos": len(rows),
                    }
                values.setdefault(code, {})[product.pk] = row["display"]

    buckets: dict[str, dict[str, Any]] = {}

    for code, meta in rows.items():
        per_product = values.get(code, {})
        cells = [per_product.get(p.pk, "") for p in products]

        # Відмінність рахуємо по ВСІХ колонках, включно з порожніми: якщо в одного товару
        # характеристики немає, а в іншого є — це саме відмінність, і її треба підсвітити.
        is_different = len(set(cells)) > 1

        bucket = buckets.setdefault(
            meta["group"],
            {"group": meta["group"], "group_sort": meta["group_sort"], "rows": []},
        )
        bucket["rows"].append(
            {
                "code": code,
                "name": meta["name"],
                "unit": meta["unit"],
                "values": cells,
                "is_different": is_different,
                "sort": meta["sort"],
                "_pos": meta["pos"],
            }
        )

    groups = sorted(buckets.values(), key=lambda g: (g["group_sort"], g["group"]))
    for group in groups:
        group["rows"].sort(key=lambda r: (r["sort"], r["_pos"]))
        for row in group["rows"]:
            del row["_pos"]

    return {"products": products, "groups": groups}
