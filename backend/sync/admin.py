"""
Синхронізація — ПУЛЬТ. Ключовий екран усієї адмінки.

Тут менеджер відповідає на три питання і жодного більше:
    1. Коли востаннє приїхав прайс і чим це закінчилось?      → SyncRunAdmin (список)
    2. Що саме пішло не так?                                   → журнал прогону (WARN/ERROR)
    3. Як це полагодити просто зараз?                          → «Оновити з Google Sheets зараз»
                                                                  / «Відкотити прогін»

⚡ І ОДИН екран тут — НЕ для менеджера: DataIssueAdmin (SYNC.md §12). Це звіт для ЗАМОВНИКА
   про проблеми в ЙОГО таблиці: лист + номер рядка + що зробити, плюс кнопка «Експортувати
   у CSV». Полагодити ці дані може тільки він — і тільки у СВОЇЙ Google-таблиці.

🔴 ПРОГОНИ НЕ РЕДАГУЮТЬСЯ. SyncRun / SyncLogEntry / SupplierOffer / ProductPriceSnapshot /
   UsdRateChange — це ЖУРНАЛИ. Правка журналу знищує єдине джерело відповіді на питання
   «чому в каталозі оце». Тому всі вони readonly (ReadOnlyAdmin), і це не «поки що».

⚡ РЕДАГУЄТЬСЯ рівно те, що описує ДЖЕРЕЛО даних: PriceSource (пріоритет, націнка, дефолти,
   запобіжники) і геометрія листів (PriceSheet / SpecSheet: column_map, header_row,
   data_start_row). Це свідомо: постачальник вставить рядок у таблицю або перейменує колонку —
   і це МУСИТЬ правитись в адмінці, без деплою (SYNC.md §1.5).

⚡ КНОПКИ СТАВЛЯТЬ РЕАЛЬНІ ЗАДАЧІ, і задачі імпортуються ЗВЕРХУ (`from sync.tasks import …`),
   а не по рядку через importlib. Це не стиль, це урок: доки тут був «розумний» диспетчер
   за dotted-path, кнопка «Відкотити прогін» рік викликала неіснуючу `rollback_run` і падала
   в рантаймі, а «Оновити зараз» передавала параметр, якого в задачі немає. Прямий імпорт
   ламає збірку на старті — тобто в CI, а не під руками менеджера.

🔴 SyncRun СТВОРЮЄ ЗАДАЧА, НЕ АДМІНКА. `run_price_sync()` заводить прогін сама, всередині
   advisory-локу (sync/services.py:1603). Якщо створити SyncRun ще й тут, у журналі осідає
   вічний RUNNING-фантом, якого ніхто не закриє.
"""

from __future__ import annotations

import io
import json

from django.contrib import admin, messages
from django.db import models as django_models
from django.db.models import QuerySet
from django.forms import Textarea
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from sync.management.commands.data_issues import CSV_ENCODING, write_issues_csv
from sync.models import (
    DataIssue,
    PriceSheet,
    PriceSource,
    ProductPriceSnapshot,
    SpecSheet,
    SupplierOffer,
    SyncLogEntry,
    SyncRun,
    UsdRateChange,
)
from sync.tasks import rollback_sync_run, sync_prices

STATUS_LABELS: dict[str, str] = {
    SyncRun.Status.RUNNING: "info",
    SyncRun.Status.SUCCESS: "success",
    SyncRun.Status.PARTIAL: "warning",
    SyncRun.Status.FAILED: "danger",
    SyncRun.Status.ABORTED: "danger",  # запобіжник спрацював — це НЕ «ок»
    SyncRun.Status.SKIPPED: "default",
}

LEVEL_LABELS: dict[str, str] = {
    SyncLogEntry.Level.INFO: "default",
    SyncLogEntry.Level.WARN: "warning",
    SyncLogEntry.Level.ERROR: "danger",
}

