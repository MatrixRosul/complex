"""
Контент — адмінка.

Дві речі, які тут легко зробити неправильно:

  1. RICH-РЕДАКТОР. `NewsPost.body`, `StaticPage.body` (і `catalog.Product.description`) — це
     НЕ plain text. Це HTML із вбудованими зображеннями (INPUTS §2: робот-пилосос з фото між
     абзацами). Тому body отримує WysiwygWidget (unfold.contrib.forms), а картинки з редактора
     осідають у `EditorImage` — бо санітайзер пропускає `img[src]` ТІЛЬКИ з нашого домену.
     🔴 Опис НЕ ФІЛЬТРУЄТЬСЯ і НЕ бере участі у фасетах. Фільтрація — тільки по
        характеристиках (catalog.ProductAttributeValue). Це вимога замовника, не деталь.

  2. UA/RU. Кожне текстове поле — дві колонки (`_uk` / `_ru`, modeltranslation). Unfold
     показує їх ВКЛАДКАМИ через TabbedTranslationAdmin — інакше форма новини перетворюється
     на дві однакові простирадла підряд.
     ⚠️ `_ru` заповнює НЕ людина руками, а черга схвалення перекладів
        (translation.TranslationEntry.approve → write-back у `*_ru`). Порожній `_ru` віддає
        `_uk` через MODELTRANSLATION_FALLBACK_LANGUAGES — сторінка ніколи не порожня.

⚠️ `StaticPage.key` НЕ перекладний і НЕ редагується після створення без потреби: це ключ
   маршрутизації (/page/<key>). Змінили — зламали зовнішні посилання й індексацію.
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Model
from django.http import HttpRequest
from django.utils.html import format_html
from modeltranslation.admin import TabbedTranslationAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.contrib.forms.widgets import WysiwygWidget
from unfold.decorators import display

from cms.admin_previews import SLOT_COLORS, layout_preview, placement_badge
from cms.models import Banner, EditorImage, MenuItem, NewsPost, PickupPoint, StaticPage


class WysiwygBodyMixin:
    """
    Rich-редактор для `body` — і для `body_uk`, і для `body_ru`.

    Тонкість modeltranslation: у формі немає поля `body`, є `body_uk` / `body_ru`, і віджет
    для них КОПІЮЄТЬСЯ з оригінального поля (patch_translation_field). Тому перевіряємо
    ОРИГІНАЛЬНЕ ім'я поля (`db_field.translated_field`), а не те, що прийшло.
    """

    wysiwyg_fields: tuple[str, ...] = ("body",)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        origin = getattr(db_field, "translated_field", None)
        name = origin.name if origin is not None else db_field.name
        if name in self.wysiwyg_fields:
            kwargs["widget"] = WysiwygWidget
        return super().formfield_for_dbfield(db_field, request, **kwargs)


def image_preview(image, height: int = 44) -> str:
    if not image:
        return "—"
    return format_html(
        '<img src="{}" style="height:{}px;border-radius:6px;object-fit:cover" />',
        image.url,
        height,
    )


# ---------------------------------------------------------------------------
# Статичні сторінки
# ---------------------------------------------------------------------------
@admin.register(StaticPage)
class StaticPageAdmin(WysiwygBodyMixin, ModelAdmin, TabbedTranslationAdmin):
    list_display = (
        "title",
        "key",
        "is_published",
        "show_in_footer",
        "show_in_menu",
        "sort_order",
        "updated_at",
    )
    list_editable = ("is_published", "show_in_footer", "show_in_menu", "sort_order")
    list_filter = ("is_published", "show_in_footer", "show_in_menu")
    search_fields = ("title", "key")
    ordering = ("sort_order", "id")

    fieldsets = (
        (
            "Сторінка",
            {
                "description": (
                    "🔴 «Оплата і доставка», «Повернення та обмін» і «Контакти» перевіряють "
                    "при верифікації магазину в LiqPay — без них платежі не підключать."
                ),
                "fields": ("key", "title", "body"),
            },
        ),
        (
            "Публікація",
            {"fields": ("is_published", ("show_in_footer", "show_in_menu"), "sort_order")},
        ),
        ("SEO", {"classes": ("collapse",), "fields": ("seo_title", "seo_description", "og_image")}),
    )


# ---------------------------------------------------------------------------
# Новини
# ---------------------------------------------------------------------------
@admin.register(NewsPost)
class NewsPostAdmin(WysiwygBodyMixin, ModelAdmin, TabbedTranslationAdmin):
    list_display = ("cover_preview", "title", "is_published", "published_at", "updated_at")
    list_display_links = ("cover_preview", "title")
    list_filter = ("is_published", ("published_at", RangeDateTimeFilter))
    list_filter_submit = True
    search_fields = ("title", "excerpt")
    date_hierarchy = "published_at"
    prepopulated_fields = {"slug": ("title",)}  # modeltranslation розкладе на slug_uk/slug_ru

    fieldsets = (
        ("Новина", {"fields": ("title", "slug", "cover", "excerpt", "body")}),
        ("Публікація", {"fields": ("is_published", "published_at")}),
        ("SEO", {"classes": ("collapse",), "fields": ("seo_title", "seo_description", "og_image")}),
    )

    @display(description="Обкладинка")
    def cover_preview(self, obj: NewsPost) -> str:
        return image_preview(obj.cover)


# ---------------------------------------------------------------------------
# Банери
# ---------------------------------------------------------------------------
@admin.register(Banner)
class BannerAdmin(ModelAdmin, TabbedTranslationAdmin):
    """⚡ `image` теж перекладне: у банері текст запечений у картинку (див. cms/models.py)."""

    list_display = (
        "preview",
        "title",
        "placement",
        "supported_badge",
        "sort_order",
        "period",
        "live_badge",
        "is_active",
    )
    list_display_links = ("preview", "title")
    list_editable = ("sort_order", "is_active")
    list_filter = (("placement", ChoicesDropdownFilter), "is_active")
    search_fields = ("title", "subtitle")
    ordering = ("placement", "sort_order", "id")
    readonly_fields = ("layout_preview",)

    fieldsets = (
        (
            "Розміщення",
            {
                "description": (
                    "Оберіть розміщення — схема нижче покаже, у яке саме місце сайту "
                    "потрапить банер. ⚠️ У кожному місці показується РІВНО ОДИН банер — "
                    "той, у якого менший «Порядок». Решта чекають своєї черги."
                ),
                "fields": (
                    "placement",
                    "layout_preview",
                    "category",
                    "sort_order",
                    "is_active",
                ),
            },
        ),
        ("Вміст", {"fields": ("title", "subtitle", "image", "image_mobile", "link_url")}),
        (
            "Період показу",
            {
                "description": "Порожньо з будь-якого боку = без обмеження.",
                "fields": (("starts_at", "ends_at"),),
            },
        ),
    )

    @display(description="Прев'ю")
    def preview(self, obj: Banner) -> str:
        return image_preview(obj.image, height=40)

    @display(description="Де це на сайті")
    def layout_preview(self, obj: Banner | None) -> str:
        """Схема сторінки з підсвіченим слотом — щоб не гадати, що робить `placement`.

        На формі СТВОРЕННЯ `obj` порожній: показуємо порожній макет, і людина бачить
        обидва стани головної ще до того, як щось обрала.
        """
        placement = getattr(obj, "placement", "") or ""
        image = getattr(obj, "image", None)
        return layout_preview(placement, image.url if image else "")

    @display(description="Слот", label=SLOT_COLORS)
    def supported_badge(self, obj: Banner) -> str:
        """⚠️ Окремо від «Показується»: там період і галочка, а тут — куди банер потрапляє.
        Банер може бути активним і при цьому не показуватись: або розміщення взагалі не
        виводиться, або слот уже зайнятий іншим банером з меншим «Порядком»."""
        return placement_badge(obj.placement)

    @display(description="Період")
    def period(self, obj: Banner) -> str:
        start = obj.starts_at.strftime("%d.%m.%Y") if obj.starts_at else "завжди"
        end = obj.ends_at.strftime("%d.%m.%Y") if obj.ends_at else "безстроково"
        return f"{start} → {end}"

    @display(
        description="Показується",
        label={True: "success", False: "default"},
    )
    def live_badge(self, obj: Banner) -> tuple[bool, str]:
        return obj.is_live, "Зараз на сайті" if obj.is_live else "Не показується"


# ---------------------------------------------------------------------------
# Меню
# ---------------------------------------------------------------------------
@admin.register(MenuItem)
class MenuItemAdmin(ModelAdmin, TabbedTranslationAdmin):
    list_display = ("title", "zone", "parent", "target", "icon", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    list_filter = (("zone", ChoicesDropdownFilter), "is_active")
    search_fields = ("title", "url")
    ordering = ("zone", "sort_order", "id")
    list_select_related = ("parent", "static_page")

    fieldsets = (
        ("Пункт", {"fields": ("zone", "parent", "title", "icon")}),
        (
            "Куди веде",
            {
                "description": (
                    "Обрано сторінку → URL будується з її ключа, а поле «Посилання» "
                    "ігнорується. Пункт-заголовок розділу («Інформація») лишається без обох."
                ),
                "fields": ("static_page", "url"),
            },
        ),
        ("Показ", {"fields": ("sort_order", "is_active")}),
    )

    @display(description="Веде на")
    def target(self, obj: MenuItem) -> str:
        if obj.static_page_id:
            return f"/page/{obj.static_page.key}"
        return obj.url or "— (заголовок розділу)"


# ---------------------------------------------------------------------------
# Самовивіз
# ---------------------------------------------------------------------------
@admin.register(PickupPoint)
class PickupPointAdmin(ModelAdmin, TabbedTranslationAdmin):
    """Координати й графік їдуть ще й у фід Hotline (<store>) — це не просто контакти."""

    list_display = ("name", "address", "city", "phone", "working_hours", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    list_filter = ("is_active", "city")
    search_fields = ("name", "address", "phone")
    ordering = ("sort_order", "id")

    fieldsets = (
        ("Точка", {"fields": ("name", "address", "city", "phone", "working_hours")}),
        (
            "Координати (йдуть у фід Hotline)",
            {"fields": (("latitude", "longitude"), "sort_order", "is_active")},
        ),
    )


# ---------------------------------------------------------------------------
# Зображення редактора
# ---------------------------------------------------------------------------
@admin.register(EditorImage)
class EditorImageAdmin(ModelAdmin):
    list_display = ("preview", "file", "alt", "size", "uploaded_by", "created_at")
    list_display_links = ("preview", "file")
    search_fields = ("file", "alt")
    date_hierarchy = "created_at"
    readonly_fields = ("width", "height", "created_at", "updated_at", "preview")
    list_select_related = ("uploaded_by",)

    @display(description="Прев'ю")
    def preview(self, obj: EditorImage) -> str:
        return image_preview(obj.file, height=48)

    @display(description="Розмір")
    def size(self, obj: EditorImage) -> str:
        if obj.width and obj.height:
            return f"{obj.width}×{obj.height}"
        return "—"

    def save_model(self, request: HttpRequest, obj: Model, form, change: bool) -> None:
        if not change and not obj.uploaded_by_id:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)
