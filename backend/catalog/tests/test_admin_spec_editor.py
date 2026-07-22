"""Редактор характеристик товару в адмінці: ОДНА колонка «Значення», а не п'ять.

ЩО ТУТ ЗАФІКСОВАНО. Інлайн малював кожному рядку всі колонки значення одразу — варіант,
текст uk, текст ru, число, так/ні. Через це:

  * таблиця не влазила в екран і редагувалась горизонтальним скролом;
  * навпроти «Тип варильної поверхні: газова» стояв порожній спінер числа і випадайка
    «Так/Ні» — три поля, з яких правильне лише одне, і менеджер щоразу вгадував, у яке
    з них писати (а помилка тиха: значення просто не показалось би на картці);
  * випадайка «Так/Ні» показувала обрізане російське «Неиз[вестно]» — при тому, що
    інтерфейс адмінки український.

Тип значення для цього НЕ треба вигадувати й НЕ треба вішати на групу: він уже є на
`Attribute.value_type`. Група («Основні», «Габаритні розміри») — це підзаголовок у картці,
і всередині однієї групи законно стоять поруч число, текст і так/ні. Тому редактор просто
читає тип характеристики РЯДКА і малює рівно одне потрібне поле.

⚠️ Найдорожчий тут — `test_untouched_row_survives_value_type_change`: якщо форма писатиме
   значення в кожен рядок безумовно, то зміна `value_type` заднім числом (а вона буває —
   синк уточнює тип) МОВЧКИ витирала б усі вже зібрані значення при першому ж «Зберегти».
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django import forms
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from catalog.admin import ProductAttributeValueInline
from catalog.models import (
    Attribute,
    AttributeGroup,
    AttributeOption,
    Category,
    Product,
    ProductAttributeValue,
)

pytestmark = pytest.mark.django_db

VT = Attribute.ValueType


# ---------------------------------------------------------------------------
# Фікстури
# ---------------------------------------------------------------------------
@pytest.fixture
def product(db: Any) -> Product:
    cat = Category(external_id="5609722", name="Плити", slug="plyty")
    cat.name_uk, cat.slug_uk = "Плити", "plyty"
    cat.save()
    prod = Product(
        sku="X1",
        name="Плита",
        slug="plyta",
        category=cat,
        base_price=Decimal("1000"),
        price=Decimal("1000"),
    )
    prod.name_uk, prod.slug_uk = "Плита", "plyta"
    prod.save()
    return prod


@pytest.fixture
def group(db: Any) -> AttributeGroup:
    return AttributeGroup.objects.create(name="Основні", code="main", sort_order=10)


def _attr(group: AttributeGroup, code: str, name: str, value_type: str) -> Attribute:
    return Attribute.objects.create(code=code, name=name, group=group, value_type=value_type)


def _option(attr: Attribute, value: str, slug: str) -> AttributeOption:
    opt = AttributeOption.objects.create(attribute=attr, value=value, slug=slug)
    opt.value_uk = value
    opt.save(update_fields=["value_uk"])
    return opt


def _request() -> Any:
    request = RequestFactory().get("/")
    request.user = get_user_model()(is_superuser=True, is_staff=True, is_active=True)
    return request


def _formset(product: Product, data: dict[str, str] | None = None) -> Any:
    inline = ProductAttributeValueInline(Product, site)
    formset_class = inline.get_formset(_request(), product)
    return formset_class(data=data, instance=product) if data else formset_class(instance=product)


def _post(product: Product, rows: list[dict[str, str]]) -> Any:
    """Payload інлайну — з реальним префіксом формсета, а не вгаданим рядком."""
    prefix = _formset(product).prefix
    initial = sum(1 for row in rows if row.get("id"))
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f"{prefix}-{index}-{key}"] = value
    return _formset(product, data)


# ---------------------------------------------------------------------------
# 1. Колонок стало менше — це і є «не треба їздити скролом»
# ---------------------------------------------------------------------------
def test_value_columns_collapsed_into_one(product: Product) -> None:
    """П'яти колонок значення в формі немає — є одна `value`."""
    rendered = set(_formset(product).empty_form.fields)

    assert "value" in rendered
    assert rendered.isdisjoint(
        {
            "option",
            "value_string",
            "value_string_uk",
            "value_string_ru",
            "value_number",
            "value_bool",
        }
    )


# ---------------------------------------------------------------------------
# 2. Віджет рядка = тип ЙОГО характеристики
# ---------------------------------------------------------------------------
def test_number_row_gets_number_input(product: Product, group: AttributeGroup) -> None:
    attr = _attr(group, "temp", "Максимальна температура", VT.NUMBER)
    ProductAttributeValue.objects.create(
        product=product, attribute=attr, value_number=Decimal("240")
    )

    field = _formset(product).forms[0].fields["value"]

    assert isinstance(field, forms.DecimalField)
    # «240», а не «240,0000»: хвости нулів у полі вводу — це шум.
    assert _formset(product).forms[0].initial["value"] == "240"


def test_option_row_offers_only_its_own_options(product: Product, group: AttributeGroup) -> None:
    """Випадайка варіантів обмежена характеристикою рядка, а не всіма 6000 опціями каталогу."""
    hob = _attr(group, "hob", "Тип варильної поверхні", VT.OPTION)
    gas = _option(hob, "газова", "hazova")
    _option(hob, "електрична", "elektrychna")
    other = _attr(group, "color", "Колір", VT.OPTION)
    _option(other, "чорний", "chornyi")
    ProductAttributeValue.objects.create(product=product, attribute=hob, option=gas)

    field = _formset(product).forms[0].fields["value"]

    assert isinstance(field, forms.ModelChoiceField)
    assert set(field.queryset.values_list("value", flat=True)) == {"газова", "електрична"}