#: Кольори типів проблем: червоне — ЛАМАЄ дані (товар не потрапить на сайт узагалі або
#: перетре інший), жовте — товар створено, але схований, поки замовник не допише рядок.
ISSUE_LABELS: dict[str, str] = {
    DataIssue.Kind.DUPLICATE_SKU: "danger",
    DataIssue.Kind.MISSING_SKU: "warning",
    DataIssue.Kind.MISSING_PHOTO: "warning",
    # ↑ MISSING_CATEGORY — теж "danger": на відміну від MISSING_SKU/MISSING_PHOTO (товар
    #   просто СХОВАНИЙ, доки замовник дозаповнить), тут товар осідає в службовому
    #   «__unmapped__» — це блокер показу в каталозі, і на реальному прайсі це НАЙБІЛЬША
    #   за обсягом проблема (353 товари), яка досі не потрапляла в жоден звіт.
    DataIssue.Kind.MISSING_CATEGORY: "danger",
    DataIssue.Kind.UNKNOWN_CATEGORY: "info",
    DataIssue.Kind.SPEC_NOT_FOUND: "default",
}

#: Людські підписи до лічильників зі `SyncRun.stats` (JSON). Порядок = порядок у картці.
#: Сирий JSON лишається нижче, у «Діагностиці», — але читати його ніхто не мусить.
STATS_LABELS: tuple[tuple[str, str], ...] = (
    ("rows_incomplete", "Сховано як неповні (немає артикула та/або фото)"),
    ("rows_surrogate_sku", "Рядків без артикула («Уточнюється») — ключ згенеровано"),
    ("rows_no_photo", "Рядків без фото"),
    ("rows_missing_category", "Рядків без категорії (лежать у службовій «__unmapped__»)"),
    ("surrogates_adopted", "Артикул з'явився — товар упізнано (дубль НЕ створено)"),
    ("products_hidden_incomplete", "Приховано товарів з сайту"),
    ("products_auto_activated", "Автоматично активовано (дані доповнено)"),
)


# ---------------------------------------------------------------------------
# Спільне
# ---------------------------------------------------------------------------
class ReadOnlyAdmin(ModelAdmin):
    """Журнал: дивитись — так, правити — ні. Див. шапку модуля."""

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> tuple:
        return tuple(f.name for f in self.model._meta.fields)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


def pretty_json(value) -> str:
    """Згорнутий JSON-в'ювер: у списку — короткий рядок, у картці — читабельний блок."""
    if not value:
        return "—"
    dumped = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return format_html(
        '<details><summary style="cursor:pointer">{} символів JSON</summary>'
        '<pre style="white-space:pre-wrap;font-size:12px;margin-top:6px">{}</pre></details>',
        len(dumped),
        dumped,
    )


# ---------------------------------------------------------------------------
# PriceSource + листи
# ---------------------------------------------------------------------------
class JSONTextareaMixin:
    """column_map / guard_report — редагуються як JSON-текст (моноширинний, 10 рядків)."""

    formfield_overrides = {
        django_models.JSONField: {
            "widget": Textarea(
                attrs={"rows": 10, "style": "font-family:ui-monospace,monospace;font-size:12px"}
            )
        },
    }


class PriceSheetInline(JSONTextareaMixin, TabularInline):
    model = PriceSheet
    extra = 0
    fields = (
        "tab_name",
        "currency",
        "column_map",
        "header_row",
        "data_start_row",
        "rate_cell",
        "date_cell",
        "sort_order",
        "is_active",
    )


class SpecSheetInline(JSONTextareaMixin, TabularInline):
    model = SpecSheet
    extra = 0
    fields = (
        "spreadsheet_id",
        "tab_name",
        "column_map",
        "header_row",
        "data_start_row",
        "spec_triplet_start_col",
        "is_active",
    )


@admin.register(PriceSource)
class PriceSourceAdmin(ModelAdmin):
    list_display = (
        "name",
        "code",
        "kind",
        "primary_badge",
        "priority",
        "price_markup_percent",
        "default_availability",
        "default_lead_days",
        "is_active",
    )
    list_editable = ("priority", "price_markup_percent", "is_active")
    list_filter = ("kind", "is_active", "is_primary")
    search_fields = ("code", "name", "spreadsheet_id")
    ordering = ("priority", "id")
    inlines = (PriceSheetInline, SpecSheetInline)

    fieldsets = (
        ("Джерело", {"fields": ("code", "name", "kind", "is_primary", "is_active")}),
        (
            "Пріоритет і ціна",
            {
                "description": (
                    "МЕНШИЙ пріоритет виграє при збігу артикула між джерелами "
                    "(project_offers, SYNC.md §3)."
                ),
                "fields": ("priority", "price_markup_percent", "overwrite_names"),
            },
        ),
        (
            "Дефолти, коли даних немає в прайсі",
            {"fields": ("default_availability", "default_lead_days")},
        ),
        ("Доступ", {"fields": ("spreadsheet_id", "feed_url")}),
        (
            "Запобіжники (прогін ABORTED, якщо порушено)",
            {
                "description": (
                    "Зупиняють прогін, який зібрався викосити каталог: обрізаний прайс, "
                    "масова деактивація, з'їхала колонка ціни."
                ),
                "fields": (
                    "guard_min_rows_ratio",
                    "guard_max_deactivate_pct",
                    "guard_max_median_price_pct",
                ),
            },
        ),
    )

    @display(description="Власний склад", boolean=True, ordering="is_primary")
    def primary_badge(self, obj: PriceSource) -> bool:
        return obj.is_primary


