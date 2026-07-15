"""Адмінка ядра: налаштування магазину (синглтон) + графік роботи.

⚠️ WorkingHours НЕ МАЄ FK НА SiteSettings — це самостійна таблиця з unique-полем `weekday`
   (7 рядків на весь магазин). Звичайний Django-inline тут неможливий за визначенням: він
   вимагає ForeignKey на батька. Додавати FK заради краси = зайва міграція і зайва колонка,
   яка завжди дорівнює 1.
   Тому графік вбудований через `unfold.contrib.inlines.NonrelatedTabularInline` — інлайн без
   зв'язку: він сам віддає queryset і сам зберігає нові рядки. Менеджер бачить графік прямо на
   сторінці налаштувань, як і просив замовник, а схема БД лишається чистою.
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from modeltranslation.admin import TabbedTranslationAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.inlines.admin import NonrelatedTabularInline
from unfold.decorators import action, display

from core.models import SiteSettings, WorkingHours

log = logging.getLogger(__name__)


class WorkingHoursInline(NonrelatedTabularInline):
    """Графік роботи — інлайн без FK (див. шапку модуля)."""

    model = WorkingHours
    extra = 0
    fields = ("weekday", "is_day_off", "open_time", "close_time")
    verbose_name = "День"
    verbose_name_plural = "Графік роботи"

    def get_form_queryset(self, obj: SiteSettings) -> QuerySet[WorkingHours]:
        return WorkingHours.objects.all()

    def save_new_instance(self, parent: SiteSettings, instance: WorkingHours) -> None:
        """Прив'язувати нема до чого — рядок самодостатній."""
        return None


@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin, TabbedTranslationAdmin):
    """Синглтон: один рядок (pk=1), який не створюють і не видаляють — тільки редагують."""

    inlines = (WorkingHoursInline,)
    actions_detail = ("detail_recalc_prices",)
    warn_unsaved_form = True

    readonly_fields = ("usd_rate_updated_at", "hotline_feed_generated_at", "rate_hint")

    fieldsets = (
        (
            "Курс і ціноутворення",
            {
                "fields": (
                    "usd_rate",
                    "usd_rate_updated_at",
                    "usd_rate_auto_nbu",
                    "price_rounding",
                    "rate_hint",
                ),
                "description": "⚠️ ЗМІНА КУРСУ АБО ОКРУГЛЕННЯ ЗАПУСКАЄ ПЕРЕРАХУНОК ЦІН. "
                "Курс → перераховуються тільки USD-товари. Правило округлення → "
                "ВЕСЬ каталог (інакше половина товарів лишиться в старому округленні до "
                "наступного синку). Товари з позначкою «Ціна зафіксована» не чіпаються. "
                "Перерахунок ставиться в чергу Celery при збереженні — і його ж можна "
                "запустити руками кнопкою вгорі сторінки.",
            },
        ),
        (
            "Контакти",
            {"fields": ("phones", "email", "address", "map_embed_url", "social_links")},
        ),
        (
            "Самовивіз і доставка",
            {
                "fields": (
                    "np_sender_city_ref",
                    "free_shipping_from",
                    "courier_uzhhorod_price_note",
                ),
                "description": "Адреса самовивозу — поле «Адреса магазину» в блоці «Контакти».",
            },
        ),
        (
            "Габарити упаковки за замовчуванням",
            {
                "classes": ("collapse",),
                "fields": (
                    "default_weight_kg",
                    "default_width_cm",
                    "default_height_cm",
                    "default_depth_cm",
                ),
                "description": "ОСТАННІЙ рівень фолбеку калькулятора Нової Пошти: "
                "характеристики товару → габарити товару → дефолти категорії → сюди.",
            },
        ),
        (
            "Оплата",
            {
                "fields": (
                    "payment_provider",
                    "installment_badge_text",
                    "installment_provider",
                    "installment_max_period",
                    "installment_min_payment",
                ),
                "description": "«Макс. платежів» — глобальне значення; конкретний товар може "
                "його перекрити своїм полем «Макс. платежів».",
            },
        ),
        (
            "Hotline",
            {
                "classes": ("collapse",),
                "fields": (
                    "hotline_enabled",
                    "hotline_firm_id",
                    "hotline_firm_name",
                    "hotline_feed_generated_at",
                ),
            },
        ),
        (
            "Синхронізація і алерти",
            {
                "classes": ("collapse",),
                "fields": (
                    "sync_enabled",
                    "sync_hours",
                    "sync_alert_emails",
                    "telegram_chat_id",
                ),
                "description": "«Синхронізація увімкнена» — це kill-switch: вимикає прогони "
                "повністю. «Години запуску» порожні → розклад beat не чіпаємо.",
            },
        ),
        (
            "SEO за замовчуванням",
            {
                "classes": ("collapse",),
                "fields": (
                    "default_seo_title",
                    "default_seo_description",
                    "default_og_image",
                ),
            },
        ),
        (
            "ШІ",
            {
                "classes": ("collapse",),
                "fields": ("ai_monthly_budget_usd", "ai_spent_this_month_usd"),
            },
        ),
    )

    # -- синглтон ----------------------------------------------------------
    def has_add_permission(self, request: HttpRequest) -> bool:
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        """Списку з одного рядка не існує — одразу відкриваємо сам рядок."""
        solo = SiteSettings.get_solo()
        return HttpResponseRedirect(
            reverse("admin:core_sitesettings_change", args=[solo.pk])
        )

    def save_model(self, request: HttpRequest, obj: SiteSettings, form: Any, change: bool) -> None:
        # SiteSettings.save() читає _changed_by_id для аудиту UsdRateChange.
        obj._changed_by_id = request.user.pk

        rate_changed = "usd_rate" in form.changed_data
        rounding_changed = "price_rounding" in form.changed_data

        super().save_model(request, obj, form, change)

        if rate_changed or rounding_changed:
            scope = "весь каталог" if rounding_changed else "тільки USD-товари"
            messages.warning(
                request,
                f"Курс/округлення змінено → перерахунок цін поставлено в чергу ({scope}). "
                "Ціни оновляться, щойно відпрацює Celery-воркер черги «sync».",
            )

    # -- підказки ----------------------------------------------------------
    @display(description="Що станеться при зміні")
    def rate_hint(self, obj: SiteSettings) -> SafeString:
        from catalog.models import Product  # локально: core не залежить від catalog

        usd_total = Product.objects.filter(source_currency="USD").count()
        usd_free = Product.objects.filter(source_currency="USD", price_locked=False).count()
        all_free = Product.objects.filter(price_locked=False).count()
        return format_html(
            "Зміна <b>курсу</b> перерахує USD-товарів: <b>{}</b> "
            "(з {} у каталозі; решта — із зафіксованою ціною).<br>"
            "Зміна <b>правила округлення</b> перерахує <b>{}</b> товар(ів) — увесь каталог.",
            usd_free,
            usd_total,
            all_free,
        )

    # -- дія ---------------------------------------------------------------
    @action(
        description="Перерахувати ціни за поточним курсом",
        url_path="recalc-prices",
        permissions=["change"],
        icon="currency_exchange",
    )
    def detail_recalc_prices(self, request: HttpRequest, object_id: int) -> HttpResponse:
        """Ручний запуск того самого перерахунку, який автоматично йде при зміні курсу."""
        from catalog.tasks import recalc_prices

        solo = SiteSettings.get_solo()
        recalc_prices.delay(
            scope="all",
            new_rate=str(solo.usd_rate),
            new_rounding=solo.price_rounding,
            user_id=request.user.pk,
        )
        messages.success(
            request,
            f"Перерахунок усього каталогу поставлено в чергу (курс {solo.usd_rate}, "
            f"округлення: {solo.get_price_rounding_display()}). "
            "Потрібен запущений Celery-воркер черги «sync».",
        )
        return HttpResponseRedirect(
            reverse("admin:core_sitesettings_change", args=[object_id])
        )


