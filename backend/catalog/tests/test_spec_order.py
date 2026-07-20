"""Порядок характеристик усередині групи = порядок з адмінки, а НЕ алфавіт.

⚠️ ЩО ТУТ ЗАФІКСОВАНО. У `grouped_specs` тайбрейком стояла НАЗВА: `(sort, name)`. Оскільки
   у ~95% характеристик `Attribute.sort_order` лежить на дефолті (100) і в `specs_json` він
   до того ж не писався взагалі, ключ вироджувався в `(0, name)` — і група перевпорядковувалась
   за алфавітом. В адмінці замовник бачив «Управління», далі «…для монтажу» (там тайбрейк — id),
   а на сайті «Висота для монтажу» пролізала ПОПЕРЕД «Управління». Класична скарга
   «в адмінці порядок нормальний, на сайті влізло між ними».

   `specs_json` уже складений у правильному порядку (rebuild_denorm сортує по
   group.sort_order → attribute.sort_order → attribute_id), тому єдине, що має робити
   рендер, — НЕ ЗІПСУВАТИ його. Тайбрейк — позиція в списку.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from catalog.models import Attribute, AttributeGroup, Category, Product, ProductAttributeValue
from catalog.services.cards import grouped_specs

pytestmark = pytest.mark.django_db


# Порядок як в адмінці: «Управління» перед монтажними розмірами. За алфавітом було б навпаки —
# «Висота для монтажу» < «Глибина…» < «Управління».
SPECS = [
    {
        "g": "Загальні характеристики",
        "gs": 60,
        "code": "upravlinnia",
        "n": "Управління",
        "u": "",
        "v": "сенсорне",
    },
    {
        "g": "Загальні характеристики",
        "gs": 60,
        "code": "vysota_montazh",
        "n": "Висота для монтажу",
        "u": "мм",
        "v": "590",
    },
    {
        "g": "Загальні характеристики",
        "gs": 60,
        "code": "shyryna_montazh",
        "n": "Ширина для монтажу",
        "u": "мм",
        "v": "560",
    },
    {
        "g": "Загальні характеристики",
        "gs": 60,
        "code": "hlybyna_montazh",
        "n": "Глибина для монтажу",
        "u": "мм",
        "v": "550",
    },
]


@pytest.fixture
def product(db: Any) -> Product:
    cat = Category(external_id="5609722", name="Духові шафи", slug="dukhovi-shafy")
    cat.name_uk, cat.slug_uk = "Духові шафи", "dukhovi-shafy"
    cat.save()
    prod = Product(
        sku="X1",
        name="Тест",
        slug="test",
        category=cat,
        base_price=Decimal("1000"),
        price=Decimal("1000"),
    )
    prod.name_uk, prod.slug_uk = "Тест", "test"
    prod.specs_json = prod.specs_json_uk = SPECS
    prod.save()
    return prod


def test_order_follows_specs_json_not_alphabet(product: Product) -> None:
    """Головне: порядок зі specs_json зберігається, навіть коли `s` немає зовсім."""
    rows = grouped_specs(product, "uk")[0]["rows"]
    assert [r["name"] for r in rows] == [s["n"] for s in SPECS]


def test_explicit_sort_order_wins(product: Product) -> None:
    """А якщо `s` проставлений (замовник змінив порядок в адмінці) — виграє він."""
    specs = [dict(row, s=order) for row, order in zip(SPECS, (40, 10, 20, 30), strict=True)]
    product.specs_json = product.specs_json_uk = specs
    product.save(update_fields=["specs_json", "specs_json_uk"])

    rows = grouped_specs(product, "uk")[0]["rows"]
    assert [r["name"] for r in rows] == [
        "Висота для монтажу",
        "Ширина для монтажу",
        "Глибина для монтажу",
        "Управління",
    ]


def test_internal_position_key_does_not_leak(product: Product) -> None:
    """`_pos` — службовий; у відповідь API він потрапити не має."""
    for row in grouped_specs(product, "uk")[0]["rows"]:
        assert "_pos" not in row


# ---------------------------------------------------------------------------
# Зміна порядку в адмінці мусить дійти до specs_json
# ---------------------------------------------------------------------------


@pytest.fixture
def attribute(product: Product) -> Attribute:
    group = AttributeGroup.objects.create(
        name="Загальні характеристики", code="general", sort_order=60
    )
    attr = Attribute.objects.create(
        name="Управління",
        code="upravlinnia",
        group=group,
        value_type=Attribute.ValueType.STRING,
        sort_order=100,
    )
    ProductAttributeValue.objects.create(product=product, attribute=attr, value_string="сенсорне")
    Product.objects.filter(pk=product.pk).update(denorm_dirty=False)
    return attr


def _is_dirty(product: Product) -> bool:
    return Product.objects.filter(pk=product.pk, denorm_dirty=True).exists()


def test_sort_order_change_marks_products_dirty(product: Product, attribute: Attribute) -> None:
    """Інакше замовник міняє порядок в адмінці, а на сайті — тиша до наступного синку."""
    attribute.sort_order = 10
    attribute.save()

    assert _is_dirty(product)


def test_group_sort_order_change_marks_products_dirty(
    product: Product, attribute: Attribute
) -> None:
    attribute.group.sort_order = 5
    attribute.group.save()

    assert _is_dirty(product)


def test_unrelated_edit_leaves_products_alone(product: Product, attribute: Attribute) -> None:
    """Синк зберігає атрибути пачками — перейменування не має мітити тисячі товарів брудними."""
    attribute.name = "Тип управління"
    attribute.save()

    assert not _is_dirty(product)
