"""Добірки для головної: «Хіти продажів», «Акції», «Новинки», «Уцінка».

Ці тести фіксують ГОЛОВНУ вимогу блоку: він показує рівно те, що написано на його заголовку,
і НІЧОГО не показує, коли даних немає.

Передісторія. На головній стояли три банери, вигадані розробником як заглушка: «Комплект зі
знижкою до 15%», «Вітринні зразки з гарантією». Жодної такої знижки в прайсі не існувало —
це були обіцянки, за якими клієнт міг прийти в магазин. Банери знесено; замість них головну
наповнюють ці добірки, зібрані ПРЯМИМ запитом по полях, якими замовник керує з адмінки.

Тому тут перевіряється не «сервіс щось повернув», а три речі, зламати які найлегше:

  1. **Порожня добірка — це `[]`, а не «щось схоже».** Якщо в БД нема жодного товару з
     галочкою «Рекомендований», блок «Хіти продажів» мусить бути ПОРОЖНІЙ, а не «ну візьмемо
     тоді просто якісь товари». Фронт на `[]` не рендерить блок узагалі.

  2. **`sale` ≠ `clearance`.** «Акція» (є стара ціна) і «Уцінка» (товар фізично уцінений) —
     різні сутності й різні блоки. Легасі-роут /products/discounted означає САМЕ `sale`, і
     сплутати їх — означає показати уцінений товар як акційний.

  3. **Знижка не буває фейковою.** Товар з `old_price <= price` у «Акції» потрапити не може
     (на рівні БД це ловить CheckConstraint), а товар без фото — у жодну добірку.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from catalog.enums import Availability, Condition
from catalog.models import Brand, Category, Product
from catalog.services.cache import invalidate_collections_cache
from catalog.services.collections import get_collections

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    """Добірки кешуються в Redis на 5 хв — між тестами кеш мусить бути порожній,
    інакше другий тест побачить дані першого."""
    invalidate_collections_cache()
    yield
    invalidate_collections_cache()


@pytest.fixture
def category(db: Any) -> Category:
    cat = Category(external_id="5609722", name="Духові шафи", slug="dukhovi-shafy")
    cat.name_uk, cat.slug_uk = "Духові шафи", "dukhovi-shafy"
    cat.save()
    return cat


@pytest.fixture
def brand(db: Any) -> Brand:
    return Brand.objects.create(name="Bosch", slug="bosch")


def _product(
    category: Category,
    brand: Brand,
    sku: str,
    *,
    price: str = "30000",
    old_price: str | None = None,
    is_featured: bool = False,
    condition: int = Condition.NEW,
    is_active: bool = True,
    # За замовчуванням фото Є: його відсутність — окремий, свідомий випадок у тесті нижче.
    image: str = "https://example.com/p.jpg",
) -> Product:
    product = Product(
        sku=sku,
        name=f"Товар {sku}",
        slug=f"tovar-{sku}",
        category=category,
        brand=brand,
        base_price=Decimal(price),
        price=Decimal(price),
        old_price=Decimal(old_price) if old_price else None,
        is_featured=is_featured,
        condition=condition,
        is_active=is_active,
        main_image_url=image,
        availability=Availability.IN_STOCK,
    )
    product.name_uk, product.slug_uk = product.name, product.slug
    product.save()
    return product


def _ids(items: list[dict[str, Any]]) -> list[int]:
    return [i["id"] for i in items]


# ---------------------------------------------------------------------------
# 1. Порожній стан — головна вимога
# ---------------------------------------------------------------------------
def test_all_collections_empty_when_no_data(category: Category, brand: Brand) -> None:
    """У БД лише звичайні товари → усі три «кураторські» добірки ПОРОЖНІ.

    Саме цей стан у базі замовника ЗАРАЗ: 0 товарів з is_featured, 0 з old_price,
    0 з condition ≠ «Новий». Блоки на головній просто не рендеряться — і це правильно.
    Якщо колись хтось «підстрахується» і почне сипати сюди випадкові товари — тест впаде.
    """
    _product(category, brand, "A1")
    _product(category, brand, "A2")

    data = get_collections("uk")

    assert data["featured"] == []
    assert data["sale"] == []
    assert data["clearance"] == []
    # «Новинки» — єдина добірка, яка не потребує кураторства: будь-який товар колись створено.
    assert len(data["new"]) == 2


# ---------------------------------------------------------------------------
# 2. Кожна добірка бере СВОЇ товари
# ---------------------------------------------------------------------------
def test_featured_takes_only_flagged(category: Category, brand: Brand) -> None:
    """«Хіти продажів» = рівно ті товари, де замовник поставив галочку в адмінці."""
    hit = _product(category, brand, "HIT", is_featured=True)
    _product(category, brand, "ORDINARY")

    assert _ids(get_collections("uk")["featured"]) == [hit.pk]


def test_sale_takes_only_real_discounts(category: Category, brand: Brand) -> None:
    """«Акції» = є стара ціна, і вона БІЛЬША за поточну."""
    discounted = _product(category, brand, "SALE", price="30000", old_price="36000")
    _product(category, brand, "NO_DISCOUNT", price="30000", old_price=None)

    assert _ids(get_collections("uk")["sale"]) == [discounted.pk]


def test_clearance_takes_only_non_new_condition(category: Category, brand: Brand) -> None:
    """«Уцінка» = будь-який стан, крім «Новий» (уцінений / відновлений / б/в)."""
    used = _product(category, brand, "USED", condition=Condition.DISCOUNTED)
    refurb = _product(category, brand, "REF", condition=Condition.REFURBISHED)
    _product(category, brand, "NEW", condition=Condition.NEW)

    assert set(_ids(get_collections("uk")["clearance"])) == {used.pk, refurb.pk}


def test_sale_and_clearance_are_different_buckets(category: Category, brand: Brand) -> None:
    """⚠️ Акція ≠ уцінка. Найлегша помилка — злити їх в одне «discounted».

    Уцінений товар БЕЗ знижки не має потрапити в «Акції», а акційний НОВИЙ товар —
    в «Уцінку». Товар, що є і тим, і тим, чесно потрапляє в обидва блоки.
    """
    only_sale = _product(category, brand, "S", price="100", old_price="150")
    only_clearance = _product(category, brand, "C", condition=Condition.DISCOUNTED)
    both = _product(
        category, brand, "B", price="100", old_price="150", condition=Condition.DISCOUNTED
    )

    data = get_collections("uk")

    assert set(_ids(data["sale"])) == {only_sale.pk, both.pk}
    assert set(_ids(data["clearance"])) == {only_clearance.pk, both.pk}


# ---------------------------------------------------------------------------
# 3. Що в добірки НЕ потрапляє
# ---------------------------------------------------------------------------
def test_inactive_product_never_shows(category: Category, brand: Brand) -> None:
    """Прихований товар не з'являється на головній, навіть із галочкою «Рекомендований»."""
    _product(category, brand, "HIDDEN", is_featured=True, is_active=False)

    data = get_collections("uk")

    assert data["featured"] == []
    assert data["new"] == []


