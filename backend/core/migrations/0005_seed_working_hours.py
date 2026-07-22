"""Графік роботи — у БД, а не лише у фолбеку фронта.

Продовження `0004_seed_site_contacts`. Там перенесено телефони/e-mail/адресу, але графік
лежить в ОКРЕМІЙ таблиці (`WorkingHours`), і на локальній базі він уже був заповнений —
тому проблему стало видно тільки на проді: `GET /cms/contacts` почав віддавати
`working_hours: []`, і години зникли з верхньої смуги. До появи ендпоінта їх малював
фолбек `frontend/src/lib/site.ts`, тобто зникнення — прямий наслідок увімкнення ендпоінта.

Значення — INPUTS §4 (ПН–ПТ 09:00–20:00, СБ–НД 10:00–17:00), ті самі, що фолбек показує
сьогодні. Тобто після міграції на сайті нічого не зміниться — але графік стане редагованим
в адмінці (Налаштування → Графік роботи).

⚠️ Заповнює, ЛИШЕ якщо таблиця порожня цілком. Частково заведений графік — це рішення
   людини (напр. неділя зроблена вихідним), і доливати в нього «дефолт» не можна.
"""

from __future__ import annotations

import datetime

from django.db import migrations

WEEKDAY_HOURS: dict[int, tuple[datetime.time, datetime.time]] = {
    0: (datetime.time(9, 0), datetime.time(20, 0)),  # ПН
    1: (datetime.time(9, 0), datetime.time(20, 0)),
    2: (datetime.time(9, 0), datetime.time(20, 0)),
    3: (datetime.time(9, 0), datetime.time(20, 0)),
    4: (datetime.time(9, 0), datetime.time(20, 0)),  # ПТ
    5: (datetime.time(10, 0), datetime.time(17, 0)),  # СБ
    6: (datetime.time(10, 0), datetime.time(17, 0)),  # НД
}


def seed_hours(apps, schema_editor):
    WorkingHours = apps.get_model("core", "WorkingHours")
    if WorkingHours.objects.exists():
        return

    WorkingHours.objects.bulk_create(
        [
            WorkingHours(weekday=day, is_day_off=False, open_time=opens, close_time=closes)
            for day, (opens, closes) in WEEKDAY_HOURS.items()
        ]
    )


def noop(apps, schema_editor):
    """Назад не відкочуємо: графік могли відредагувати в адмінці після міграції."""


class Migration(migrations.Migration):
    dependencies = [("core", "0004_seed_site_contacts")]

    operations = [migrations.RunPython(seed_hours, noop)]