@admin.register(PriceSheet)
class PriceSheetAdmin(JSONTextareaMixin, ModelAdmin):
    list_display = (
        "tab_name",
        "price_source",
        "currency",
        "header_row",
        "data_start_row",
        "rate_cell",
        "sort_order",
        "is_active",
    )
    list_editable = ("header_row", "data_start_row", "sort_order", "is_active")
    list_filter = ("price_source", "currency", "is_active")
    search_fields = ("tab_name", "price_source__name", "price_source__code")
    list_select_related = ("price_source",)


@admin.register(SpecSheet)
class SpecSheetAdmin(JSONTextareaMixin, ModelAdmin):
    list_display = (
        "tab_name",
        "price_source",
        "spreadsheet_id",
        "header_row",
        "data_start_row",
        "spec_triplet_start_col",
        "is_active",
    )
    list_editable = ("header_row", "data_start_row", "spec_triplet_start_col", "is_active")
    list_filter = ("price_source", "is_active")
    search_fields = ("tab_name", "spreadsheet_id")
    list_select_related = ("price_source",)


# ---------------------------------------------------------------------------
# SyncRun — головний екран
# ---------------------------------------------------------------------------
class SyncLogEntryInline(TabularInline):
    """
    Журнал прогону просто в картці.

    ⚡ Показуємо ЛИШЕ WARN і ERROR. Це не косметика: саме вони й пишуться (SYNC.md §3.4 —
    INFO майже не логується), і саме вони відповідають на питання «що зламалось».
    Повний журнал (з фільтром по рівню) — посилання «Увесь журнал прогону» у картці.
    """

    model = SyncLogEntry
    extra = 0
    can_delete = False
    verbose_name = "Запис журналу"
    verbose_name_plural = "Журнал прогону — тільки попередження і помилки"

    fields = ("level", "action", "sheet", "row_number", "sku", "message")
    readonly_fields = fields

    def get_queryset(self, request: HttpRequest):
        return (
            super()
            .get_queryset(request)
            .filter(level__in=[SyncLogEntry.Level.WARN, SyncLogEntry.Level.ERROR])
        )

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


