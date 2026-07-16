"""Адмінка черги ШІ-збору — перегляд пропозиції й застосування в каталог.

Менеджер тут робить рівно два рухи: дивиться диф (що знайшлось, з якого джерела, з якою
впевненістю) і натискає «Застосувати» або «Відхилити». Створювати джоби руками не можна —
їх кладе збирач (команда import_harvest).
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpRequest
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from unfold.admin import ModelAdmin
from unfold.decorators import action, display

from aispecs.category_specs import get_template
from aispecs.models import SpecHarvestJob
from aispecs.services import ApplyError, apply_job, reject_job

_STATUS_COLORS = {
    "queued": "info",
    "running": "info",
    "needs_review": "warning",
    "applied": "success",
    "rejected": "danger",
    "failed": "danger",
}
_CONF_COLORS = {"high": "success", "medium": "warning", "low": "danger"}


@admin.register(SpecHarvestJob)
class SpecHarvestJobAdmin(ModelAdmin):
    list_display = (
        "product_link",
        "model_code",
        "status_badge",
        "confidence_badge",
        "confirmed_count",
        "created_at",
    )
    list_filter = ("status", "match_confidence", "category_key", "source_type")
    search_fields = ("product__name_uk", "product__sku", "model_code")
    list_select_related = ("product",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    actions = ("act_apply", "act_reject")
    actions_detail = ("detail_apply", "detail_reject")

    readonly_fields = (
        "product",
        "category_key",
        "model_code",
        "matched",
        "match_confidence",
        "source_type",
        "primary_source_url",
        "proposed_table",
        "proposed_description",
        "warnings_list",
        "applied_count",
        "reviewed_by",
        "reviewed_at",
        "error",
    )
    fieldsets = (
        (
            "Збіг товару з джерелом",
            {
                "fields": (
                    "product",
                    "model_code",
                    ("matched", "match_confidence"),
                    ("source_type", "primary_source_url"),
                ),
                "description": "🔴 Значення з «сусідньої версії» (не підтверджені точним кодом) "
                "у каталог НЕ пишуться — вони показані нижче сірим і при застосуванні пропускаються.",
            },
        ),
        ("Пропозиція — характеристики", {"fields": ("proposed_table",)}),
        ("Пропозиція — опис (укр)", {"fields": ("proposed_description",)}),
        ("Застереження збирача", {"classes": ("collapse",), "fields": ("warnings_list",)}),
        (
            "Статус",
            {"fields": ("status", "applied_count", ("reviewed_by", "reviewed_at"), "error")},
        ),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False  # джоби створює збирач (import_harvest), не людина руками

    # ---- колонки списку ----
    @display(description="Товар", ordering="product__name_uk")
    def product_link(self, obj: SpecHarvestJob) -> str:
        return str(obj.product)

    @display(description="Статус", label=_STATUS_COLORS, ordering="status")
    def status_badge(self, obj: SpecHarvestJob) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    @display(description="Збіг", label=_CONF_COLORS)
    def confidence_badge(self, obj: SpecHarvestJob) -> tuple[str, str]:
        return obj.match_confidence, obj.get_match_confidence_display() or "—"

    @display(description="Підтверджено")
    def confirmed_count(self, obj: SpecHarvestJob) -> str:
        return f"{len(obj.confirmed_specs())} хар."

    # ---- диф на картці ----
    @display(description="Що буде записано")
    def proposed_table(self, obj: SpecHarvestJob) -> SafeString:
        template = get_template(obj.category_key)
        rows = []
        for spec in obj.proposed_specs or []:
            key = spec.get("key", "")
            label = template.get(key, (key,))[0]
            val = spec.get("num")
            val = str(val) if val is not None else (spec.get("text") or "")
            conf = spec.get("confidence", "")
            src = spec.get("source_url", "")
            if spec.get("exact_code") is False:
                rows.append(
                    format_html(
                        '<tr style="opacity:.5"><td>{}</td><td>{}</td>'
                        '<td colspan="2"><i>не підтверджено точним кодом — пропущено</i></td></tr>',
                        label, val,
                    )
                )
            else:
                link = format_html('<a href="{}" target="_blank">джерело ↗</a>', src) if src else ""
                rows.append(
                    format_html(
                        "<tr><td><b>{}</b></td><td>{}</td><td>{}</td><td>{}</td></tr>",
                        label, val, conf, link,
                    )
                )
        progs = ", ".join(str(p) for p in (obj.proposed_programs or []))
        if progs:
            rows.append(
                format_html(
                    "<tr><td><b>Перелік програм</b></td><td colspan=3>{}</td></tr>", progs
                )
            )
        if not rows:
            return format_html("<i>Порожньо.</i>")
        return format_html(
            '<table style="border-collapse:collapse;width:100%">'
            '<thead><tr style="text-align:left;border-bottom:1px solid #ccc">'
            "<th>Характеристика</th><th>Значення</th><th>Впевненість</th><th>Джерело</th></tr></thead>"
            "<tbody>{}</tbody></table>",
            format_html_join("", "{}", ((r,) for r in rows)),
        )

    @display(description="Застереження")
    def warnings_list(self, obj: SpecHarvestJob) -> SafeString:
        if not obj.warnings:
            return format_html("—")
        return format_html_join(
            "", "<div style='margin:4px 0'>⚠ {}</div>", ((w,) for w in obj.warnings)
        )

    # ---- дії ----
    def _apply_many(self, request: HttpRequest, queryset) -> None:
        ok = fail = 0
        for job in queryset:
            try:
                apply_job(job, request.user)
                ok += 1
            except (ApplyError, Exception) as exc:  # noqa: BLE001 — показуємо будь-яку помилку
                fail += 1
                messages.error(request, f"{job.product}: {exc}")
        if ok:
            self.message_user(request, f"Застосовано джоб: {ok}.", messages.SUCCESS)
        if fail:
            self.message_user(request, f"Не вдалося: {fail}.", messages.WARNING)

    @admin.action(description="Застосувати — записати в каталог")
    def act_apply(self, request: HttpRequest, queryset) -> None:
        self._apply_many(request, queryset)

    @admin.action(description="Відхилити")
    def act_reject(self, request: HttpRequest, queryset) -> None:
        n = 0
        for job in queryset:
            reject_job(job, request.user)
            n += 1
        self.message_user(request, f"Відхилено: {n}.", messages.SUCCESS)

    @action(description="Застосувати", icon="check")
    def detail_apply(self, request: HttpRequest, object_id: int):
        job = SpecHarvestJob.objects.get(pk=object_id)
        try:
            n = apply_job(job, request.user)
            self.message_user(request, f"Записано {n} полів у «{job.product}».", messages.SUCCESS)
        except Exception as exc:  # noqa: BLE001
            self.message_user(request, f"Помилка: {exc}", messages.ERROR)

    @action(description="Відхилити", icon="close")
    def detail_reject(self, request: HttpRequest, object_id: int):
        job = SpecHarvestJob.objects.get(pk=object_id)
        reject_job(job, request.user)
        self.message_user(request, "Відхилено.", messages.SUCCESS)
