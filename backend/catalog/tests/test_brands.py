"""Рядок брендів на головній: топ-N виробників за кількістю активних товарів.

⚠️ ГОЛОВНЕ, ЩО ТУТ ПЕРЕВІРЯЄТЬСЯ, — `slug`, А НЕ КРАСА СПИСКУ.
   Плитка бренду веде у каталог з фасетом: `/catalog?brand={slug}`. Фасет фільтрує по токену
   `brand:{slug}` (Product.filter_tokens), тому `?brand=Bosch` — це нуль товарів. Якщо хтось
   колись «спростить» відповідь до одного лише `name`, увесь рядок брендів мовчки перетвориться
   на 12 посилань у порожнечу. Саме цей контракт фіксує test_slug_matches_filter_token.

Лічильник рахується з тієї самої умови, що й лістинг (активний товар в активній категорії) —
інакше «Bosch (70)» вело б у каталог, де 66.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from catalog.enums import Availability
from catalog.models import Brand, Category, Product
from catalog.services.brands import get_top_brands
from catalog.services.cache import invalidate_catalog_cache

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    """Рядок брендів кешується на годину — між тестами кеш мусить бути порожній."""
    invalidate_catalog_cache()
    yield
    invalidate_catalog_cache()


@pytest.fixture
def category(db: Any) -> Category:
    cat = Category(external_id="5609722", name="Духові шафи", slug="dukhovi-shafy")
    cat.name_uk, cat.slug_uk = "Духові шафи", "dukhovi-shafy"
    cat.save()
    return cat


def _brand(name: str, slug: str, *, is_active: bool = True) -> Brand:
    return Brand.objects.create(name=name, slug=slug, is_active=is_active)


def _products(
    category: Category,
    brand: Brand | None,
    n: int,
    *,
    is_active: bool = True,
    tag: str = "",
    denormed: bool = True,
) -> None:
    """Створює товари РАЗОМ з токеном `brand:{slug}` — саме так їх лишає синк.

    ⚠️ `denormed=False` імітує товар, чий денорм ще не прогнався (`filter_tokens=[]`). Це не
       екзотика: у живій базі таких зараз три (кавоварки Bosch) — і саме через них лічильник по
       FK розходився з каталогом.
    """
    for i in range(n):
        # `tag` — щоб два виклики на той самий бренд не зіштовхнулись на unique(sku).
        prefix = f"{brand.slug if brand else 'nobrand'}{tag}"
        product = Product(
            sku=f"{prefix}-{i}",
            name=f"Товар {prefix}-{i}",
            slug=f"tovar-{prefix}-{i}",
            category=category,
            brand=brand,
            base_price=Decimal("10000"),
            price=Decimal("10000"),
            main_image_url="https://cdn.example.com/p.webp",
            is_active=is_active,
            availability=Availability.IN_STOCK,
            filter_tokens=[f"brand:{brand.slug}"] if brand and denormed else [],
        )
        product.name_uk, product.slug_uk = product.name, product.slug
        product.save()


def _names(items: list[dict[str, Any]]) -> list[str]:
    return [b["name"] for b in items]


# ---------------------------------------------------------------------------
# 1. Порядок і лічильник
# ---------------------------------------------------------------------------
def test_sorted_by_products_count_desc(category: Category) -> None:
    """Рядок брендів — це «наші основні виробники», тобто спершу ті, кого в нас найбільше."""
    bosch = _brand("Bosch", "bosch")
    gorenje = _brand("Gorenje", "gorenje")
    smeg = _brand("SMEG", "smeg")

    _products(category, gorenje, 2)
    _products(category, bosch, 5)
    _products(category, smeg, 1)

    result = get_top_brands()

    assert _names(result) == ["Bosch", "Gorenje", "SMEG"]
    assert [b["products_count"] for b in result] == [5, 2, 1]


def test_ties_are_broken_by_name(category: Category) -> None:
    """Однакова кількість → алфавіт. Без тай-брейка два бренди мінялися б місцями між запитами,
    і рядок брендів «мерехтів» би на кожній ревалідації ISR."""
    _products(category, _brand("Zanussi", "zanussi"), 3)
    _products(category, _brand("AEG", "aeg"), 3)

    assert _names(get_top_brands()) == ["AEG", "Zanussi"]


def test_limit_is_respected(category: Category) -> None:
    for i in range(5):
        _products(category, _brand(f"Brand{i}", f"brand{i}"), 5 - i)

    assert len(get_top_brands(limit=3)) == 3


# ---------------------------------------------------------------------------
# 2. Кого в рядку НЕ буде
# ---------------------------------------------------------------------------
def test_brand_without_products_is_hidden(category: Category) -> None:
    """Бренд без товарів — це посилання в порожній каталог. Те саме правило, що й для
    порожніх категорій у дереві."""
    _products(category, _brand("Bosch", "bosch"), 1)
    _brand("Ghost", "ghost")  # заведений синком «про запас», товарів немає

    assert _names(get_top_brands()) == ["Bosch"]


def test_inactive_products_are_not_counted(category: Category) -> None:
    """Прихований товар не рахується — інакше лічильник розійшовся б з каталогом."""
    bosch = _brand("Bosch", "bosch")
    _products(category, bosch, 2)
    _products(category, bosch, 3, is_active=False, tag="-off")

    assert get_top_brands()[0]["products_count"] == 2


def test_inactive_category_products_are_not_counted(category: Category) -> None:
    """Категорію вимкнули → її товари зникають і з каталогу, і з лічильника бренду."""
    bosch = _brand("Bosch", "bosch")
    _products(category, bosch, 4)
    Category.objects.filter(pk=category.pk).update(is_active=False)

    assert get_top_brands() == []


def test_inactive_brand_is_hidden(category: Category) -> None:
    _products(category, _brand("Dead", "dead", is_active=False), 3)

    assert get_top_brands() == []


# ---------------------------------------------------------------------------
# 3. Контракт з фасетом
# ---------------------------------------------------------------------------
def test_slug_matches_filter_token(category: Category) -> None:
    """⚠️ КЛЮЧОВИЙ ТЕСТ. `slug` з відповіді має дорівнювати суфіксу токена `brand:{...}` у
    `Product.filter_tokens` — інакше `/catalog?brand={slug}` віддасть порожню видачу.

    Токен пише денормалізація (sync/services.py::rebuild_product_denorm).
    """
    bosch = _brand("Bosch", "bosch")
    _products(category, bosch, 1)

    item = get_top_brands()[0]

    assert item["slug"] == "bosch"
    tokens = Product.objects.filter(brand=bosch).values_list("filter_tokens", flat=True)[0]
    assert f"brand:{item['slug']}" in tokens
    # А ось назва як фільтр НЕ працює: токен у нижньому регістрі й латиницею.
    assert f"brand:{item['name']}" not in tokens


def test_count_matches_catalog_not_fk(category: Category) -> None:
    """⚠️ ЛІЧИЛЬНИК = СКІЛЬКИ ТОВАРІВ ПОКАЖЕ КАТАЛОГ, а не скільки їх висить на FK.

    Товар з порожніми `filter_tokens` (денорм ще не прогнався) у каталозі за `?brand=bosch` НЕ
    знайдеться — отже, і в лічильнику його бути не може. Живий приклад: три кавоварки Bosch у
    базі замовника давали «Bosch (70)» по FK при 67 у каталозі. Саме так лічильникам на сайті
    перестають вірити.
    """
    bosch = _brand("Bosch", "bosch")
    _products(category, bosch, 2)
    _products(category, bosch, 3, tag="-dirty", denormed=False)

    assert Product.objects.filter(brand=bosch).count() == 5  # по FK їх п'ять…
    assert get_top_brands()[0]["products_count"] == 2  # …а в каталозі видно два


def test_logo_url_is_none_when_no_logo(category: Category) -> None:
    """Логотипів немає в жодного з 49 брендів — це нормальна, а не помилкова відповідь."""
    _products(category, _brand("Bosch", "bosch"), 1)

    assert get_top_brands()[0]["logo_url"] is None


# ---------------------------------------------------------------------------
# 4. Запити
# ---------------------------------------------------------------------------
def test_no_n_plus_1(category: Category, django_assert_max_num_queries: Any) -> None:
    """Два запити на весь рядок: лічильники (unnest + GROUP BY) і самі бренди. Не «по запиту
    на бренд» — інакше рядок з 12 логотипів коштував би 13 запитів на кожній головній."""
    for i in range(10):
        _products(category, _brand(f"Brand{i}", f"brand{i}"), 2)

    invalidate_catalog_cache()  # міряємо промах кешу
    with django_assert_max_num_queries(2):
        assert len(get_top_brands(limit=12)) == 10


def test_cached_call_makes_no_queries(
    category: Category, django_assert_max_num_queries: Any
) -> None:
    _products(category, _brand("Bosch", "bosch"), 1)

    get_top_brands()  # нагріли

    with django_assert_max_num_queries(0):
        assert len(get_top_brands()) == 1
