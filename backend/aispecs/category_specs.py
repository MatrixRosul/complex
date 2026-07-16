"""Шаблони очікуваних характеристик по КАТЕГОРІЯХ.

Тут живе те, що просив замовник: «стандартний перелік характеристик, по якому потім
порівнюються моделі». Це і є та уніфікація, від якої залежить порівняння й фільтрація —
кожна посудомийка мапиться в ТІ САМІ коди характеристик, тому «економія води» в двох
моделях зводиться до одного `Attribute.code`, а не до двох різних фільтрів.

Свідомо в коді, а не в БД (як `Attribute.role`): це доменний словник, який змінюється
рідко й ревʼюється в рівʼю коду. `apply` (aispecs/services.py) створює за ним `Attribute`,
`AttributeGroup`, `Unit` за потреби — усе з `needs_review=True`, `is_filterable=False`.

Ключі МУСЯТЬ збігатися з ключами, які повертає збирач (harvest_*.js, поле spec.key).
"""

from __future__ import annotations

# Групи-підзаголовки в картці: code -> (назва, порядок)
GROUPS: dict[str, tuple[str, int]] = {
    "main": ("Основні", 10),
    "dimensions": ("Габаритні розміри", 20),
    "efficiency": ("Енергія та шум", 30),
    "programs": ("Програми", 40),
    "features": ("Функції", 50),
}

# Одиниці виміру: code -> (назва, синоніми)
UNITS: dict[str, tuple[str, list[str]]] = {
    "cm": ("см", ["см", "cm"]),
    "set": ("компл.", ["компл.", "компл", "комплектів", "комплекти"]),
    "l_cycle": ("л/цикл", ["л/цикл", "л"]),
    "kwh100": ("кВт·год/100", ["кВт·год/100 циклів", "кВт·год/100", "кВт·год"]),
    "db": ("дБ", ["дБ", "db", "дб"]),
}

# key -> (назва укр, група, тип значення, код одиниці|None)
#   тип: "number" | "option" | "string" | "bool"
DISHWASHER: dict[str, tuple[str, str, str, str | None]] = {
    "installation_type": ("Тип встановлення", "main", "option", None),
    "color_panel": ("Колір панелі", "main", "option", None),
    "place_settings": ("Кількість комплектів посуду", "main", "number", "set"),
    "width_cm": ("Ширина", "dimensions", "number", "cm"),
    "dimensions_hwd_cm": ("Розміри (В×Ш×Г)", "dimensions", "string", None),
    "energy_class": ("Клас енергоефективності", "efficiency", "option", None),
    "water_l": ("Витрата води", "efficiency", "number", "l_cycle"),
    "energy_kwh_100": ("Енергоспоживання", "efficiency", "number", "kwh100"),
    "noise_db": ("Рівень шуму", "efficiency", "number", "db"),
    "noise_class": ("Клас шуму", "efficiency", "option", None),
    "programs_count": ("Кількість програм", "programs", "number", None),
    "programs_list": ("Перелік програм", "programs", "string", None),
    "drying_type": ("Тип сушіння", "features", "option", None),
    "third_rack": ("Третій лоток", "features", "string", None),
    "adjustable_basket": ("Регульований верхній кошик", "features", "string", None),
    "leak_protection": ("Захист від протікання", "features", "string", None),
    "delay_start": ("Відкладений старт", "features", "string", None),
    "half_load": ("Половинне завантаження", "features", "string", None),
    "connectivity": ("Wi-Fi / Smart", "features", "string", None),
    "inverter_motor": ("Інверторний мотор", "features", "string", None),
    "display": ("Дисплей / індикація", "features", "string", None),
    "beam_on_floor": ("Промінь на підлозі", "features", "string", None),
}

TEMPLATES: dict[str, dict[str, tuple[str, str, str, str | None]]] = {
    "dishwasher": DISHWASHER,
}


def get_template(category_key: str) -> dict[str, tuple[str, str, str, str | None]]:
    """Шаблон характеристик для категорії; порожній dict — якщо категорії ще немає."""
    return TEMPLATES.get(category_key, {})
