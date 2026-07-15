"""Добірки товарів для головної сторінки: хіти, акції, новинки, уцінка.

⚠️ ЖОДНОЇ МАРКЕТИНГОВОЇ ВИГАДКИ. Кожна добірка — це ПРЯМИЙ запит по полю, яким замовник керує
   сам з адмінки. Блок існує рівно тоді, коли в БД є товари, що під нього підпадають; порожня
   добірка повертає `[]`, і фронт її просто не рендерить. Ніяких «знижок до 15%», яких немає
   в даних (саме через це знесено демо-банери — див. `core/management/commands/seed_demo.py`).

   featured  → Product.is_featured        — галочка «Рекомендований» в адмінці
   sale      → Product.old_price IS NOT NULL — стара ціна є ⇒ знижка справжня
   new       → Product.created_at          — коли товар з'явився в БД
   clearance → Product.condition != NEW    — уцінка/відновлений/б/в

⚠️ ЧОМУ `sale`, А НЕ `discounted`. У цьому ж API вже є GET /products/discounted, і там
   «discounted» означає «є old_price» (знижка). У ТЗ на добірки тим самим словом названо УЦІНКУ
   (condition != NEW) — а це зовсім інша сутність: уцінений холодильник з подряпиною і
   холодильник за акційною ціною лежать у різних блоках. Тримати в одному API два різні значення
   слова «discounted» — це закладена міна. Тому тут: `sale` = знижка (old_price),
   `clearance` = уцінка (condition). Легасі-роут /products/discounted лишається як був.

⚠️ ЗНИЖКА НЕ МОЖЕ БУТИ ФЕЙКОВОЮ НА РІВНІ БД: CheckConstraint `prod_old_price_gt_price` гарантує
   old_price > price. Тобто якщо old_price заповнена — відсоток знижки завжди додатний, і фронт
   рахує його чесно. `pricing.recalc()` гасить old_price у None, якщо після зміни курсу вона
   перестала бути більшою за price.

`by_category` — КАРУСЕЛІ ПО КАТЕГОРІЯХ (додано 14.07.2026)
----------------------------------------------------------
Три «кураторські» добірки вище зараз ПОРОЖНІ: у базі 0 товарів з `is_featured` і 0 з `old_price`.
Тобто головна, зібрана лише з них, — це заголовок «Хіти продажів» і нічого під ним. Щоб головна
жила ДО того, як замовник розставить галочки, показуємо те, що в базі точно є: товари з трьох
найбільших НЕПОРОЖНІХ кореневих категорій.

Це не маркетингова вигадка (вона в цьому модулі заборонена), а чесна навігація: карусель —
це РІВНО перша сторінка тієї ж категорії в тому ж дефолтному сортуванні (`listing.DEFAULT_SORT`).
Клік «Дивитись усі» веде у каталог і показує ті самі товари першими — користувач не бачить
підміни.

«Найбільші» і «непорожні» рахує НЕ цей модуль: список кореневих категорій береться з
`services/tree.py` (там уже є роллап-лічильники по нащадках і фільтр порожніх). Друга реалізація
того самого означала б, що колись вони розійдуться — і на плитці буде «(164)», а в каруселі під
нею товари іншої категорії.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from django.core.cache import cache
from django.db.models import QuerySet

from catalog.enums import Condition
from catalog.models import Category, Product
from catalog.services import cards, listing, tree
from catalog.services.cache import COLLECTIONS_TTL, collections_key
from catalog.services.lang import tr

log = logging.getLogger(__name__)

__all__ = [
    "BY_CATEGORY_COUNT",
    "BY_CATEGORY_PRODUCTS",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "NAMES",
    "get_collections",
]

DEFAULT_LIMIT: Final[int] = 12
# Стеля вибірки. Кеш завжди тримає рівно стільки, а `?limit=` ріже вже готовий список у Python —
# тому `limit` НЕ входить у ключ кешу (див. cache.collections_key).
MAX_LIMIT: Final[int] = 24

NAMES: Final[tuple[str, ...]] = ("featured", "sale", "new", "clearance")

# Скільки каруселей по категоріях і скільки товарів у кожній.
# 3 × 8: більше — це вже не головна, а каталог, згорнутий у кілька екранів скролу.
BY_CATEGORY_COUNT: Final[int] = 3
BY_CATEGORY_PRODUCTS: Final[int] = 8


def _base_qs() -> QuerySet[Product]:
    """Спільна база всіх добірок: активний товар, активна категорія, Є ФОТО.

    ⚠️ `.exclude(main_image_url="")` — не косметика. Картка в сітці бере фото САМЕ з
       `main_image_url` (див. cards.list_item), а не з таблиці ProductImage. Зараз у базі
       314 активних товарів, і в 20 з них `main_image_url` порожній — без цього фільтра
       вони б лягли в карусель сірими дірками.

    ⚠️ `product_list_qs()` дає select_related(brand) + prefetch свотчів. Без нього кожна
       картка в каруселі — це +2 запити (класичний N+1 на 4 блоки × 12 товарів = ~100 запитів).
    """
    return (
        cards.product_list_qs()
        .filter(is_active=True, category__is_active=True)
        .exclude(main_image_url="")
    )


def _top_root_categories(lang: str) -> list[Category]:
    """Топ-N НЕПОРОЖНІХ кореневих категорій за кількістю товарів (з нащадками).

    Лічильники й фільтр порожніх беремо з `tree` — того самого дерева, яке рендерить мегаменю
    і плитки. Дерево кешоване (TTL 1 год), тож у 99% викликів це нуль запитів у БД.

    Тай-брейк по `id`: дві категорії з однаковою кількістю товарів не мають міняти порядок
    між ревалідаціями ISR.
    """
    roots = tree.get_category_tree(lang)
    top = sorted(roots, key=lambda r: (-r["products_count"], r["id"]))[:BY_CATEGORY_COUNT]
    if not top:
        return []

    order = {node["id"]: i for i, node in enumerate(top)}
    categories = Category.objects.filter(pk__in=order)
    return sorted(categories, key=lambda c: order[c.pk])


def _by_category(lang: str) -> list[dict[str, Any]]:
    """[{id, name, slug, products[≤8]}] — карусель на кожну з топ-категорій.

    ⚠️ `listing.category_scope()` — той самий скоуп, що й у каталозі: категорія РАЗОМ з
       підкатегоріями (по матеріалізованому `path`). Кореневі категорії власних товарів майже
       не мають («Вбудована техніка» — контейнер), тому фільтр по `category_id` дав би порожню
       карусель під плиткою з написом «164».

    ⚠️ Сортування — `listing.DEFAULT_SORT`, тобто рівно те, що користувач побачить, клікнувши
       «Дивитись усі». Карусель, у якій товари інші, ніж на першій сторінці категорії, читається
       як помилка.
    """
    blocks: list[dict[str, Any]] = []

    for category in _top_root_categories(lang):
        products = (
            _base_qs()
            .filter(listing.category_scope(category))
            .order_by(*listing.SORT_KEYS[listing.DEFAULT_SORT])[:BY_CATEGORY_PRODUCTS]
        )
        items = [cards.list_item(p, lang) for p in products]
        if not items:
            continue  # страховка: дерево вважає категорію непорожньою, але всі її товари без фото
        blocks.append(
            {
                "id": category.pk,
                "name": tr(category, "name", lang),
                "slug": tr(category, "slug", lang),
                "external_id": category.external_id,
                "products": items,
            }
        )

    return blocks


def _build(lang: str) -> dict[str, Any]:
    """Читає добірки з БД і серіалізує їх. Викликається лише при промаху кешу."""
    querysets: dict[str, QuerySet[Product]] = {
        # Куратор — замовник: галочка в адмінці. Найсвіжіші позначені — першими.
        "featured": _base_qs().filter(is_featured=True).order_by("-id"),
        # Знижка = є стара ціна. CheckConstraint гарантує old_price > price.
        "sale": _base_qs().filter(old_price__isnull=False).order_by("-id"),
        # Новинки — за датою появи в БД. Тай-брейк по -id: у синку сотні товарів отримують
        # ОДИН created_at (bulk-імпорт), і без -id порядок між ними був би недетермінований,
        # тобто головна «перетасовувалась» би на кожну ревалідацію ISR.
        "new": _base_qs().order_by("-created_at", "-id"),
        # Уцінка = будь-який стан, крім «Новий» (відновлений / уцінений / б/в).
        "clearance": _base_qs().exclude(condition=Condition.NEW).order_by("-id"),
    }
    data: dict[str, Any] = {
        name: [cards.list_item(p, lang) for p in qs[:MAX_LIMIT]] for name, qs in querysets.items()
    }
    data["by_category"] = _by_category(lang)
    return data


def get_collections(lang: str, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """{featured, sale, new, clearance, by_category} — усі блоки головної одним викликом.

    Кеш Redis, TTL 5 хв (COLLECTIONS_TTL), ключ — по мові. Промах кешу = 4 запити з prefetch
    + по одному на карусель категорії; попадання = 0 запитів у БД (у Redis лежать уже готові
    dict'и, а не Product-моделі).

    Недоступний Redis НЕ валить головну: читаємо з БД і йдемо далі.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    key = collections_key(lang)

    try:
        cached = cache.get(key)
    except Exception:
        # Redis лежить — це не привід віддавати 500 на ГОЛОВНІЙ. Читаємо з БД і йдемо далі.
        log.warning("Не вдалося прочитати кеш добірок", exc_info=True)
        cached = None

    if cached is None:
        cached = _build(lang)
        try:
            cache.set(key, cached, COLLECTIONS_TTL)
        except Exception:
            log.warning("Не вдалося записати кеш добірок", exc_info=True)

    # ⚠️ `by_category` — не плаский список карток, а список БЛОКІВ, тому зріз `limit` іде
    #    всередину кожного блоку. Спільний `[:limit]` по всьому dict'у обрізав би САМІ категорії
    #    (limit=2 → зникла б третя карусель) — а це вже інший контракт, ніж «по limit карток».
    result: dict[str, Any] = {name: cached[name][:limit] for name in NAMES}
    result["by_category"] = [
        {**block, "products": block["products"][:limit]} for block in cached["by_category"]
    ]
    return result
