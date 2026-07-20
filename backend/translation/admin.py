"""
Переклад — ЧЕРГА СХВАЛЕННЯ. Головний робочий екран редактора (вимога замовника).

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ ЩО ТУТ ВІДБУВАЄТЬСЯ                                                                   ║
║   модель пише `target_text` → редактор ПРАВИТЬ його прямо в списку → дія «Схвалити»    ║
║   переносить текст у `published_text` і в `*_ru`-колонку моделі.                       ║
║   Сайт читає ТІЛЬКИ `published_text` / `*_ru`. Тобто неперевірений машинний переклад   ║
║   фізично не може з'явитись на сайті — не тому, що «ми обережні», а за побудовою.      ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

⚡ ЧОМУ `target_text` РЕДАГУЄТЬСЯ ПРЯМО В СПИСКУ (list_editable): черга — це ~4 900 рядків
   словника, які треба переглянути ОДИН раз. Відкривати картку заради правки одного слова
   («Черный» → «Чорний») — це 4 900 зайвих переходів. Тут правка і схвалення робляться
   з одного екрана, пачками.

⚡ ПОРЯДОК ЧЕРГИ — не за датою, а за KIND_REVIEW_PRIORITY (TRANSLATION.md §6.3): спершу
   СЛОВНИК (назви характеристик → одиниці → значення → категорії), і лише потім описи.
   Схвалені 600 назв характеристик фіксують термінологію одразу в 10 000 товарів і в глосарії
   для описів. Це найвищий ROI на годину роботи модератора — тому він бачить їх першими.

⚠️ ЗАЛЕЖНІСТЬ: `TranslationEntry.approve()` робить write-back у `*_ru` через
   `translation/services/writeback.py`, а «Перекласти заново» ставить `translate_pending_task`.
   Обидва імпортуються ЗВЕРХУ, а не через importlib: якщо ім'я зникне — Django впаде на
   старті, а не мовчки в проді під руками редактора.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Case, IntegerField, QuerySet, Value, When
from django.http import HttpRequest
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.decorators import display

from translation.models import (
    KIND_REVIEW_PRIORITY,
    GlossaryTerm,
    TranslationEntry,
    TranslationKind,
    TranslationStatus,
)
from translation.tasks import translate_pending_task

STATUS_LABELS: dict[str, str] = {
    TranslationStatus.PENDING: "default",
    TranslationStatus.MACHINE: "warning",  # чекає людину
    TranslationStatus.APPROVED: "success",
    TranslationStatus.REJECTED: "danger",
    TranslationStatus.STALE: "info",  # сайт віддає старий СХВАЛЕНИЙ текст — не аварія
    TranslationStatus.FAILED: "danger",
    TranslationStatus.DO_NOT_TRANSLATE: "default",
}

# Словник vs обсяг (TRANSLATION.md §1): словник перекладається один раз і застосовується
# до всіх товарів — його видно окремим кольором.
DICTIONARY_KINDS = {
    TranslationKind.ATTRIBUTE_NAME,
    TranslationKind.ATTRIBUTE_VALUE,
    TranslationKind.UNIT,
    TranslationKind.CATEGORY_NAME,
}


class ModelNoteFilter(admin.SimpleListFilter):
    """«Де модель сама сумнівалась» — найкращий сигнал, куди модератору дивитись першим."""

    title = "Примітка моделі"
    parameter_name = "has_note"

    def lookups(self, request, model_admin):
        return (("1", "Є примітка / сумнів"), ("0", "Без примітки"))

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.exclude(model_note="")
        if self.value() == "0":
            return queryset.filter(model_note="")
        return queryset


@admin.register(TranslationEntry)
class TranslationEntryAdmin(ModelAdmin):
    list_display = (
        "id",
        "kind_badge",
        "source_preview",
        "target_text",  # ← редагується прямо в списку
        "status_badge",
        "note_flag",
        "engine_model",
        "cost_display",
        "updated_at",
    )
    list_display_links = ("id",)
    list_editable = ("target_text",)
    list_fullwidth = True
    list_per_page = 50

    list_filter = (
        ("kind", ChoicesDropdownFilter),
        ("status", ChoicesDropdownFilter),
        ("target_lang", ChoicesDropdownFilter),
        ModelNoteFilter,
        "content_type",
        ("updated_at", RangeDateTimeFilter),
    )
    list_filter_submit = True
    search_fields = ("source_text", "target_text", "published_text", "field", "batch_id")
    search_help_text = "Текст джерела, переклад, назва поля або batch_id"

    actions = ("approve_selected", "reject_selected", "retranslate_selected")

    readonly_fields = (
        "content_type",
        "object_id",
        "field",
        "kind",
        "source_lang",
        "target_lang",
        "source_text",
        "source_hash",
        "published_text",
        "published_at",
        "validation_errors",
        "engine_model",
        "prompt_version",
        "glossary_version",
        "batch_id",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "edited_by",
        "approved_by",
        "approved_at",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Переклад",
            {
                "description": (
                    "Правимо ЛИШЕ «Переклад (чернетка)». На сайт він потрапляє тільки дією "
                    "«Схвалити» — вона копіює його в «Опубліковано» і в *_ru-колонку моделі."
                ),
                "fields": ("source_text", "target_text", "status", "model_note"),
            },
        ),
        (
            "Опубліковано (те, що бачить сайт)",
            {"fields": ("published_text", "published_at", "approved_by", "approved_at")},
        ),
        (
            "Об'єкт",
            {
                "fields": (
                    ("content_type", "object_id", "field"),
                    ("kind", "source_lang", "target_lang"),
                    "source_hash",
                )
            },
        ),
        (
            "Аудит і витрати",
            {
                "classes": ("collapse",),
                "fields": (
                    ("engine_model", "prompt_version", "glossary_version", "batch_id"),
                    ("input_tokens", "output_tokens", "cost_usd"),
                    "validation_errors",
                    ("edited_by", "created_at", "updated_at"),
                ),
            },
        ),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        # Записи черги створює збирач (сканує моделі), а не людина: інакше ключ
        # (content_type, object_id, field, target_lang) збирається руками і б'ється об unique.
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        """Порядок черги: спершу СЛОВНИК (див. шапку модуля), потім обсяг."""
        priority = Case(
            *[When(kind=kind, then=Value(rank)) for kind, rank in KIND_REVIEW_PRIORITY.items()],
            default=Value(999),
            output_field=IntegerField(),
        )
        return (
            super()
            .get_queryset(request)
            .select_related("content_type", "approved_by")
            .annotate(_review_rank=priority)
            .order_by("_review_rank", "-updated_at", "-id")
        )

    def save_model(self, request: HttpRequest, obj: TranslationEntry, form, change: bool) -> None:
        if "target_text" in getattr(form, "changed_data", []):
            obj.edited_by = request.user
        super().save_model(request, obj, form, change)

    # -- колонки -------------------------------------------------------------

    @display(
        description="Вид",
        ordering="kind",
        label={
            **dict.fromkeys(DICTIONARY_KINDS, "primary"),
            TranslationKind.PRODUCT_NAME: "info",
            TranslationKind.PRODUCT_SHORT_DESCRIPTION: "info",
            TranslationKind.PRODUCT_DESCRIPTION: "info",
            TranslationKind.SEO_TITLE: "default",
            TranslationKind.SEO_DESCRIPTION: "default",
            TranslationKind.PAGE_HTML: "warning",
            TranslationKind.NEWS_HTML: "warning",
            TranslationKind.OTHER: "default",
        },
    )
    def kind_badge(self, obj: TranslationEntry) -> tuple[str, str]:
        return obj.kind, obj.get_kind_display()

    @display(description="Джерело (uk)", ordering="source_text")
    def source_preview(self, obj: TranslationEntry) -> str:
        text = obj.source_text or ""
        short = text if len(text) <= 90 else text[:90] + "…"
        return format_html('<span title="{}">{}</span>', text[:500], short)

    @display(description="Статус", ordering="status", label=STATUS_LABELS)
    def status_badge(self, obj: TranslationEntry) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    @display(description="⚠", boolean=True)
    def note_flag(self, obj: TranslationEntry) -> bool:
        """Модель лишила примітку — тут вона сумнівалась. Дивитись у першу чергу."""
        return bool(obj.model_note)

    @display(description="Вартість", ordering="cost_usd")
    def cost_display(self, obj: TranslationEntry) -> str:
        if not obj.cost_usd:
            return "—"
        return f"${obj.cost_usd:.6f}".rstrip("0").rstrip(".")

    # -- масові дії ----------------------------------------------------------

    @admin.action(description="✅ Схвалити вибрані (опублікувати)")
    def approve_selected(self, request: HttpRequest, queryset: QuerySet) -> None:
        approved = 0
        empty = 0

        for entry in queryset:
            if not entry.target_text.strip():
                # Схвалити порожній переклад = опублікувати порожній RU. Ніколи.
                empty += 1
                continue

            entry.approve(request.user)
            approved += 1

        if approved:
            self.message_user(
                request, f"Схвалено й опубліковано: {approved}.", level=messages.SUCCESS
            )
        if empty:
            self.message_user(
                request,
                f"Пропущено {empty}: порожній переклад схвалити не можна.",
                level=messages.ERROR,
            )

    @admin.action(description="❌ Відхилити вибрані")
    def reject_selected(self, request: HttpRequest, queryset: QuerySet) -> None:
        """
        REJECTED — це «на перепереклад», а не «видалити».
        `published_text` НЕ чіпаємо: якщо там був схвалений текст, сайт має далі показувати
        саме його, а не порожнечу.
        """
        count = queryset.update(status=TranslationStatus.REJECTED, updated_at=timezone.now())
        self.message_user(
            request,
            f"Відхилено: {count}. Опублікований раніше текст на сайті не змінено.",
            level=messages.SUCCESS,
        )

    @admin.action(description="🔄 Перекласти заново")
    def retranslate_selected(self, request: HttpRequest, queryset: QuerySet) -> None:
        """
        Повертає записи в чергу і будить прогін.

        ⚡ `translate_pending_task` НЕ приймає список id — вона сама вибирає з БД усе, що
           чекає (`_pending_qs`). Тому спершу COMMIT нових статусів, і лише потім задача:
           інакше воркер прочитає рядки до коміту й не побачить нічого. Звідси on_commit.
        """
        count = queryset.update(
            status=TranslationStatus.PENDING,
            validation_errors=[],
            updated_at=timezone.now(),
        )
        transaction.on_commit(translate_pending_task.delay)
        self.message_user(
            request,
            f"Повернуто в чергу: {count}. Прогін запущено — він перекладе всі записи "
            "зі статусом «Очікує», не лише вибрані. Оновіть сторінку за кілька хвилин.",
            level=messages.SUCCESS,
        )


@admin.register(GlossaryTerm)
class GlossaryTermAdmin(ModelAdmin):
    """
    Термінологічний словник для system-блоку промпта.

    ⚠️ Блок кешується у Claude (префіксний кеш) і рендериться ДЕТЕРМІНОВАНО. Тому правки тут
       дешеві, але не безкоштовні: `glossary_version` бампається раз на добу вночі, а не на
       кожне збереження — інакше кеш не доживає до кінця батчу.
    """

    list_display = (
        "section",
        "source_term",
        "target_term",
        "note",
        "sort_order",
        "is_active",
        "updated_at",
    )
    list_display_links = ("source_term",)
    list_editable = ("target_term", "note", "sort_order", "is_active")
    list_filter = (("section", ChoicesDropdownFilter), "is_active")
    search_fields = ("source_term", "target_term", "note")
    search_help_text = "Термін uk або ru"
    ordering = ("section", "sort_order", "id")
    list_per_page = 100

    fieldsets = (
        ("Термін", {"fields": ("section", ("source_term", "target_term"), "note")}),
        (
            "Показ",
            {
                "description": (
                    "Неактивний термін не потрапляє в промпт. Для брендів і стоп-перекладів "
                    "лишайте примітку («бренд, лишати як є»)."
                ),
                "fields": (("source_lang", "target_lang"), "sort_order", "is_active"),
            },
        ),
    )
