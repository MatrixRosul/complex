"""Перекладні поля core (django-modeltranslation).

Кожне поле тут → дві фізичні колонки `<field>_uk` / `<field>_ru`.
Дефолт — uk, фолбек ru→uk (MODELTRANSLATION_FALLBACK_LANGUAGES), тому порожній
переклад ніколи не показує користувачу порожнечу.
"""

from __future__ import annotations

from modeltranslation.translator import TranslationOptions, register

from core.models import SiteSettings


@register(SiteSettings)
class SiteSettingsTranslationOptions(TranslationOptions):
    fields = (
        "address",
        "courier_uzhhorod_price_note",
        "installment_badge_text",
        "default_seo_title",
        "default_seo_description",
    )
    # default_og_image НЕ перекладний: це один фолбек-банер магазину, а не контент сторінки.
    # Перекладний og_image живе в SEOMixin (Category / Product / NewsPost / StaticPage).
