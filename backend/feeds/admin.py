"""
Фіди — адмінка.

🔴 ЦІНА ПОМИЛКИ: Hotline публікує фід 7 днів. Не оновили — публікація ПРИПИНЯЄТЬСЯ і товари
   зникають з майданчика. Тому головна колонка тут не «коли згенеровано», а «скільки товарів
   і СКІЛЬКИ ПРОПУЩЕНО — і чому». Без skipped_reasons діагностика зводиться до «у фіді 200
   товарів замість 3000, і невідомо чому».

Артефакти — ЖУРНАЛ (readonly). Правити згенерований XML з адмінки безглуздо: наступна
генерація перезапише файл.

⚠️ Кнопка «Згенерувати фід зараз» — поки заглушка (feeds.tasks ще немає). Щойно задача
   з'явиться — кнопка почне запускати її без зміни коду (див. _dispatch_task).
"""

from __future__ import annotations

import importlib

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from feeds.models import FeedArtifact, HotlineCategory

STUB_MESSAGE = "Задача генерації фіда буде підключена наступним кроком (feeds.tasks)."


def _dispatch_task(dotted_path: str, **kwargs) -> bool:
    module_path, _, task_name = dotted_path.rpartition(".")
    try:
        module = importlib.import_module(module_path)
        task = getattr(module, task_name)
    except (ImportError, AttributeError):
        return False
    task.delay(**kwargs)
    return True


@admin.register(FeedArtifact)
class FeedArtifactAdmin(ModelAdmin):
    list_display = (
        "generated_at",
        "kind",
        "current_badge",
        "items_count",
        "skipped_display",
        "no_mpn_count",
        "default_dims_count",
        "size_display",
        "file_link",
    )
    list_filter = (
        ("kind", ChoicesDropdownFilter),
        "is_current",
        ("generated_at", RangeDateTimeFilter),
    )
    list_filter_submit = True
    date_hierarchy = "generated_at"
    ordering = ("-generated_at",)
    list_select_related = ("run",)

    # 🔵 Кнопка з ТЗ — у шапці списку.
    actions_list = ("generate_now",)

    fieldsets = (
        ("Артефакт", {"fields": ("kind", "is_current", "file", "file_link", "size_bytes")}),
        (
            "Що потрапило у фід",
            {
                "fields": (
                    ("items_count", "skipped_count"),
                    "skipped_reasons_view",
                    "no_mpn_count",
                    "default_dims_count",
                )
            },
        ),
        ("Прогін", {"fields": (("generated_at", "duration_ms"), "run", "error")}),
    )

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> tuple:
        return (
            *(f.name for f in self.model._meta.fields),
            "file_link",
            "skipped_reasons_view",
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    # -- колонки -------------------------------------------------------------

    @display(description="Поточний", label={True: "success", False: "default"})
    def current_badge(self, obj: FeedArtifact) -> tuple[bool, str]:
        return obj.is_current, "На постійному URL" if obj.is_current else "Історія"

    @display(description="Пропущено", ordering="skipped_count")
    def skipped_display(self, obj: FeedArtifact) -> str:
        if not obj.skipped_count:
            return "0"
        reasons = ", ".join(
            f"{key}: {value}" for key, value in sorted((obj.skipped_reasons or {}).items())
        )
        return format_html("<b>{}</b> <small>{}</small>", obj.skipped_count, reasons or "—")

    @display(description="Причини пропуску")
    def skipped_reasons_view(self, obj: FeedArtifact) -> str:
        reasons = obj.skipped_reasons or {}
        if not reasons:
            return "—"
        return format_html(
            "<ul style='margin:0;padding-left:18px'>{}</ul>",
            format_html_join(
                "",
                "<li><b>{}</b> — {}</li>",
                sorted(reasons.items(), key=lambda kv: -kv[1]),
            ),
        )

    @display(description="Розмір", ordering="size_bytes")
    def size_display(self, obj: FeedArtifact) -> str:
        if not obj.size_bytes:
            return "—"
        return f"{obj.size_bytes / 1024:.0f} КБ"

    @display(description="Файл")
    def file_link(self, obj: FeedArtifact) -> str:
        if not obj.file:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>', obj.file.url, obj.file.name
        )

    # -- дії -----------------------------------------------------------------

    @action(
        description="Згенерувати фід зараз",
        url_path="generate-now",
        icon="rss_feed",
        variant=ActionVariant.PRIMARY,
    )
    def generate_now(self, request: HttpRequest) -> HttpResponse:
        queued = _dispatch_task("feeds.tasks.generate_hotline_feed")
        if queued:
            self.message_user(
                request,
                "Генерацію фіда поставлено в чергу. Оновіть сторінку за хвилину.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                f"Фід НЕ ЗГЕНЕРОВАНО: {STUB_MESSAGE} Поточний артефакт не змінено.",
                level=messages.WARNING,
            )
        return redirect(reverse("admin:feeds_feedartifact_changelist"))


@admin.register(HotlineCategory)
class HotlineCategoryAdmin(ModelAdmin):
    """
    Рубрикатор Hotline — ЧУЖИЙ довідник (синк раз на тиждень з hotline_tree_uk.csv).

    Readonly свідомо: назви тут мусять збігатися з Hotline байт-у-байт (зв'язок з фідом іде
    по ТОЧНІЙ назві листової категорії). Мапінг на НАШІ категорії живе не тут, а полем
    catalog.Category.hotline_category.

    ⚡ search_fields потрібні не для краси: без них автокомпліт FK у catalog.CategoryAdmin
       не працює взагалі.
    """

    list_display = ("path", "name", "depth", "leaf_badge", "is_active")
    list_filter = ("is_leaf", "is_active", "depth")
    search_fields = ("path", "name")
    search_help_text = "Шлях або назва рубрики Hotline"
    ordering = ("path",)
    list_select_related = ("parent",)

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> tuple:
        return tuple(f.name for f in self.model._meta.fields)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @display(description="Листова", label={True: "success", False: "default"})
    def leaf_badge(self, obj: HotlineCategory) -> tuple[bool, str]:
        # Тільки листову можна поставити в Category.hotline_category — фід з нелистовою
        # категорією Hotline не приймає.
        return obj.is_leaf, "Можна мапити" if obj.is_leaf else "Вузол"
