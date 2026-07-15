"""
Конфіг листів під РЕАЛЬНУ структуру таблиць замовника (звірено 14.07.2026, OAuth-доступ).

До цієї міграції геометрію було реконструйовано ЗІ СКРІНШОТІВ, і вона не збігалась з
дійсністю ЖОДНИМ рядком:

    БУЛО (зі скрінів)                       СТАЛО (реальність)
    заголовки в рядку 3                     заголовки в рядку 1
    рядок 4: A4 = дата, E4 = курс USD       рядок 2: A2 = дата (Excel serial 46211)
    дані з рядка 5                          рядок 3 = секція, дані з рядка 4
    курс USD читається з таблиці            КУРСУ В ТАБЛИЦІ НЕМАЄ (він був в іншому файлі)
    spreadsheet_id = файл характеристик     прайс — ОКРЕМИЙ файл «Прайс (Сайт)»
    характеристики: лист «Гривнева» (300)   лист «Основна» (1580 рядків — повніший)

⚠️ ЧОМУ МІГРАЦІЯ, А НЕ ХАРДКОД: геометрія й карта колонок — це ДАНІ (їх правлять в адмінці,
коли замовник посуне колонку), а не код. Міграція лише виставляє правильний СТАРТОВИЙ стан.

⚠️ Чіпаємо ТІЛЬКИ власне джерело Complex (is_primary=True). Прайси сторонніх постачальників
мають свою геометрію, і затирати її цією міграцією не можна.
"""

from django.db import migrations

PRICE_SPREADSHEET_ID = "12xmgixwelF621CPRhd9gqxNRrIW1RF-vLfHsAA4Dohc"  # «Прайс (Сайт)»
SPEC_SPREADSHEET_ID = "1qgMFRcTcH2Jt1q1kICmOYVJQiXSPzZpCVdP8UogqjDY"  # «База даних (Актуальна)»

#: Робочі листи прайсу. «Лист4» порожній, «USD (копия)» / «USD (копия) 1» — КОПІЇ:
#: у конфіг вони не потрапляють, тому синк їх фізично не читає.
PRICE_TABS = {"UAH": "UAH", "USD": "USD"}

#: Один column_map на ОБИДВА листи: заголовки в них РІЗНІ («Бренд» vs «Виробник»,
#: «Країна виробництва» vs «Країна_виробник»), тому колонки оголошені СИНОНІМАМИ.
#: Плюс поверх цього працює вбудований sheets.COLUMN_SYNONYMS.
PRICE_COLUMN_MAP = {
    "name": "Найменування",
    "price": "Ціна",
    "qty": "К-сть",
    "currency": "Валюта",
    "category": "Категорія",
    "brand": ["Бренд", "Виробник"],
    "country": ["Країна виробництва", "Країна_виробник"],
    "photo": "Фото",
    "sku": "Артикул",
}

#: «Основна»: A..I фіксовані, з колонки J — трійки характеристик.
#: ⚠️ Колонка H (8-ма) містить БРЕНД, але її заголовок — сміття («2401579», чийсь артикул,
#:    що заїхав у шапку). Шукати за іменем нічого; оголошуємо ПОЗИЦІЙНО.
SPEC_COLUMN_MAP = {
    "name": "Найменування",
    "country": "Країна виробництва",
    "package_dims": "Розміри в упакуванні (см)",
    "image": "Зображення",
    "sku": "Артикул",
    "brand": "#8",
    "category": "Категорія",
    "spec_triplet_start": "Назва_Характеристики",
}


def apply_real_geometry(apps, schema_editor):
    PriceSource = apps.get_model("sync", "PriceSource")
    PriceSheet = apps.get_model("sync", "PriceSheet")
    SpecSheet = apps.get_model("sync", "SpecSheet")

    for source in PriceSource.objects.filter(is_primary=True):
        source.spreadsheet_id = PRICE_SPREADSHEET_ID
        source.save(update_fields=["spreadsheet_id"])

        for tab, currency in PRICE_TABS.items():
            PriceSheet.objects.update_or_create(
                price_source=source,
                tab_name=tab,
                defaults={
                    "currency": currency,
                    "column_map": PRICE_COLUMN_MAP,
                    "header_row": 1,
                    "data_start_row": 4,
                    "rate_cell": "",  # курсу в прайсі немає — беремо з SiteSettings
                    "date_cell": "A2",
                    "is_active": True,
                    "sort_order": 0 if tab == "UAH" else 1,
                },
            )

        # Листи, яких у прайсі більше немає (або яких там ніколи не було) — вимикаємо,
        # а не видаляємо: на них можуть посилатись оффери, і історія має лишитись.
        PriceSheet.objects.filter(price_source=source).exclude(tab_name__in=PRICE_TABS).update(
            is_active=False
        )

        # Характеристики: «Основна» замість «Гривневої» (1580 рядків проти 300).
        spec, _ = SpecSheet.objects.update_or_create(
            price_source=source,
            spreadsheet_id=SPEC_SPREADSHEET_ID,
            tab_name="Основна",
            defaults={
                "column_map": SPEC_COLUMN_MAP,
                "header_row": 1,
                "data_start_row": 2,
                "spec_triplet_start_col": 10,
                "is_active": True,
            },
        )
        SpecSheet.objects.filter(price_source=source).exclude(pk=spec.pk).update(is_active=False)


def noop(apps, schema_editor):
    """Назад дороги немає й не треба: стара геометрія була просто НЕПРАВИЛЬНОЮ."""


class Migration(migrations.Migration):
    dependencies = [("sync", "0003_supplieroffer_sku_is_surrogate_and_more")]

    operations = [migrations.RunPython(apply_real_geometry, noop)]
