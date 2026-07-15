"""Перекладні поля каталогу (django-modeltranslation).

Кожне поле тут → дві фізичні колонки `<field>_uk` / `<field>_ru` у тій самій таблиці.
Дефолт — uk; MODELTRANSLATION_FALLBACK_LANGUAGES=("uk",) → порожній `_ru` віддає `_uk`,
тобто сторінка ніколи не порожня.

⚠️ ЩО САМЕ ПЕРЕКЛАДАЄМО — СЛОВНИК, А НЕ ТОВАРИ (TRANSLATION.md §1):
   Attribute.name (~600), AttributeOption.value (~6000), Unit.name (~25) перекладаються
   РІВНО ОДИН РАЗ і застосовуються до всіх 10 000 товарів. Саме тому характеристики
   зобов'язані бути нормалізованими (Attribute/AttributeOption/Unit), а не рядками в JSON.

⚠️ ЧОГО ТУТ СВІДОМО НЕМАЄ:
   • Product.filter_tokens, Product.specs_num — токени й числа, а не текст. Токен будується
     зі slug (який НЕ перекладний), тому перемикання uk↔ru не змінює результат фільтра і не
     подвоює фасети. Мовним є лише ПІДПИС фасета (Attribute.name_ru, AttributeOption.value_ru).
   • Product.sku / mpn / name_normalized — ключі синхронізації.
   • Brand.name — власна назва («Gorenje» не перекладається).
   • *.slug у AttributeOption / Brand / Country — вічні ключі токенів і URL фільтрів.
   • *.code, *.aliases — службові.

⚠️ ЩО ТУТ Є І ЗДАЄТЬСЯ ДИВНИМ, АЛЕ ОБОВ'ЯЗКОВЕ:
   • Product.specs_json  → specs_json_uk / specs_json_ru. Без цього російська картка показує
     УКРАЇНСЬКІ характеристики.
   • Product.search_vector → search_vector_uk / search_vector_ru (конфіги `uk` і `ru_complex`).
     Без цього російський пошук не знаходить нічого.
   • Category.slug / Product.slug — декоративні (роут іде по external_id / id), але мовні:
     /ua/p/123/xolodylnyk vs /ru/p/123/holodilnik.
"""

from __future__ import annotations

from modeltranslation.translator import TranslationOptions, register

from catalog.models import (
    Attribute,
    AttributeGroup,
    AttributeOption,
    Category,
    Country,
    Product,
    ProductAttributeValue,
    ProductImage,
    RelatedGroup,
    RelatedGroupItem,
    Unit,
    VariantGroup,
    VariantItem,
)

# SEO-поля з core.SEOMixin — перекладні на кожному нащадку, який їх має.
SEO_FIELDS = ("seo_title", "seo_description", "og_image")


@register(Category)
class CategoryTranslationOptions(TranslationOptions):
    fields = ("name", "slug", "description", *SEO_FIELDS)
    # external_id, path, virtual_tokens, hotline_category (FK) — НЕ перекладні:
    # це ключі мапінгу з прайсу і токени, а не контент.


@register(Country)
class CountryTranslationOptions(TranslationOptions):
    fields = ("name",)
    # code / slug / aliases — ключі. Токен "country:polshcha" однаковий в обох мовах.


@register(Product)
class ProductTranslationOptions(TranslationOptions):
    fields = (
        "name",
        "slug",
        "description",
        "short_description",
        "condition_note",
        # ДЕНОРМАЛІЗОВАНІ ПРОЄКЦІЇ — теж перекладні (див. шапку модуля):
        "specs_json",
        "search_vector",
        *SEO_FIELDS,
    )


@register(ProductImage)
class ProductImageTranslationOptions(TranslationOptions):
    fields = ("alt",)


@register(AttributeGroup)
class AttributeGroupTranslationOptions(TranslationOptions):
    fields = ("name",)  # «Основні» / «Основные»


@register(Unit)
class UnitTranslationOptions(TranslationOptions):
    fields = ("name",)  # «міс» / «мес», «об/хв» / «об/мин» — ~25 рядків на весь каталог


@register(Attribute)
class AttributeTranslationOptions(TranslationOptions):
    fields = ("name",)  # БЕЗ одиниці: name="Висота", unit=FK(Unit «мм»)


@register(AttributeOption)
class AttributeOptionTranslationOptions(TranslationOptions):
    fields = ("value",)  # slug НЕ перекладається — він і є ключем токена


@register(ProductAttributeValue)
class ProductAttributeValueTranslationOptions(TranslationOptions):
    fields = ("value_string",)
    # value_number / value_bool / raw_value — не текст. raw_value — сирий аудит з прайсу.


@register(VariantGroup)
class VariantGroupTranslationOptions(TranslationOptions):
    fields = ("name",)


@register(VariantItem)
class VariantItemTranslationOptions(TranslationOptions):
    fields = ("label",)  # '50" (127 см)' / «Чорний» → «Черный»


@register(RelatedGroup)
class RelatedGroupTranslationOptions(TranslationOptions):
    fields = ("name",)


@register(RelatedGroupItem)
class RelatedGroupItemTranslationOptions(TranslationOptions):
    fields = ("role",)  # «Духова шафа» / «Духовой шкаф»