def test_bool_row_is_ukrainian(product: Product, group: AttributeGroup) -> None:
    """Дефолтний NullBooleanSelect давав «Неиз[вестно]» — інтерфейс адмінки лише український."""
    attr = _attr(group, "ignition", "Автопідпал", VT.BOOL)
    ProductAttributeValue.objects.create(product=product, attribute=attr, value_bool=True)

    form = _formset(product).forms[0]

    assert [label for _, label in form.fields["value"].choices] == ["—", "Так", "Ні"]
    assert form.initial["value"] == "1"
    assert "Неиз" not in str(form["value"])


def test_string_row_gets_text_input(product: Product, group: AttributeGroup) -> None:
    attr = _attr(group, "control", "Управління конфорками", VT.STRING)
    ProductAttributeValue.objects.create(
        product=product, attribute=attr, value_string="поворотні перемикачі"
    )

    form = _formset(product).forms[0]

    assert isinstance(form.fields["value"], forms.CharField)
    assert form.initial["value"] == "поворотні перемикачі"


# ---------------------------------------------------------------------------
# 3. Збереження кладе значення в ПОТРІБНУ колонку
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value_type", "typed", "field", "expected"),
    [
        (VT.NUMBER, "240,5", "value_number", Decimal("240.5")),  # кома — як на укр. клавіатурі
        (VT.NUMBER, "85", "value_number", Decimal("85")),
        (VT.BOOL, "так", "value_bool", True),
        (VT.BOOL, "немає", "value_bool", False),
        (VT.STRING, "сенсорне", "value_string", "сенсорне"),
    ],
)
def test_new_row_typed_as_text_lands_in_right_column(
    product: Product,
    group: AttributeGroup,
    value_type: str,
    typed: str,
    field: str,
    expected: Any,
) -> None:
    """У НОВОМУ рядку тип ще не відомий (характеристику обирають тут же) — значення вводять
    текстом, а форма зводить його до типу обраної характеристики."""
    attr = _attr(group, "x", "X", value_type)

    formset = _post(product, [{"attribute": str(attr.pk), "value": typed, "sort_order": "0"}])
    assert formset.is_valid(), formset.errors
    formset.save()

    pav = ProductAttributeValue.objects.get(product=product, attribute=attr)
    assert getattr(pav, field) == expected


def test_new_option_row_matches_existing_option(product: Product, group: AttributeGroup) -> None:
    attr = _attr(group, "hob", "Тип варильної поверхні", VT.OPTION)
    gas = _option(attr, "газова", "hazova")

    formset = _post(product, [{"attribute": str(attr.pk), "value": "Газова", "sort_order": "0"}])
    assert formset.is_valid(), formset.errors
    formset.save()

    assert ProductAttributeValue.objects.get(product=product, attribute=attr).option_id == gas.pk


def test_unknown_option_is_rejected_not_created(product: Product, group: AttributeGroup) -> None:
    """Одруківка не має тихо народжувати новий варіант — це прямий шлях до брудних фасетів."""
    attr = _attr(group, "hob", "Тип варильної поверхні", VT.OPTION)
    _option(attr, "газова", "hazova")

    formset = _post(product, [{"attribute": str(attr.pk), "value": "газва", "sort_order": "0"}])

    assert not formset.is_valid()
    assert "газва" in str(formset.errors)
    assert attr.options.count() == 1


def test_editing_switches_column(product: Product, group: AttributeGroup) -> None:
    """Було число — стало порожньо: старе значення мусить піти з колонки, а не залишитись."""
    attr = _attr(group, "temp", "Максимальна температура", VT.NUMBER)
    pav = ProductAttributeValue.objects.create(
        product=product, attribute=attr, value_number=Decimal("240")
    )

    formset = _post(
        product,
        [{"id": str(pav.pk), "attribute": str(attr.pk), "value": "", "sort_order": "0"}],
    )
    assert formset.is_valid(), formset.errors
    formset.save()

    pav.refresh_from_db()
    assert pav.value_number is None


# ---------------------------------------------------------------------------
# 4. 🔴 Найдорожче: рядок, якого не чіпали, не переписується
# ---------------------------------------------------------------------------
def test_untouched_row_survives_value_type_change(product: Product, group: AttributeGroup) -> None:
    """Синк уточнив тип із STRING на NUMBER, а значення лишилось у `value_string`.

    Якщо форма пише в кожен рядок безумовно, то перше ж «Зберегти» на будь-якому іншому
    рядку витерло б це значення — мовчки і на всіх таких товарах одразу.
    """
    attr = _attr(group, "power", "Потужність", VT.STRING)
    pav = ProductAttributeValue.objects.create(
        product=product, attribute=attr, value_string="2.4 кВт"
    )
    pav.value_string_uk = "2.4 кВт"
    pav.save(update_fields=["value_string_uk"])
    Attribute.objects.filter(pk=attr.pk).update(value_type=VT.NUMBER)

    # Рядок показано (поле — числове, порожнє) і надіслано БЕЗ правок.
    formset = _post(
        product,
        [{"id": str(pav.pk), "attribute": str(attr.pk), "value": "", "sort_order": "0"}],
    )
    assert formset.is_valid(), formset.errors
    formset.save()

    pav.refresh_from_db()
    assert pav.value_string == "2.4 кВт"
