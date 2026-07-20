"""ШІ-збір характеристик — черга пропозицій на перегляд.

🔴 ГОЛОВНЕ ПРАВИЛО (INPUTS + рішення замовника): ШІ НІКОЛИ не пише в каталог напряму.
Збирач кладе пропозицію сюди зі статусом NEEDS_REVIEW → людина дивиться диф → натискає
«Застосувати» → лише тоді `apply_job` пише `ProductAttributeValue(source=ai)`. Це те, що
рятує від хаосу («Sharp» vs «SHARP») і від вигаданих характеристик у каталозі.

Також: у підсумок беруться ЛИШЕ значення, підтверджені точним кодом моделі (spec.exact_code
!= False). Непідтверджене (з сусідньої версії) лишається в `proposed_specs` для аудиту, але
`apply` його пропускає — краще порожнє поле, ніж чуже значення.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class SpecHarvestJob(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "У черзі"
        RUNNING = "running", "Виконується"
        NEEDS_REVIEW = "needs_review", "На перегляд"
        APPLIED = "applied", "Застосовано"
        REJECTED = "rejected", "Відхилено"
        FAILED = "failed", "Помилка"

    class MatchConfidence(models.TextChoices):
        HIGH = "high", "Висока"
        MEDIUM = "medium", "Середня"
        LOW = "low", "Низька"

    product = models.ForeignKey(
        "catalog.Product",
        verbose_name="Товар",
        on_delete=models.CASCADE,
        related_name="harvest_jobs",
    )
    category_key = models.CharField(
        "Шаблон категорії",
        max_length=40,
        default="dishwasher",
        help_text="Ключ у aispecs.category_specs.TEMPLATES — за ним мапляться характеристики.",
    )
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.NEEDS_REVIEW,
        db_index=True,
    )

    # --- матчинг товару з джерелом ---
    model_code = models.CharField("Код моделі", max_length=120, blank=True)
    matched = models.BooleanField("Точний збіг коду", default=False)
    match_confidence = models.CharField(
        "Впевненість збігу", max_length=8, choices=MatchConfidence.choices, blank=True
    )
    source_type = models.CharField("Тип джерела", max_length=20, blank=True)
    primary_source_url = models.URLField("Головне джерело", max_length=1000, blank=True)

    # --- пропозиція (сирий вивід збирача) ---
    proposed_specs = models.JSONField("Запропоновані характеристики", default=list, blank=True)
    proposed_programs = models.JSONField("Перелік програм", default=list, blank=True)
    proposed_description = models.TextField("Запропонований опис", blank=True)
    warnings = models.JSONField("Застереження збирача", default=list, blank=True)

    # --- застосування / аудит ---
    applied_count = models.PositiveSmallIntegerField("Записано полів", default=0)
    error = models.TextField("Помилка", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Створив",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Переглянув",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reviewed_at = models.DateTimeField("Переглянуто", null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "ШІ-збір характеристик"
        verbose_name_plural = "ШІ-збір характеристик"
        indexes = [models.Index(fields=["status", "-created_at"], name="harvest_status_idx")]

    def __str__(self) -> str:
        return f"{self.product_id}: {self.model_code} [{self.status}]"

    def confirmed_specs(self) -> list[dict]:
        """Лише підтверджене точним кодом і з непорожнім значенням — те, що піде в каталог."""
        out = []
        for s in self.proposed_specs or []:
            if s.get("exact_code") is False:
                continue
            if s.get("num") is None and not (s.get("text") or "").strip():
                continue
            out.append(s)
        return out

    def unconfirmed_specs(self) -> list[dict]:
        return [
            s
            for s in (self.proposed_specs or [])
            if s.get("exact_code") is False
            and (s.get("num") is not None or (s.get("text") or "").strip())
        ]
