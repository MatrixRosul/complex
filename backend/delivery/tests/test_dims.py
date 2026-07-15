"""
Габарити й об'ємна вага — найдорожча пастка проєкту (INTEGRATIONS §1.7).

Числа тут НЕ вигадані: 171,5 × 56 × 35 см / 61,5 кг — реальний холодильник зі скріна
таблиці характеристик; 50×50×60 / 2 кг / 0.15 м³ / 37.5 кг — живий замір API НП.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from delivery.models import DimsSource
from delivery.services.dims import cart_dims, effective_dims

pytestmark = pytest.mark.django_db


def test_volumetric_weight_is_volume_times_250(np_box):
    """0.15 м³ × 250 кг/м³ = 37.5 кг. Це число НП і бере за основу ціни."""
    d = effective_dims(np_box)

    assert d.volume_m3 == Decimal("0.15000")
    assert d.volumetric_weight_kg == Decimal("37.500")


def test_chargeable_weight_is_max_of_actual_and_volumetric(np_box):
    """🔴 СЕРЦЕ ПРОБЛЕМИ: фактична вага 2 кг, платимо за 37.5 кг.

    Калькулятор, який візьме 2 кг, порахує 97.5 грн замість 539.5 — заниження в 5.5×
    на КОЖНОМУ замовленні.
    """
    d = effective_dims(np_box)

    assert d.weight_kg == Decimal("2")
    assert d.volumetric_weight_kg == Decimal("37.500")
    assert d.chargeable_weight_kg == Decimal("37.500")  # НЕ 2!


def test_fridge_is_billed_by_volume_not_weight(fridge):
    """Холодильник: 61,5 кг фактичних, але об'ємних — 84 кг. Платимо за об'єм."""
    d = effective_dims(fridge)

    assert d.volume_m3 == Decimal("0.33614")  # 171.5 × 56 × 35 / 1e6
    assert d.volumetric_weight_kg == Decimal("84.035")
    assert d.chargeable_weight_kg == Decimal("84.035")
    assert d.chargeable_weight_kg > d.weight_kg


def test_options_seat_is_always_built(fridge):
    """OptionsSeat шлеться ЗАВЖДИ — без нього НП рахує лише за фактичною вагою."""
    seat = effective_dims(fridge).as_options_seat()

    assert seat == {
        "weight": "61.5",
        "volumetricWidth": "56",
        "volumetricHeight": "171.5",
        "volumetricLength": "35",
    }


# --- ланцюг фолбеків -------------------------------------------------------


def test_source_is_spec_when_dims_come_from_characteristics(fridge):
    assert effective_dims(fridge).source == DimsSource.SPEC


def test_falls_back_to_category_defaults(fridge, category):
    """Немає габаритів у товара → дефолти категорії, а НЕ нулі."""
    fridge.package_height_cm = None
    fridge.package_width_cm = None
    fridge.package_depth_cm = None
    fridge.package_weight_kg = None
    fridge.save()

    category.default_height_cm = Decimal("175")
    category.default_width_cm = Decimal("60")
    category.default_depth_cm = Decimal("70")
    category.default_weight_kg = Decimal("70")
    category.save()
    fridge.refresh_from_db()

    d = effective_dims(fridge)

    assert (d.height_cm, d.width_cm, d.depth_cm) == (
        Decimal("175"),
        Decimal("60"),
        Decimal("70"),
    )
    assert d.weight_kg == Decimal("70")
    assert d.source == DimsSource.CATEGORY


def test_falls_back_to_global_defaults(fridge, site_settings):
    """Немає ні в товара, ні в категорії → глобальний дефолт. НІКОЛИ не нулі."""
    fridge.package_height_cm = None
    fridge.package_width_cm = None
    fridge.package_depth_cm = None
    fridge.package_weight_kg = None
    fridge.save()

    d = effective_dims(fridge)

    assert (d.height_cm, d.width_cm, d.depth_cm) == (
        Decimal("40"),
        Decimal("40"),
        Decimal("40"),
    )
    assert d.weight_kg == Decimal("5")
    assert d.source == DimsSource.DEFAULT
    # Навіть на дефолтах об'єм НЕ нульовий → OptionsSeat є що надіслати.
    assert d.volume_m3 > 0


def test_source_is_the_weakest_link(fridge, category):
    """Вага з характеристик, габарити — з дефолтів категорії → source = category.

    Метрика покриття має показувати НАЙГІРШУ правду, інакше «80% товарів на spec»
    приховає те, що габаритів у них насправді немає.
    """
    fridge.package_height_cm = None
    fridge.package_width_cm = None
    fridge.package_depth_cm = None
    fridge.save()  # вага (61.5, spec) лишилась

    category.default_height_cm = Decimal("175")
    category.default_width_cm = Decimal("60")
    category.default_depth_cm = Decimal("70")
    category.save()
    fridge.refresh_from_db()

    d = effective_dims(fridge)

    assert d.weight_kg == Decimal("61.5")  # з характеристик
    assert d.source == DimsSource.CATEGORY  # але джерело — найслабше


def test_partial_dims_do_not_leak(fridge, category):
    """Два виміри з трьох — це НЕ габарити. Падаємо на дефолт категорії цілком."""
    fridge.package_depth_cm = None
    fridge.save()

    category.default_height_cm = Decimal("175")
    category.default_width_cm = Decimal("60")
    category.default_depth_cm = Decimal("70")
    category.save()
    fridge.refresh_from_db()

    d = effective_dims(fridge)

    assert d.height_cm == Decimal("175")  # НЕ 171.5 — беремо трійку цілком
    assert d.source == DimsSource.CATEGORY


# --- кошик -----------------------------------------------------------------


def test_cart_makes_one_seat_per_unit(fridge, small_item):
    """Два холодильники — ДВА місця, а не одна коробка подвоєної висоти."""
    c = cart_dims([(fridge, 2), (small_item, 1)])

    assert c.seats_amount == 3
    assert c.weight_kg == Decimal("124.500")  # 61.5×2 + 1.5
    assert c.volume_m3 == Decimal("0.68878")  # 0.33614×2 + 0.0165
    # Об'ємна: 84.035×2 + max(1.5, 4.125) = 168.07 + 4.125
    assert c.chargeable_weight_kg == Decimal("172.195")
    assert len(c.as_options_seat()) == 3


def test_cart_source_is_the_weakest_item(fridge, small_item):
    """Один товар без габаритів псує метрику всього кошика — так і має бути."""
    small_item.package_height_cm = None
    small_item.package_width_cm = None
    small_item.package_depth_cm = None
    small_item.package_weight_kg = None
    small_item.save()

    assert cart_dims([(fridge, 1), (small_item, 1)]).source == DimsSource.DEFAULT