@admin.register(WorkingHours)
class WorkingHoursAdmin(ModelAdmin):
    """Окрема сторінка графіка — дубль інлайна на «Налаштуваннях магазину».

    Потрібна, бо `WorkingHours.weekday` має unique: додавати восьмий понеділок не можна,
    і зручніше правити 7 рядків там, де їх видно списком.
    """

    list_display = ("weekday_badge", "hours", "is_day_off")
    list_display_links = ("weekday_badge",)
    list_editable = ("is_day_off",)
    ordering = ("weekday",)

    @display(description="День", ordering="weekday")
    def weekday_badge(self, obj: WorkingHours) -> str:
        return obj.get_weekday_display()

    @display(description="Години", label={"вихідний": "danger"})
    def hours(self, obj: WorkingHours) -> str:
        if obj.is_day_off:
            return "вихідний"
        if obj.open_time and obj.close_time:
            return f"{obj.open_time:%H:%M}–{obj.close_time:%H:%M}"
        return "—"


# ---------------------------------------------------------------------------
# Технічні застосунки — геть із очей менеджера.
# Сайдбар зібраний вручну (UNFOLD["SIDEBAR"]["navigation"]), але ГОЛОВНА сторінка
# адмінки авто-перелічує всі застосунки в тілі — там і виринали axes / otp /
# celery-beat. Фільтруємо їх зі списку застосунків: у меню й на головній їх немає,
# але за прямим URL (/admin/axes/ тощо) розробник дістанеться, як і раніше.
# ---------------------------------------------------------------------------
_HIDDEN_ADMIN_APPS = frozenset({"axes", "otp_static", "otp_totp", "django_celery_beat"})
_orig_get_app_list = admin.site.get_app_list


def _get_app_list(request: HttpRequest, app_label: str | None = None) -> list:
    apps = _orig_get_app_list(request, app_label)
    # Фільтруємо ЛИШЕ загальний список (головна адмінки, app_label=None). Запит
    # конкретного застосунку — /admin/axes/ тощо — не чіпаємо, інакше його сторінка-
    # індекс віддає порожньо й падає 404. Прямий доступ за URL має лишатись робочим.
    if app_label is not None:
        return apps
    return [app for app in apps if app["app_label"] not in _HIDDEN_ADMIN_APPS]


admin.site.get_app_list = _get_app_list
