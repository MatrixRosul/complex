"""Пошук за АРТИКУЛОМ і єдність побудови пошукового вектора.

Два критичні баги, які цей файл фіксує назавжди:

1. **sku не потрапляв у tsvector.** `/search?q=2401574` віддавав 0 результатів, хоча товар
   з таким артикулом існує, а плейсхолдер поля пошуку прямо обіцяє «Пошук товарів, брендів,
   артикулів…». Причина: `_fields()` будував вектор з (name, бренд+категорія, specs, опис)
   і НІКОЛИ не включав sku.

2. **Синк будував вектор ІНШОЮ функцією.** `sync.services.rebuild_denorm` мав власний інлайн
   `SearchVector("name_uk", config="uk")`: без sku, без бренду/категорії/характеристик, без
   `setweight` і — найгірше — БЕЗ українського стемера, хоча запит у `search.py` стемиться.
   Тобто товар, що пройшов синк, отримував ЗІПСОВАНИЙ вектор і мовчки випадав з пошуку.
   Тест нижче ловить саме це: вектор ПІСЛЯ денорму мусить збігатися з канонічним.

Каталог до цього не мав ЖОДНОГО тесту — обидва баги тому й дожили до продакшену.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from catalog.enums import Availability
from catalog.models import Brand, Category, Product
from catalog.services.search import search_products
from catalog.services.search_index import rebuild_search_vectors

pytestmark = pytest.mark.django_db


def _vector(pk: int) -> str:
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT search_vector_uk::text FROM catalog_product WHERE id = %s", [pk])
        return cur.fetchone()[0] or ""


@pytest.fixture
def oven(db: Any) -> Product:
    """Реальний товар із бази замовника: sku 2401574."""
    category = Category(external_id="5609722", name="Духові шафи", slug="dukhovi-shafy")
    category.name_uk, category.slug_uk = "Духові шафи", "dukhovi-shafy"
    category.save()

    brand = Brand.objects.create(name="Bosch", slug="bosch")

    product = Product(
        sku="2401574",
        name="Духова шафа Bosch CMG7241B1",
        slug="bosch-cmg7241b1",
        category=category,
        brand=brand,
        base_price=Decimal("30000"),
        price=Decimal("30000"),
        availability=Availability.IN_STOCK,
    )
    product.name_uk, product.slug_uk = product.name, product.slug
    product.save()

    rebuild_search_vectors(Product.objects.filter(pk=product.pk))
    product.refresh_from_db()
    return product


def test_sku_lands_in_search_vector_with_weight_a(oven: Product) -> None:
    """Артикул мусить бути у векторі, і саме вагою 'A' (нарівні з назвою)."""
    vector = _vector(oven.pk)
    lexeme = next((part for part in vector.split() if part.startswith("'2401574':")), None)

    assert lexeme is not None, f"артикула немає у векторі зовсім: {vector}"
    assert "A" in lexeme, f"артикул є, але не вагою 'A': {lexeme}"


def test_search_by_full_sku(oven: Product) -> None:
    """ГОЛОВНИЙ баг: цей запит віддавав 0 результатів."""
    assert list(search_products("2401574", "uk").values_list("pk", flat=True)) == [oven.pk]


def test_search_by_sku_prefix(oven: Product) -> None:
    """Людина не дописала артикул до кінця — товар усе одно мусить знайтись."""
    assert oven.pk in set(search_products("240157", "uk").values_list("pk", flat=True))


def test_exact_sku_outranks_everything(oven: Product) -> None:
    """Точний збіг за артикулом — ПЕРШИЙ рядок видачі, а не десь у хвості."""
    noise = Product(
        sku="9999999",
        # Артикул шуканого товару згадується в НАЗВІ іншого — класична пастка ранжування.
        name="Кабель для духової шафи 2401574 сумісний",
        slug="noise",
        category=oven.category,
        base_price=Decimal("100"),
        price=Decimal("100"),
        availability=Availability.IN_STOCK,
    )
    noise.name_uk, noise.slug_uk = noise.name, noise.slug
    noise.save()
    rebuild_search_vectors(Product.objects.filter(pk=noise.pk))

    top = next(iter(search_products("2401574", "uk").values_list("pk", flat=True)))
    assert top == oven.pk


def test_text_search_still_works(oven: Product) -> None:
    """Регресія: додавання sku не має ламати звичайний пошук по словах."""
    for query in ("духова шафа", "Bosch", "духові"):
        found = set(search_products(query, "uk").values_list("pk", flat=True))
        assert oven.pk in found, query


def test_sync_denorm_builds_the_same_vector_as_canonical_rebuild(oven: Product) -> None:
    """🔴 КОРІНЬ БАГА «пошук не працює після синку».

    `rebuild_denorm` (те, що запускає СИНК) будував вектор власним `SearchVector("name_uk")`
    і затирав ним канонічний: зникали sku, бренд, категорія, характеристики, ваги і стемінг.
    Тепер обидва шляхи мусять дати БАЙТ У БАЙТ той самий вектор.
    """
    from sync.services import rebuild_denorm

    canonical = _vector(oven.pk)
    assert "'2401574':" in canonical  # вихідна точка — вектор правильний

    rebuild_denorm([oven.pk])  # рівно те, що робить синк

    assert _vector(oven.pk) == canonical
    # І товар усе ще знаходиться за артикулом ПІСЛЯ синку — власне те, що ламалось.
    assert list(search_products("2401574", "uk").values_list("pk", flat=True)) == [oven.pk]
