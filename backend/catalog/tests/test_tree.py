"""Дерево категорій: порожні вузли не показуються, плитка бере фото товару.

Передісторія (14.07.2026). Головна показувала плитки категорій з generic-іконками і лічильником
«0» під кожною — бо `Category.image` не заповнена ЖОДНА, а 4 з 9 кореневих категорій («Генератори»,
«Кліматичне обладнання», «Посуд і прилади», «Уцінений товар») не мають жодного товару. Тобто
третина головного меню вела на порожню сторінку.

Рішення замовника: порожня категорія не показується НІДЕ (головна, мегаменю, каталог, футер),
доки в неї не покладуть товар; плитка непорожньої категорії показує фото реального товару з неї.

Тому тут перевіряється саме те, що найлегше зламати назад:
  1. вузол з нулем товарів (з урахуванням нащадків!) у дерево не потрапляє;
  2. непорожній вузол не зникає разом з порожніми сусідами;
  3. `image_url` фолбечиться на фото товару — детерміновано, а не «якесь із трьох»;
  4. власна плитка категорії (коли замовник її заллє) фолбек перебиває;
  5. усе це — БЕЗ N+1: дерево віддається на кожній сторінці сайту.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from catalog.enums import Availability
from catalog.models import Category, Product
from catalog.services.cache import invalidate_catalog_cache
from catalog.services.tree import build_category_tree, get_category_tree, visible_children

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    """Дерево кешується на годину — між тестами кеш мусить бути порожній."""
    invalidate_catalog_cache()
    yield
    invalidate_catalog_cache()


def _category(external_id: str, name: str, parent: Category | None = None) -> Category:
    category = Category(
        external_id=external_id,
        name=name,
        slug=f"cat-{external_id}",
        parent=parent,
    )
    category.name_uk, category.slug_uk = name, category.slug
    category.save()
    return category


def _product(
    category: Category,
    sku: str,
    *,
    price: str = "10000",
    image: str = "https://cdn.example.com/p.webp",
    is_active: bool = True,
) -> Product:
    product = Product(
        sku=sku,
        name=f"Товар {sku}",
        slug=f"tovar-{sku}",
        category=category,
        base_price=Decimal(price),
        price=Decimal(price),
        main_image_url=image,
        is_active=is_active,
        availability=Availability.IN_STOCK,
    )
    product.name_uk, product.slug_uk = product.name, product.slug
    product.save()
    return product


def _flat(nodes: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Дерево → {id: вузол}, щоб не писати обходи в кожному тесті."""
    flat: dict[int, dict[str, Any]] = {}
    for node in nodes:
        flat[node["id"]] = node
        flat.update(_flat(node["children"]))
    return flat


# ---------------------------------------------------------------------------
# 1. Порожні категорії — головна вимога
# ---------------------------------------------------------------------------
def test_empty_category_is_hidden() -> None:
    """Категорія без товарів не потрапляє в дерево взагалі — ні коренем, ні дитиною."""
    full = _category("100", "Велика техніка")
    empty = _category("200", "Генератори")
    _product(full, "A1")

    ids = _flat(build_category_tree("uk"))

    assert full.pk in ids
    assert empty.pk not in ids


def test_parent_survives_when_only_child_has_products() -> None:
    """Товари висять на листках. Корінь-контейнер («Велика техніка») своїх товарів не має —
    і все одно мусить лишитись у дереві, бо в нього непорожня дитина.

    Найгірший можливий баг цього фільтра: викинути корінь разом з усім каталогом під ним.
    """
    root = _category("100", "Велика техніка")
    child = _category("101", "Холодильники", parent=root)
    _product(child, "A1")

    tree = build_category_tree("uk")

    assert [n["id"] for n in tree] == [root.pk]
    assert [n["id"] for n in tree[0]["children"]] == [child.pk]
    assert tree[0]["products_count"] == 1  # роллап: свої 0 + 1 у дитини


def test_empty_child_is_dropped_but_siblings_stay() -> None:
    """Порожню підкатегорію ховаємо, непорожню сусідку — ні."""
    root = _category("100", "Велика техніка")
    alive = _category("101", "Холодильники", parent=root)
    dead = _category("102", "Пральні машини", parent=root)
    _product(alive, "A1")

    tree = build_category_tree("uk")
    children = [n["id"] for n in tree[0]["children"]]

    assert children == [alive.pk]
    assert dead.pk not in _flat(tree)


def test_inactive_product_does_not_revive_category() -> None:
    """Прихований товар — це не товар. Категорія з одним is_active=False лишається порожньою."""
    category = _category("100", "Генератори")
    _product(category, "HIDDEN", is_active=False)

    assert build_category_tree("uk") == []


# ---------------------------------------------------------------------------
# 2. Плитка: фолбек на фото товару
# ---------------------------------------------------------------------------
def test_image_url_falls_back_to_product_photo() -> None:
    """`Category.image` порожня → плитку малює фото товару з цієї ж категорії."""
    category = _category("100", "Холодильники")
    _product(category, "A1", image="https://cdn.example.com/fridge.webp")

    node = _flat(build_category_tree("uk"))[category.pk]

    assert node["image_url"] == "https://cdn.example.com/fridge.webp"


