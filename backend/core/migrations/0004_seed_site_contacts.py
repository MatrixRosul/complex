"""Контакти магазину переїжджають із коду фронта в адмінку.

`GET /cms/contacts` не існував, тому телефони, e-mail і адреса, які показує шапка й підвал,
жили ЗАХАРДКОДЖЕНИМИ у `frontend/src/lib/site.ts::FALLBACK_CONTACTS`. Поля в `SiteSettings`
при цьому були — просто порожні й нікому не потрібні: заповнення їх в адмінці не міняло на
сайті нічого.

Тепер ендпоінт є. Ця міграція переносить у БД РІВНО ті значення, які сайт показує сьогодні,
щоб увімкнення ендпоінта нічого не змінило візуально — і водночас зробило їх редагованими.

⚠️ НЕ перезаписує вже заповнені поля: якщо контакти в адмінці хтось уже вніс, вони виграють.
"""

from __future__ import annotations

from django.core.cache import cache
from django.db import migrations

# Джерело — frontend/src/lib/site.ts (реальні контакти замовника, які зараз стоять на сайті).
PHONES = ["+380950915222", "+380991717925", "+380507388811"]
EMAIL = "uzh.tehnika77@gmail.com"
ADDRESS_UK = "м. Ужгород"
ADDRESS_RU = "г. Ужгород"


def seed_contacts(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    # Синглтон створюється лінивим get_or_create у `get_solo()`, тож на чистій БД його
    # тут може ще не бути — саме тому get_or_create, а не get.
    settings, _ = SiteSettings.objects.get_or_create(pk=1)

    if not settings.phones:
        settings.phones = PHONES
    if not settings.email:
        settings.email = EMAIL
    if not settings.address_uk:
        # `address` і `address_uk` — та сама колонка з погляду modeltranslation, але в
        # історичній моделі вони окремі поля, тож пишемо обидва явно.
        settings.address_uk = ADDRESS_UK
        settings.address = ADDRESS_UK
    if not settings.address_ru:
        settings.address_ru = ADDRESS_RU

    settings.save()

    # 🔴 ОБОВ'ЯЗКОВО. `SiteSettings.get_solo()` кешує синглтон у Redis на годину, а
    #    інвалідацію робить кастомний `save()` — якого в ІСТОРИЧНОЇ моделі міграції немає.
    #    Без цього рядка після деплою ендпоінт ще годину віддавав би порожні контакти,
    #    хоча в БД вони вже лежать. Ключ — літералом: `SiteSettings.CACHE_KEY` брати з
    #    живої моделі всередині міграції не можна.
    cache.delete("core:sitesettings")


def noop(apps, schema_editor):
    """Назад не відкочуємо: контакти могли відредагувати в адмінці після міграції."""


class Migration(migrations.Migration):
    dependencies = [("core", "0003_rename_pg_objects_to_complex")]

    operations = [migrations.RunPython(seed_contacts, noop)]