@admin.register(SyncRun)
class SyncRunAdmin(ReadOnlyAdmin):
    list_display = (
        "kind",
        "status_badge",
        "price_source",
        "started_at",
        "duration_display",
        "counters",
        "errors_display",
        "issues_display",
        "trigger",
    )
    list_filter = (
        ("kind", ChoicesDropdownFilter),
        ("status", ChoicesDropdownFilter),
        ("trigger", ChoicesDropdownFilter),
        "price_source",
        ("started_at", RangeDateTimeFilter),
    )
    list_filter_submit = True
    search_fields = ("id", "traceback")
    date_hierarchy = "started_at"
    ordering = ("-started_at",)
    list_select_related = ("price_source", "started_by")
    inlines = (SyncLogEntryInline,)

    # 🔵 Кнопка з ТЗ: «Оновити з Google Sheets зараз» — у шапці списку прогонів.
    actions_list = ("sync_now",)
    actions = ("rollback_run",)

    fieldsets = (
        (
            "Прогін",
            {
                "fields": (
                    "id",
                    "kind",
                    "status",
                    "price_source",
                    ("trigger", "started_by"),
                    ("dry_run", "forced"),
                    "rolled_back_run",
                    "journal_link",
                )
            },
        ),
        (
            "Час",
            {"fields": (("started_at", "finished_at"), "duration_display")},
        ),
        (
            "Підсумок прогону",
            {
                "description": (
                    "Що прогін зробив з даними замовника. Рядок без артикула або без фото — "
                    "це НЕ помилка синку, а чернетка в прайсі: товар створено, але схований, "
                    "поки рядок не допишуть. Список таких рядків — «Проблеми в даних»."
                ),
                "fields": ("summary_view", "issues_view"),
            },
        ),
        (
            "Лічильники",
            {
                "fields": (
                    ("rows_read", "rows_expected"),
                    ("products_created", "products_updated", "products_deactivated"),
                    ("rows_skipped", "rows_failed", "rows_duplicated"),
                    ("images_queued", "specs_filled"),
                    "categories_missing",
                )
            },
        ),
        (
            "Діагностика",
            {
                "classes": ("collapse",),
                "fields": ("guard_report_view", "stats_view", "raw_snapshot_key", "traceback"),
            },
        ),
    )

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> tuple:
        return (
            *super().get_readonly_fields(request, obj),
            "duration_display",
            "summary_view",
            "issues_view",
            "guard_report_view",
            "stats_view",
            "journal_link",
        )

    # -- колонки -------------------------------------------------------------

    @display(description="Статус", ordering="status", label=STATUS_LABELS)
    def status_badge(self, obj: SyncRun) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    @display(description="Тривалість", ordering="duration_ms")
    def duration_display(self, obj: SyncRun) -> str:
        if obj.duration_ms is None:
            return "—"
        if obj.duration_ms < 1000:
            return f"{obj.duration_ms} мс"
        return f"{obj.duration_ms / 1000:.1f} с"

    @display(description="Створено / оновлено / деактивовано")
    def counters(self, obj: SyncRun) -> str:
        return format_html(
            '<span style="color:#16a34a">+{}</span> / '
            '<span style="color:#2563eb">~{}</span> / '
            '<span style="color:#dc2626">−{}</span>',
            obj.products_created,
            obj.products_updated,
            obj.products_deactivated,
        )

    @display(description="Помилок", ordering="rows_failed")
    def errors_display(self, obj: SyncRun) -> str:
        total = obj.rows_failed + obj.rows_skipped + obj.rows_duplicated
        if not total:
            return "0"
        return format_html(
            "<b>{}</b> <small>(невалідних {} · пропущено {} · дублів {})</small>",
            total,
            obj.rows_failed,
            obj.rows_skipped,
            obj.rows_duplicated,
        )

    @display(description="Проблем у даних")
    def issues_display(self, obj: SyncRun) -> str:
        """Скільки рядків прайсу треба виправити замовнику. Зі `stats` — без зайвого COUNT."""
        counts = obj.stats.get("issues") or {}
        total = sum(counts.values())
        if not total:
            return "0"
        url = reverse("admin:sync_dataissue_changelist")
        return format_html('<a href="{}?run__id__exact={}"><b>{}</b></a>', url, obj.pk, total)

    @display(description="Підсумок прогону")
    def summary_view(self, obj: SyncRun) -> SafeString:
        """
        Людські підписи замість сирого JSON: «Сховано як неповні: 247».

        Показуємо ЛИШЕ ті ключі, які цей прогін реально порахував (`stats` у старих прогонів
        їх не має — не малюємо порожніх нулів там, де лічильника просто не існувало).
        """
        rows = [
            (label, obj.stats[key]) for key, label in STATS_LABELS if obj.stats.get(key) is not None
        ]
        if not rows:
            return format_html(
                '<span style="opacity:.6">Лічильників немає: прогін старого формату '
                "або не дійшов до запису.</span>"
            )
        return format_html(
            '<table style="border-collapse:collapse">{}</table>',
            format_html_join(
                "",
                '<tr><td style="padding:2px 12px 2px 0">{}:</td>'
                '<td style="padding:2px 0"><b>{}</b></td></tr>',
                rows,
            ),
        )

    @display(description="Проблеми в даних (для замовника)")
    def issues_view(self, obj: SyncRun) -> SafeString:
        """Розбивка проблем по типах + посилання одразу у відфільтрований звіт."""
        counts = obj.stats.get("issues") or {}
        if not sum(counts.values()):
            return format_html("Проблем у даних не знайдено. 🎉")
        url = reverse("admin:sync_dataissue_changelist")
        rows = [
            (
                DataIssue.Kind(kind).label if kind in DataIssue.Kind.values else kind,
                count,
                url,
                obj.pk,
                kind,
            )
            for kind, count in sorted(counts.items(), key=lambda kv: -kv[1])
            if count
        ]
        return format_html(
            '<table style="border-collapse:collapse">{}</table>'
            '<div style="margin-top:6px"><a href="{}?run__id__exact={}"><b>Відкрити звіт '
            "і вивантажити CSV для замовника</b></a></div>",
            format_html_join(
                "",
                '<tr><td style="padding:2px 12px 2px 0">{}:</td>'
                '<td style="padding:2px 0"><a href="{}?run__id__exact={}&kind__exact={}">'
                "<b>{}</b></a></td></tr>",
                ((label, u, pk, kind, count) for label, count, u, pk, kind in rows),
            ),
            url,
            obj.pk,
        )

    @display(description="Звіт запобіжників")
    def guard_report_view(self, obj: SyncRun) -> str:
        return pretty_json(obj.guard_report)

    @display(description="Статистика (сирий JSON)")
    def stats_view(self, obj: SyncRun) -> str:
        return pretty_json(obj.stats)

    @display(description="Журнал")
    def journal_link(self, obj: SyncRun) -> str:
        if not obj.pk:
            return "—"
        url = reverse("admin:sync_synclogentry_changelist")
        return format_html(
            '<a href="{}?run__id__exact={}">Увесь журнал прогону</a> &nbsp;·&nbsp; '
            '<a href="{}?run__id__exact={}&level__exact=error">Тільки помилки</a>',
            url,
            obj.pk,
            url,
            obj.pk,
        )

    # -- дії -----------------------------------------------------------------

    @action(
        description="Оновити з Google Sheets зараз",
        url_path="sync-now",
        icon="cloud_sync",
        variant=ActionVariant.PRIMARY,
    )
    def sync_now(self, request: HttpRequest) -> HttpResponse:
        """Кнопка з ТЗ. Прогін заводить сама задача — тут лише запуск (див. шапку модуля)."""
        source = (
            PriceSource.objects.filter(is_active=True, is_primary=True).first()
            or PriceSource.objects.filter(is_active=True).first()
        )
        if source is None:
            self.message_user(
                request,
                "Немає жодного активного джерела прайсу. Спочатку створіть PriceSource "
                "(наприклад, «Complex, власний склад», priority=100).",
                level=messages.ERROR,
            )
            return redirect(reverse("admin:sync_pricesource_changelist"))

        sync_prices.delay(
            source_id=source.pk,
            started_by=request.user.pk,
            trigger=SyncRun.Trigger.MANUAL,
        )
        self.message_user(
            request,
            f"Прогін запущено (джерело «{source.name}»). Оновіть сторінку за хвилину. "
            "Якщо інший прогін цього джерела вже йде, новий стане SKIPPED — це нормально.",
            level=messages.SUCCESS,
        )
        return redirect(reverse("admin:sync_syncrun_changelist"))

    @admin.action(description="Відкотити прогін (за знімком цін)")
    def rollback_run(self, request: HttpRequest, queryset) -> None:
        """
        Відкат по ProductPriceSnapshot (SYNC.md §5).

        Показуємо РЕАЛЬНУ цифру: скільки товарів у знімку — це те, що відкат зачепить.
        """
        for run in queryset:
            snapshots = ProductPriceSnapshot.objects.filter(run=run).count()
            if not snapshots:
                self.message_user(
                    request,
                    f"Прогін {run.pk}: знімка цін немає — відкотити нічого. "
                    "Знімок пишеться лише перед проєкцією прайсу.",
                    level=messages.ERROR,
                )
                continue

            rollback_sync_run.delay(run_id=str(run.pk), user_id=request.user.pk)
            self.message_user(
                request,
                f"Відкат прогону {run.pk} поставлено в чергу ({snapshots} товарів у знімку).",
                level=messages.SUCCESS,
            )


