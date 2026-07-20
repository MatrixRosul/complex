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
    "mm": ("мм", ["мм", "mm"]),
    "set": ("компл.", ["компл.", "компл", "комплектів", "комплекти"]),
    "l": ("л", ["л", "l", "літрів"]),
    "l_cycle": ("л/цикл", ["л/цикл", "л"]),
    "kwh100": ("кВт·год/100", ["кВт·год/100 циклів", "кВт·год/100", "кВт·год"]),
    "kwh": ("кВт·год", ["кВт·год", "kWh"]),
    "db": ("дБ", ["дБ", "db", "дб"]),
    "w": ("Вт", ["Вт", "W", "вт", "ват"]),
    "celsius": ("°C", ["°C", "°с", "градусів"]),
    "pcs": ("шт", ["шт", "шт.", "штук"]),
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

# Духові шафи / пароварки
OVEN: dict[str, tuple[str, str, str, str | None]] = {
    "oven_type": ("Тип", "main", "option", None),
    "volume_l": ("Обʼєм", "main", "number", "l"),
    "cleaning_type": ("Тип очищення", "features", "option", None),
    "energy_class": ("Клас енергоефективності", "efficiency", "option", None),
    "heating_modes": ("Кількість режимів нагріву", "features", "number", None),
    "max_temp": ("Максимальна температура", "main", "number", "celsius"),
    "control_type": ("Тип керування", "main", "option", None),
    "convection": ("Конвекція", "features", "string", None),
    "grill": ("Гриль", "features", "string", None),
    "steam_function": ("Функція пари", "features", "string", None),
    "telescopic_rails": ("Телескопічні напрямні", "features", "string", None),
    "meat_probe": ("Термощуп", "features", "string", None),
    "display": ("Дисплей", "features", "string", None),
    "timer": ("Таймер / годинник", "features", "string", None),
    "connectivity": ("Wi-Fi / Smart", "features", "string", None),
    "door_cooling": ("Охолодження / тип дверцят", "features", "string", None),
    "auto_programs": ("Автоматичні програми", "features", "string", None),
    "power_w": ("Потужність", "efficiency", "number", "w"),
    "width_cm": ("Ширина", "dimensions", "number", "cm"),
    "dimensions_hwd_cm": ("Розміри (В×Ш×Г)", "dimensions", "string", None),
    "niche_size": ("Розміри ніші для встановлення", "dimensions", "string", None),
    "color": ("Колір", "main", "option", None),
}

# Варильні поверхні (газові / індукційні / електричні — одна сітка)
HOB: dict[str, tuple[str, str, str, str | None]] = {
    "hob_type": ("Тип", "main", "option", None),
    "burners": ("Кількість конфорок / зон", "main", "number", "pcs"),
    "surface_material": ("Матеріал поверхні", "main", "option", None),
    "control_type": ("Тип керування", "main", "option", None),
    "installation_type": ("Тип встановлення", "main", "option", None),
    "ignition": ("Автопідпал", "features", "string", None),
    "gas_control": ("Газ-контроль (газова безпека)", "features", "string", None),
    "residual_heat": ("Індикатор залишкового тепла", "features", "string", None),
    "booster": ("Функція Booster", "features", "string", None),
    "flex_zone": ("Flex-зона / обʼєднання зон", "features", "string", None),
    "timer": ("Таймер", "features", "string", None),
    "auto_off": ("Автовимкнення", "features", "string", None),
    "safety_lock": ("Захист від дітей", "features", "string", None),
    "power_w": ("Загальна потужність", "efficiency", "number", "w"),
    "width_cm": ("Ширина", "dimensions", "number", "cm"),
    "dimensions_hwd_cm": ("Розміри (В×Ш×Г)", "dimensions", "string", None),
    "niche_size": ("Розміри ніші для встановлення", "dimensions", "string", None),
    "color": ("Колір", "main", "option", None),
}

TEMPLATES: dict[str, dict[str, tuple[str, str, str, str | None]]] = {
    "dishwasher": DISHWASHER,
    "oven": OVEN,
    "hob": HOB,
}


def get_template(category_key: str) -> dict[str, tuple[str, str, str, str | None]]:
    """Шаблон характеристик для категорії; порожній dict — якщо категорії ще немає."""
    return TEMPLATES.get(category_key, {})


# Відповідність назв характеристик ek.ua → ключі нашої сітки (нормалізовано: lower, один пробіл).
# Те, чого тут немає, лягає як generic-характеристика за назвою (name_uk).
EK_ALIASES: dict[str, dict[str, str]] = {
    "dishwasher": {
        "тип установки": "installation_type",
        "кількість комплектів посуду": "place_settings",
        "витрата води": "water_l",
        "сушіння": "drying_type",
        "відсік для приладів": "third_rack",
        "інверторний двигун": "inverter_motor",
        "клас енергоспоживання (new)": "energy_class",
        "клас енергоспоживання": "energy_class",
        "рівень шуму": "noise_db",
        "клас гучності": "noise_class",
        "кількість програм": "programs_count",
        "захист від протікання": "leak_protection",
        "таймер відстрочки запуску": "delay_start",
        "проекція роботи на підлозі": "beam_on_floor",
        "регулювання верхнього кошика": "adjustable_basket",
        "управління зі смартфона (wi-fi)": "connectivity",
        "дисплей": "display",
        "габарити (вхшхг)": "dimensions_hwd_cm",
        "напівзавантаження": "half_load",
        "колір": "color_panel",
    },
    "oven": {
        "тип": "oven_type",
        "об'єм": "volume_l",
        "обʼєм": "volume_l",
        "очищення": "cleaning_type",
        "тип очищення": "cleaning_type",
        "клас енергоспоживання (new)": "energy_class",
        "клас енергоспоживання": "energy_class",
        "конвекція": "convection",
        "гриль": "grill",
        "телескопічні напрямні": "telescopic_rails",
        "термощуп": "meat_probe",
        "дисплей": "display",
        "таймер": "timer",
        "управління зі смартфона (wi-fi)": "connectivity",
        "габарити (вхшхг)": "dimensions_hwd_cm",
        "колір": "color",
        "управління": "control_type",
        "макс. температура": "max_temp",
    },
    "hob": {
        "тип": "hob_type",
        "кількість конфорок": "burners",
        "матеріал поверхні": "surface_material",
        "управління": "control_type",
        "газ-контроль": "gas_control",
        "автопідпал": "ignition",
        "індикатор залишкового тепла": "residual_heat",
        "booster": "booster",
        "таймер": "timer",
        "захист від дітей": "safety_lock",
        "габарити (вхшхг)": "dimensions_hwd_cm",
        "колір": "color",
    },
}


def ek_aliases(category_key: str) -> dict[str, str]:
    return EK_ALIASES.get(category_key, {})


def template_fields(category_key: str) -> list[dict]:
    """Сітку категорії у вигляді для збирача: [{key, label, unit, type, group}].

    Це те, що передається агентам — щоб КОЖЕН заповнював рівно ці рядки (одна структура
    з будь-якого джерела). Джерело істини — той самий шаблон, що застосовує apply.
    """
    out = []
    for key, (name, group_code, value_type, unit_code) in get_template(category_key).items():
        if key.endswith("_list"):  # службові (перелік програм) — не в сітці
            continue
        out.append(
            {
                "key": key,
                "label": name,
                "unit": UNITS.get(unit_code, ("", []))[0] if unit_code else "",
                "type": value_type,
                "group": GROUPS.get(group_code, (group_code, 0))[0],
            }
        )
    return out
