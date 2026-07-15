"""Дерево категорій для мегаменю (ADR-001).

Віддається ЦІЛКОМ, одним масивом кореневих вузлів з вкладеними дітьми, і кешується в Redis:
59 вузлів глибиною 2, які змінюються раз на місяць, але читаються на кожній сторінці.

⚠️ ЛІЧИЛЬНИКИ РАХУЮТЬСЯ НАЖИВО, А НЕ БЕРУТЬСЯ З `Category.products_count`
------------------------------------------------------------------------
У моделі є денормалізоване поле `products_count` (DATA_MODEL §2.1), яке має наповнювати
Celery-задача `refresh_counts`. Цієї задачі ЩЕ НЕ ІСНУЄ — у базі там зараз рівно нулі на всіх
24 категоріях. Читати його означало б віддати мегаменю з «(0)» біля кожного пункту.

Тому лічильник рахується двома запитами (GROUP BY + роллап у Python) прямо при нагріванні кешу.
Це коректно за побудовою і не залежить від задачі, якої немає. Коли `refresh_counts` з'явиться —
цей модуль можна буде перевести на денорм-поле, але ПОКИ ЩО жива агрегація по 24 вузлах —
це мілісекунди раз на годину, а не проблема.

⚠️ Лічильник батька включає ТОВАРИ ВСІХ НАЩАДКІВ. «Велика побутова техніка (23)» — це сума
   холодильників, пралок і посудомийок, бо товари висять на листках, а не на корені. Без роллапу
   всі кореневі пункти мегаменю показували б «(0)» — формально «правильно», по факту абсурд.

⚠️ ПОРОЖНІ КАТЕГОРІЇ З ДЕРЕВА ВИКИДАЮТЬСЯ (рішення замовника, 14.07.2026)
------------------------------------------------------------------------
Вузол, у якого роллап-лічильник = 0 (ані своїх товарів, ані в жодного нащадка), не показується
НІДЕ: ані в мегаменю, ані плиткою на головній, ані у футері — доки замовник не покладе туди
товар. Зараз таких кореневих 4 з 9 («Генератори», «Кліматичне обладнання», «Посуд і прилади»,
«Уцінений товар») — це пункти меню, що ведуть на порожню сторінку, тобто глухий кут.

⚠️ ФІЛЬТР ЖИВЕ ТУТ, У СЕРВІСІ, А НЕ У В'ЮСІ. Одне й те саме дерево віддається і мегаменю, і
   футеру, і плиткам головної (усі троє смикають /categories/tree). Фільтр у в'юсі означав би
   «сховали в меню, лишили у футері».

⚠️ ПЛИТКА КАТЕГОРІЇ — ФОЛБЕК НА ФОТО ТОВАРУ
-------------------------------------------
`Category.image` не заповнена ЖОДНА (замовник плиток не малював), тому `image_url` завжди був
None, і головна показувала generic-іконки. Фолбек: беремо `main_image_url` НАЙДОРОЖЧОГО
активного товару з фото у піддереві категорії (тай-брейк — менший id). Дорожчий товар — це
флагман: він фотогенічніший за уцінений чайник і не міняється щодня.

Детермінованість тут не примха: плитка, яка на кожній ревалідації ISR показує інший холодильник,
виглядає як баг верстки. Один запит (DISTINCT ON) на все дерево — ніякого N+1.

⚠️ `main_image_url` — це вже `md`-версія (700px webp, `ProductImage.file_card`), див.
   sync/services.py::rebuild_product_denorm. Окремо ходити в ProductImage не треба.

⚠️ ІНВАЛІДАЦІЯ. Кеш дерева зносить сигнал по Category, але НЕ по Product (signals.py пояснює
   чому: 10 000 товарів за прогін синку = 10 000 інвалідацій). Отже і лічильники, і плитки
   самозагоюються через TTL 1 год — рівно так само, як лічильники жили тут з першого дня.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.core.cache import cache
from django.db.models import Count, Q

from catalog.models import Category, Product
from catalog.services.cache import TREE_TTL, tree_key
from catalog.services.lang import tr

__all__ = [
    "build_category_tree",
    "get_category_tree",
    "icon_url",
    "image_url",
    "resolve_category",
    "visible_children",
]

# Кандидат на плитку: (ціна, id товару, URL фото) — саме в такому порядку, бо це ще й ключ вибору.
_Tile = tuple[Decimal, int, str]


def icon_url(category: Category) -> str | None:
    """Міні-емблема мегаменю (INPUTS §4). FileField → URL або None."""
    try:
        return category.icon.url if category.icon else None
    except ValueError:
        return None


def image_url(category: Category) -> str | None:
    try:
        return category.image.url if category.image else None
    except ValueError:
        return None


def _direct_counts() -> dict[int, int]:
    """{category_id: скільки АКТИВНИХ товарів висить безпосередньо на цій категорії}."""
    rows = (
        Product.objects.filter(is_active=True)
        .values("category_id")
        .annotate(n=Count("id"))
        .values_list("category_id", "n")
    )
    return dict(rows)


def _virtual_count(tokens: list[str]) -> int:
    """Лічильник ВІРТУАЛЬНОЇ категорії («Уцінка» = virtual_tokens=['cond:2']).

    Товарів через FK у неї немає — вибірка йде по токенах (DATA_MODEL §2.1), тож і рахувати
    треба так само, інакше «Уцінка (0)» при непорожньому лістингу.
    """
    if not tokens:
        return 0
    return Product.objects.filter(is_active=True, filter_tokens__contains=tokens).count()


def _direct_tiles() -> dict[int, _Tile]:
    """{category_id: (ціна, id, фото)} найдорожчого активного товару з фото В САМІЙ категорії.

    DISTINCT ON — ОДИН запит на весь каталог замість «по товару на категорію» (N+1 на 40 вузлах
    мегаменю, який видно на кожній сторінці). PostgreSQL гарантує, що в межах кожної
    `category_id` лишиться перший рядок з `ORDER BY`, тобто найдорожчий, а при рівних цінах —
    з меншим id (стабільно між прогонами).
    """
    rows = (
        Product.objects.filter(is_active=True)
        .exclude(main_image_url="")
        .order_by("category_id", "-price", "id")
        .distinct("category_id")
        .values_list("category_id", "price", "id", "main_image_url")
    )
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


def _virtual_tile(tokens: list[str]) -> _Tile | None:
    """Плитка ВІРТУАЛЬНОЇ категорії («Уцінка»): товарів через FK у неї немає — шукаємо по токенах.

    Симетрично до `_virtual_count()`: там теж окремий запит на вузол. Віртуальних категорій у
    дереві одиниці (зараз — жодної непорожньої), тому це не N+1, а рівно стільки запитів,
    скільки таких вузлів.
    """
    row = (
        Product.objects.filter(is_active=True, filter_tokens__contains=tokens)
        .exclude(main_image_url="")
        .order_by("-price", "id")
        .values_list("price", "id", "main_image_url")
        .first()
    )
    return (row[0], row[1], row[2]) if row else None


def _best_tile(pool: list[_Tile]) -> str | None:
    """Найдорожчий товар піддерева; при рівних цінах — з меншим id (детермінізм)."""
    if not pool:
        return None
    return max(pool, key=lambda tile: (tile[0], -tile[1]))[2]


def _rollup_tiles(categories: list[Category], direct: dict[int, _Tile]) -> dict[int, str]:
    """Плитка вузла = фото товару з НЬОГО САМОГО або з будь-якого нащадка.

    Кореневі категорії власних товарів майже не мають («Велика побутова техніка» — це лише
    контейнер), тому без роллапу по нащадках плитки головної лишились би порожніми саме там,
    де вони потрібні. Той самий O(n²) по префіксах `path`, що й у `_rollup()` — десятки вузлів.
    """
    tiles: dict[int, str] = {}
    for node in categories:
        if node.virtual_tokens:
            tile = _virtual_tile(node.virtual_tokens)
            if tile is not None:
                tiles[node.pk] = tile[2]
            continue

        pool: list[_Tile] = []
        if node.pk in direct:
            pool.append(direct[node.pk])
        prefix = f"{node.path}/"
        for other in categories:
            if other.pk != node.pk and other.path.startswith(prefix) and other.pk in direct:
                pool.append(direct[other.pk])

        url = _best_tile(pool)
        if url:
            tiles[node.pk] = url
    return tiles


def _rollup(categories: list[Category], direct: dict[int, int]) -> dict[int, int]:
    """Лічильник вузла = свої товари + товари всіх нащадків (по матеріалізованому `path`).

    Дерево — десятки вузлів, тому чесний O(n²) по префіксах простіший і зрозуміліший за
    рекурсивний CTE, і виконується за мікросекунди.
    """
    total: dict[int, int] = {}
    for node in categories:
        if node.virtual_tokens:
            total[node.pk] = _virtual_count(node.virtual_tokens)
            continue
        n = direct.get(node.pk, 0)
        prefix = f"{node.path}/"
        for other in categories:
            if other.pk != node.pk and other.path.startswith(prefix):
                n += direct.get(other.pk, 0)
        total[node.pk] = n
    return total


def _serialize(
    category: Category, counts: dict[int, int], tiles: dict[int, str], lang: str
) -> dict[str, Any]:
    return {
        "id": category.pk,
        "external_id": category.external_id,
        "name": tr(category, "name", lang),
        "slug": tr(category, "slug", lang),
        "parent_id": category.parent_id,
        "depth": category.depth,
        "products_count": counts.get(category.pk, 0),
        "icon_url": icon_url(category),
        # Власна плитка ЗАВЖДИ виграє: щойно замовник заллє картинку в адмінці, фолбек на фото
        # товару мовчки поступиться їй місцем — без жодної правки коду.
        "image_url": image_url(category) or tiles.get(category.pk),
        "children": [],
    }


def build_category_tree(lang: str) -> list[dict[str, Any]]:
    """Будує дерево БЕЗ кешу (використовується і як функція нагрівання кешу).

    Порожні вузли (роллап-лічильник = 0) у результат НЕ потрапляють — див. шапку модуля.
    """
    categories = list(
        Category.objects.filter(is_active=True, is_service=False).order_by("sort_order", "id")
    )
    counts = _rollup(categories, _direct_counts())
    tiles = _rollup_tiles(categories, _direct_tiles())

    # ⚠️ Відсіюємо ДО збірки дерева. Роллап гарантує, що батько непорожнього вузла теж
    #    непорожній, тож «осиротити» дитину цим фільтром неможливо за побудовою.
    visible = [c for c in categories if counts.get(c.pk, 0) > 0]

    nodes = {c.pk: _serialize(c, counts, tiles, lang) for c in visible}
    roots: list[dict[str, Any]] = []

    for category in visible:
        node = nodes[category.pk]
        parent = nodes.get(category.parent_id) if category.parent_id else None
        # Батько може бути неактивним/службовим → його немає в `nodes`. Тоді вузол спливає в
        # корінь, а не зникає з меню разом з усіма товарами.
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)

    return roots


def get_category_tree(lang: str, *, use_cache: bool = True) -> list[dict[str, Any]]:
    """Дерево категорій з Redis (TTL 1 год + інвалідація сигналом)."""
    if not use_cache:
        return build_category_tree(lang)

    key = tree_key(lang)
    cached = cache.get(key)
    if cached is not None:
        return cached

    tree = build_category_tree(lang)
    cache.set(key, tree, TREE_TTL)
    return tree


def _find(nodes: list[dict[str, Any]], category_id: int) -> dict[str, Any] | None:
    for node in nodes:
        if node["id"] == category_id:
            return node
        found = _find(node["children"], category_id)
        if found is not None:
            return found
    return None


def visible_children(lang: str, category: Category) -> list[dict[str, Any]]:
    """Непорожні підкатегорії — «чипси» під заголовком лістингу.

    ⚠️ Беремо їх з ТОГО САМОГО (кешованого) дерева, що й мегаменю, а не окремим запитом. Дві
       причини, і жодна не про швидкість:
         1. правило «порожня категорія не показується НІДЕ» мусить діяти і в каталозі — інакше
            прибрана з меню «Вбудовані пральні машини» лишалась би чипсом на сторінці батька;
         2. чипс отримує плитку і лічильник задарма — рівно ті, що на головній.

    Категорії, якої в дереві немає (порожня, відкрита прямим URL), дітей теж не показуємо —
    їх у неї за визначенням немає жодної непорожньої.
    """
    node = _find(get_category_tree(lang), category.pk)
    return node["children"] if node is not None else []


def resolve_category(ref: str) -> Category | None:
    """Знаходить категорію за `external_id` АБО за slug (uk/ru).

    ⚠️ Приймає обидва навмисно. Технічне завдання просить `/catalog/{category_slug}`, а фронт
    (frontend/src/lib/api/http.ts) шле `?category={external_id}` — бо канонічний URL за ADR-002 —
    `/{locale}/c/{ext_id}/{slug}`, де ID канонічний, а slug декоративний. Приймати лише щось одне
    означало б зламати одного з двох споживачів.

    Порядок перевірки — external_id ПЕРШИМ: він unique і не перекладний, тобто однозначний.
    """
    if not ref:
        return None

    qs = Category.objects.filter(is_active=True)

    category = qs.filter(external_id=ref).first()
    if category is not None:
        return category

    return qs.filter(Q(slug_uk=ref) | Q(slug_ru=ref)).first()