@admin.register(SyncLogEntry)
class SyncLogEntryAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "level_badge", "action", "sheet", "row_number", "sku", "message")
    list_filter = (
        ("level", ChoicesDropdownFilter),
        ("action", ChoicesDropdownFilter),
        "run",
        ("created_at", RangeDateTimeFilter),
    )
    list_filter_submit = True
    search_fields = ("sku", "message")
    date_hierarchy = "created_at"
    list_select_related = ("run",)

    @display(description="Рівень", ordering="level", label=LEVEL_LABELS)
    def level_badge(self, obj: SyncLogEntry) -> tuple[str, str]:
        return obj.level, obj.get_level_display()


# ---------------------------------------------------------------------------
# DataIssue — ЗВІТ ДЛЯ ЗАМОВНИКА (SYNC.md §12)
# ---------------------------------------------------------------------------
@admin.register(DataIssue)
class DataIssueAdmin(ReadOnlyAdmin):
    """
    Єдиний екран адмінки, який дивиться НЕ менеджер, а ЗАМОВНИК (Артур).

    Він не читає логів і ніколи не читатиме. Йому треба рівно чотири речі:
    ЩО не так · У ЯКОМУ ЛИСТІ · В ЯКОМУ РЯДКУ · ЩО ЗРОБИТИ. Тому «Лист» і «Рядок» тут —
    найпомітніші колонки: саме за ними він знайде рядок у СВОЇЙ Google-таблиці.

    Це ЗВІТ, а не таблиця для правок: правити треба ТАБЛИЦЮ, а не цей список (виправлення
    тут не долетить нікуди й наступний прогін його затре). Звідси ReadOnlyAdmin і головна
    дія екрана — «Експортувати у CSV»: файл кидається замовнику в месенджер, відкривається
    в Google Sheets, і він іде правити рядок за рядком.
    """

    list_display = (
        "kind_badge",
        "sheet_badge",
        "row_badge",
        "sku",
        "name",
        "message",
    )
    list_filter = (
        ("kind", ChoicesDropdownFilter),
        "run",
        "sheet",
    )
    list_filter_submit = True
    search_fields = ("sku", "name")
    search_help_text = "Артикул або назва товару з прайсу"
    list_select_related = ("run",)
    list_fullwidth = True
    list_per_page = 50
    actions = ("export_csv",)
    # ⚠️ Без фільтра по прогону список — це СУМА всіх прогонів (кожен dry-run дописує свої
    #    2591 рядки). Замовнику треба ОСТАННІЙ — рівно те, що показує `data_issues` без --run.
    actions_list = ("latest_run_issues",)

    fieldsets = (
        (
            "Де це в таблиці",
            {
                "description": (
                    "Відкрийте свою Google-таблицю, перейдіть на вказаний лист і знайдіть "
                    "цей рядок за номером."
                ),
                "fields": ("location_view", "sku", "name"),
            },
        ),
        ("Що зробити", {"fields": ("kind", "message")}),
        ("Службове", {"classes": ("collapse",), "fields": ("run", "created_at", "payload_view")}),
    )

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> tuple:
        return (*super().get_readonly_fields(request, obj), "location_view", "payload_view")

    # -- колонки -------------------------------------------------------------
    @display(description="Тип проблеми", ordering="kind", label=ISSUE_LABELS)
    def kind_badge(self, obj: DataIssue) -> tuple[str, str]:
        return obj.kind, obj.get_kind_display()

    @display(description="Лист", ordering="sheet")
    def sheet_badge(self, obj: DataIssue) -> SafeString:
        if not obj.sheet:
            return format_html("—")
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:6px;'
            'background:rgba(128,128,128,.14);font-weight:600;white-space:nowrap">{}</span>',
            obj.sheet,
        )

    @display(description="Рядок у таблиці", ordering="row_number")
    def row_badge(self, obj: DataIssue) -> SafeString:
        if not obj.row_number:
            return format_html("—")
        return format_html(
            '<b style="font-size:16px;font-variant-numeric:tabular-nums">{}</b>', obj.row_number
        )

    # -- картка --------------------------------------------------------------
    @display(description="Лист і рядок")
    def location_view(self, obj: DataIssue) -> SafeString:
        if not obj.pk:
            return format_html("—")
        where = f"рядок {obj.row_number}" if obj.row_number else "рядок невідомий"
        return format_html(
            '<div style="font-size:20px"><b>{}</b> · лист «<b>{}</b>»</div>',
            where,
            obj.sheet or "—",
        )

    @display(description="Деталі")
    def payload_view(self, obj: DataIssue) -> str:
        return pretty_json(obj.payload)

    # -- дії -----------------------------------------------------------------
    @action(
        description="Показати останній прогін",
        url_path="latest-run",
        icon="filter_alt",
        variant=ActionVariant.PRIMARY,
    )
    def latest_run_issues(self, request: HttpRequest) -> HttpResponse:
        """Фільтр «тільки останній прогін прайсу» — те саме, що `data_issues` без --run."""
        url = reverse("admin:sync_dataissue_changelist")
        latest = (
            SyncRun.objects.filter(kind=SyncRun.Kind.SHEETS_PRICES).order_by("-started_at").first()
        )
        if latest is None:
            self.message_user(request, "Жодного прогону прайсу ще не було.", level=messages.WARNING)
            return redirect(url)
        return redirect(f"{url}?run__id__exact={latest.pk}")

    @admin.action(description="Експортувати у CSV (для замовника)")
    def export_csv(self, request: HttpRequest, queryset: QuerySet[DataIssue]) -> HttpResponse:
        """
        Той САМИЙ файл, що й `manage.py data_issues --csv` — та сама функція
        (`write_issues_csv`), ті самі колонки, те саме кодування. Дві вивантажки, які
        розійшлись би колонками, — це два різні звіти і зіпсована довіра до обох.

        BOM (utf-8-sig) додається РІВНО ОДИН раз, на весь файл: якби ми віддавали
        `HttpResponse(charset="utf-8-sig")`, Django кодував би КОЖЕН `write()` окремо —
        і BOM опинився б на початку кожного рядка.
        """
        buffer = io.StringIO(newline="")
        write_issues_csv(queryset, buffer)

        filename = f"problemy-v-danyh-{timezone.localdate():%Y-%m-%d}.csv"
        response = HttpResponse(
            buffer.getvalue().encode(CSV_ENCODING),
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


# ---------------------------------------------------------------------------
# Журнали
# ---------------------------------------------------------------------------
@admin.register(SupplierOffer)
class SupplierOfferAdmin(ReadOnlyAdmin):
    list_display = (
        "sku",
        "price_source",
        "name",
        "base_price",
        "source_currency",
        "availability",
        "stock_qty",
        "sheet",
        "is_active",
        "updated_at",
    )
    list_filter = (
        "price_source",
        ("availability", ChoicesDropdownFilter),
        ("source_currency", ChoicesDropdownFilter),
        "sku_is_surrogate",
        "is_active",
    )
    list_filter_submit = True
    search_fields = ("sku", "mpn", "name")
    search_help_text = "Артикул, артикул виробника або назва"
    list_select_related = ("price_source",)
    ordering = ("sku",)

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> tuple:
        return (*super().get_readonly_fields(request, obj), "raw_view")

    fieldsets = (
        (
            "Пропозиція",
            {
                "fields": (
                    ("price_source", "sku"),
                    "sku_is_surrogate",
                    "name",
                    ("base_price", "base_old_price", "source_currency"),
                    ("availability", "stock_qty", "order_lead_days"),
                    ("brand_raw", "country_raw", "mpn", "category_ext_id"),
                    "photo_urls",
                )
            },
        ),
        (
            "Службове",
            {
                "fields": (
                    ("is_active", "sheet", "row_number"),
                    "last_seen_run",
                    ("first_seen_at", "updated_at"),
                    "raw_view",
                )
            },
        ),
    )

    @display(description="Сирий рядок прайсу")
    def raw_view(self, obj: SupplierOffer) -> str:
        return pretty_json(obj.raw)


@admin.register(ProductPriceSnapshot)
class ProductPriceSnapshotAdmin(ReadOnlyAdmin):
    list_display = (
        "run",
        "product",
        "old_price",
        "old_base_price",
        "old_source_currency",
        "old_availability",
        "old_stock_qty",
        "old_synced_at",
    )
    list_filter = ("run", "old_availability")
    search_fields = ("product__sku",)
    list_select_related = ("run", "product")


@admin.register(UsdRateChange)
class UsdRateChangeAdmin(ReadOnlyAdmin):
    list_display = (
        "created_at",
        "rate_change",
        "old_rounding",
        "new_rounding",
        "products_recalculated",
        "changed_by",
        "run",
    )
    list_filter = (("created_at", RangeDateTimeFilter),)
    date_hierarchy = "created_at"
    list_select_related = ("changed_by", "run")

    @display(description="Курс USD")
    def rate_change(self, obj: UsdRateChange) -> str:
        return f"{obj.old_rate or '—'} → {obj.new_rate or '—'}"