def test_tile_takes_the_most_expensive_product() -> None:
    """Вибір товару для плитки ДЕТЕРМІНОВАНИЙ: найдорожчий (флагман), а не «якийсь».

    ⚠️ Це не естетика. Плитка, яка на кожній ревалідації ISR показує інший холодильник,
    виглядає як баг верстки — і саме так поводиться будь-який запит без ORDER BY.
    """
    category = _category("100", "Холодильники")
    _product(category, "CHEAP", price="5000", image="https://cdn.example.com/cheap.webp")
    _product(category, "FLAGSHIP", price="90000", image="https://cdn.example.com/flagship.webp")
    _product(category, "MID", price="30000", image="https://cdn.example.com/mid.webp")

    node = _flat(build_category_tree("uk"))[category.pk]

    assert node["image_url"] == "https://cdn.example.com/flagship.webp"


def test_root_tile_comes_from_descendant() -> None:
    """У кореневої категорії власних товарів немає — плитка бере фото з піддерева.

    Без роллапу по нащадках плитки головної лишились би порожніми саме там, де вони потрібні:
    на головній стоять КОРЕНЕВІ категорії.
    """
    root = _category("100", "Велика техніка")
    child = _category("101", "Холодильники", parent=root)
    _product(child, "A1", price="42000", image="https://cdn.example.com/deep.webp")

    node = _flat(build_category_tree("uk"))[root.pk]

    assert node["image_url"] == "https://cdn.example.com/deep.webp"


def test_product_without_photo_is_not_a_tile() -> None:
    """Товар без фото не стає плиткою (інакше в сітці — сіра діра), але категорію не ховає:
    товар у ній Є, просто без картинки."""
    category = _category("100", "Холодильники")
    _product(category, "NO_PHOTO", price="99000", image="")
    _product(category, "WITH_PHOTO", price="1000", image="https://cdn.example.com/ok.webp")

    node = _flat(build_category_tree("uk"))[category.pk]

    assert node["products_count"] == 2
    assert node["image_url"] == "https://cdn.example.com/ok.webp"


def test_own_image_wins_over_fallback() -> None:
    """Щойно замовник заллє плитку в адмінці — фолбек мовчки поступається їй місцем."""
    category = _category("100", "Холодильники")
    _product(category, "A1", image="https://cdn.example.com/product.webp")
    Category.objects.filter(pk=category.pk).update(image="categories/tiles/fridges.jpg")

    node = _flat(build_category_tree("uk"))[category.pk]

    assert node["image_url"].endswith("categories/tiles/fridges.jpg")


# ---------------------------------------------------------------------------
# 3. Чипси підкатегорій у каталозі — те саме правило
# ---------------------------------------------------------------------------
def test_visible_children_hides_empty_subcategories() -> None:
    """Порожня підкатегорія не має лишатись чипсом на сторінці батька після того, як ми
    прибрали її з меню. «Не показується НІДЕ» — це і про каталог теж."""
    root = _category("100", "Велика техніка")
    alive = _category("101", "Холодильники", parent=root)
    _category("102", "Пральні машини", parent=root)  # порожня
    _product(alive, "A1")

    children = visible_children("uk", root)

    assert [c["id"] for c in children] == [alive.pk]
    # Чипс отримує лічильник і плитку задарма — з того самого дерева.
    assert children[0]["products_count"] == 1
    assert children[0]["image_url"]


# ---------------------------------------------------------------------------
# 4. N+1 і кеш
# ---------------------------------------------------------------------------
def test_no_n_plus_1(django_assert_max_num_queries: Any) -> None:
    """Кількість запитів НЕ залежить від кількості категорій і товарів.

    Дерево віддається на КОЖНІЙ сторінці сайту (мегаменю + футер). Плитка, яку шукали б
    окремим запитом на категорію, — це +40 запитів на кожен рендер шапки.

    Стеля 3 = категорії + лічильники (GROUP BY) + плитки (DISTINCT ON).
    """
    for i in range(6):
        root = _category(f"{100 + i}", f"Корінь {i}")
        for j in range(3):
            child = _category(f"{200 + i * 10 + j}", f"Дитина {i}-{j}", parent=root)
            _product(child, f"P{i}-{j}", price=str(1000 * (j + 1)))

    with django_assert_max_num_queries(3):
        tree = build_category_tree("uk")

    assert len(tree) == 6
    assert all(node["image_url"] for node in tree)


def test_cached_call_makes_no_queries(django_assert_max_num_queries: Any) -> None:
    """Попадання в кеш = нуль запитів: у Redis лежить уже готове дерево."""
    category = _category("100", "Холодильники")
    _product(category, "A1")

    get_category_tree("uk")  # нагріли

    with django_assert_max_num_queries(0):
        assert len(get_category_tree("uk")) == 1


def test_cache_is_invalidated_on_category_save() -> None:
    """Категорію зберегли → дерево перебудовується одразу, а не через годину TTL."""
    root = _category("100", "Велика техніка")
    _product(root, "A1")
    assert len(get_category_tree("uk")) == 1

    child = _category("101", "Холодильники", parent=root)  # post_save → інвалідація
    _product(child, "B1")

    assert [n["id"] for n in get_category_tree("uk")[0]["children"]] == [child.pk]
