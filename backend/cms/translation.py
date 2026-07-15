"""
CMS — реєстрація перекладних полів (django-modeltranslation).

Кожне поле тут → ДВІ фізичні колонки: `field_uk` / `field_ru` (DATA_MODEL §0).
Мови: `uk` (дефолт) + `ru`. RU — В MVP, не «на майбутнє» (INPUTS §1).
Порожній `_ru` віддає `_uk` (MODELTRANSLATION_FALLBACK_LANGUAGES = ("uk",)) — сторінка
ніколи не порожня.

⚠️ Хто пише в `_ru`: НІХТО вручну масово. Пише `translation.TranslationEntry.approve()` —
   черга машинного перекладу з ручним схваленням. `_ru`-колонка — це ВІТРИНА,
   `TranslationEntry` — джерело правди для черги, аудиту й витрат (docs/research/TRANSLATION.md).

⚠️ MODELTRANSLATION_AUTO_POPULATE = False (base.py): при створенні об'єкта `_ru` НЕ копіює `_uk`.
   Це навмисно — інакше неможливо відрізнити «перекладено» від «продубльовано українською»,
   і черга перекладу не побачить роботи.

🔴 НЕ ДОДАВАТИ сюди `StaticPage.key`, `MenuItem.url`, `EditorImage.alt`:
   • `key` — ключ маршрутизації: перекладеш → URL сторінки зміниться при перемиканні мови
     і зламаються зовнішні посилання та індексація;
   • `url` — шлях, а не текст;
   • `alt` осідає всередині HTML-опису, а опис уже має версію на кожну мову.

🔴 Індекси й unique по мовних колонках (`slug_uk`) — НЕ в `Meta`, а окремою міграцією ПІСЛЯ
   initial (DATA_MODEL §0). Причина: modeltranslation додає колонки в `AppConfig.ready()`,
   і посилання на них у `Meta` робить `makemigrations` залежним від порядку ініціалізації.
   Див. шапку `cms/models.py`.
"""

from __future__ import annotations

from modeltranslation.translator import TranslationOptions, register

from cms.models import Banner, MenuItem, NewsPost, PickupPoint, StaticPage

# SEO-поля з core.SEOMixin — перекладні для КОЖНОЇ конкретної моделі, яка його успадковує
# (міксин абстрактний, тому окремо зареєструвати його не можна).
SEO_FIELDS = ("seo_title", "seo_description", "og_image")


@register(Banner)
class BannerTranslationOptions(TranslationOptions):
    # ⚡ `image` / `image_mobile` — теж перекладні: у банерах текст запечений у картинку,
    #    тому RU-версія слайдера — це ІНШИЙ ФАЙЛ, а не інший підпис.
    fields = ("title", "subtitle", "image", "image_mobile")


@register(NewsPost)
class NewsPostTranslationOptions(TranslationOptions):
    fields = ("title", "slug", "excerpt", "body", *SEO_FIELDS)


@register(StaticPage)
class StaticPageTranslationOptions(TranslationOptions):
    fields = ("title", "body", *SEO_FIELDS)


@register(MenuItem)
class MenuItemTranslationOptions(TranslationOptions):
    fields = ("title",)


@register(PickupPoint)
class PickupPointTranslationOptions(TranslationOptions):
    fields = ("name", "address", "working_hours")