def test_product_without_photo_never_shows(category: Category, brand: Brand) -> None:
    """Товар без фото — сіра діра в каруселі. Картка бере фото з main_image_url."""
    _product(category, brand, "NO_PHOTO", is_featured=True, image="")

    assert get_collections("uk")["featured"] == []


def test_inactive_category_hides_product(category: Category, brand: Brand) -> None:
    """Категорію вимкнули → її товари зникають з головної разом з нею."""
    _product(category, brand, "IN_HIDDEN_CAT", is_featured=True)
    Category.objects.filter(pk=category.pk).update(is_active=False)

    assert get_collections("uk")["featured"] == []


# ---------------------------------------------------------------------------
# 4. Ліміт і порядок
# ---------------------------------------------------------------------------
def test_limit_is_respected(category: Category, brand: Brand) -> None:
    for i in range(8):
        _product(category, brand, f"P{i}", is_featured=True)

    assert len(get_collections("uk", limit=3)["featured"]) == 3


def test_new_is_ordered_newest_first(category: Category, brand: Brand) -> None:
    """«Новинки» — найновіші спереду.

    ⚠️ Тай-брейк по -id критичний: синк створює сотні товарів в ОДНУ мілісекунду, і без
    нього порядок між ними був би недетермінований — головна «перетасовувалась» би на
    кожну ревалідацію ISR.
    """
    first = _product(category, brand, "OLD")
    second = _product(category, brand, "NEW")

    assert _ids(get_collections("uk")["new"])[0] == second.pk
    assert first.pk in _ids(get_collections("uk")["new"])


# ---------------------------------------------------------------------------
# 5. N+1
# ---------------------------------------------------------------------------
def test_no_n_plus_1(category: Category, brand: Brand, django_assert_max_num_queries: Any) -> None:
    """Кількість запитів НЕ залежить від кількості товарів у добірках.

    ⚠️ Головна — найвідвідуваніша сторінка сайту, і саме тут N+1 найдорожчий. Свотчі кольорів
    (`cards.swatches`) лізуть у `membership.group.items`, тобто БЕЗ prefetch кожен товар зі
    свотч-групою додає СВІЙ запит. 4 блоки × 12 товарів = ~100 запитів на одне відкриття
    головної — і помітно це стало б лише в проді.

    Стеля 16 = 4 добірки × (1 запит на товари + prefetch варіантів) + дерево категорій
    (категорії + лічильники + плитки) + вибірка топ-категорій + 1 карусель `by_category`.
    Головне тут не точне число, а те, що воно ОДНАКОВЕ для 2 і для 30 товарів — і що воно
    НЕ росте з кількістю категорій у каруселі (їх максимум 3, і кожна — рівно один запит).
    """
    for i in range(30):
        _product(
            category,
            brand,
            f"P{i}",
            is_featured=True,
            old_price="99999",
            condition=Condition.DISCOUNTED,
        )

    invalidate_collections_cache()  # міряємо ПРОМАХ кешу — найгірший випадок
    with django_assert_max_num_queries(16):
        data = get_collections("uk")

    # Добірки справді наповнені — інакше тест «нуль запитів на нуль товарів» був би зеленим ні про що.
    assert len(data["featured"]) == 12
    assert len(data["sale"]) == 12


def test_cached_call_makes_no_queries(
    category: Category, brand: Brand, django_assert_max_num_queries: Any
) -> None:
    """Попадання в кеш = НУЛЬ запитів у БД: у Redis лежать готові dict'и, а не Product-моделі."""
    _product(category, brand, "HIT", is_featured=True)

    get_collections("uk")  # нагріли кеш

    with django_assert_max_num_queries(0):
        assert len(get_collections("uk")["featured"]) == 1


# ---------------------------------------------------------------------------
# 6. Кеш
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 7. by_category — каруселі по категоріях
# ---------------------------------------------------------------------------
def _root(external_id: str, name: str) -> Category:
    category = Category(external_id=external_id, name=name, slug=f"cat-{external_id}")
    category.name_uk, category.slug_uk = name, category.slug
    category.save()
    return category


def test_by_category_returns_three_biggest_roots(brand: Brand) -> None:
    """Три НАЙБІЛЬШІ непорожні кореневі категорії — саме те, чим зараз наповнена база.

    Це заміна порожнім «Хітам» і «Акціям»: у базі 0 товарів з is_featured і 0 зі старою ціною,
    тому головна, зібрана лише з кураторських добірок, — це чотири заголовки і нічого під ними.
    """
    big = _root("100", "Вбудована техніка")
    mid = _root("200", "Дрібна техніка")
    small = _root("300", "Аудіо")
    tiny = _root("400", "Краса")  # 4-та за розміром — у трійку не входить

    for i in range(4):
        _product(big, brand, f"BIG{i}")
    for i in range(3):
        _product(mid, brand, f"MID{i}")
    for i in range(2):
        _product(small, brand, f"SML{i}")
    _product(tiny, brand, "TINY")

    blocks = get_collections("uk")["by_category"]

    assert [b["id"] for b in blocks] == [big.pk, mid.pk, small.pk]
    assert [b["name"] for b in blocks] == ["Вбудована техніка", "Дрібна техніка", "Аудіо"]
    assert [len(b["products"]) for b in blocks] == [4, 3, 2]


def test_by_category_skips_empty_categories(brand: Brand) -> None:
    """Порожня категорія не стає каруселлю — так само, як не стає плиткою в дереві."""
    alive = _root("100", "Вбудована техніка")
    _root("200", "Генератори")  # 0 товарів
    _product(alive, brand, "A1")

    blocks = get_collections("uk")["by_category"]

    assert [b["name"] for b in blocks] == ["Вбудована техніка"]


def test_by_category_includes_products_of_subcategories(brand: Brand) -> None:
    """Кореневі категорії власних товарів майже не мають — товари висять на листках.

    Без скоупу «категорія + нащадки» карусель під плиткою «Вбудована техніка (164)» була б
    порожня, і саме так виглядав би найдорожчий баг цього блоку.
    """
    root = _root("100", "Вбудована техніка")
    child = Category(external_id="101", name="Духові шафи", slug="dukhovi", parent=root)
    child.name_uk, child.slug_uk = child.name, child.slug
    child.save()
    deep = _product(child, brand, "DEEP")

    blocks = get_collections("uk")["by_category"]

    assert [b["id"] for b in blocks] == [root.pk]
    assert _ids(blocks[0]["products"]) == [deep.pk]


def test_by_category_caps_products_per_block(brand: Brand) -> None:
    """Карусель — не каталог: максимум 8 карток на категорію."""
    root = _root("100", "Вбудована техніка")
    for i in range(12):
        _product(root, brand, f"P{i}")

    blocks = get_collections("uk")["by_category"]

    assert len(blocks[0]["products"]) == 8


def test_by_category_products_have_photo_and_are_active(brand: Brand) -> None:
    """Ті самі правила, що й у решти добірок: без фото і без активності товару в каруселі немає."""
    root = _root("100", "Вбудована техніка")
    visible = _product(root, brand, "OK")
    _product(root, brand, "NO_PHOTO", image="")
    _product(root, brand, "HIDDEN", is_active=False)

    blocks = get_collections("uk")["by_category"]

    assert _ids(blocks[0]["products"]) == [visible.pk]


def test_cache_is_invalidated_on_product_save(category: Category, brand: Brand) -> None:
    """Замовник ставить галочку в адмінці → товар з'являється в «Хітах» ОДРАЗУ, а не через TTL.

    Це саме той шлях, яким замовник керує головною. Якби post_save не зносив кеш добірок,
    він поставив би галочку, оновив головну — і нічого б не змінилось (5 хв TTL). Класичний
    баг «я все зробив, а сайт не оновився».
    """
    assert get_collections("uk")["featured"] == []  # нагріваємо кеш порожнім результатом

    product = _product(
        category, brand, "HIT", is_featured=True
    )  # .save() → post_save → інвалідація

    assert _ids(get_collections("uk")["featured"]) == [product.pk]
