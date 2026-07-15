"""
Celery-задачі перекладу.

Черга — `ai` (-c 1): config/settings/base.py:277 вже маршрутизує `translation.tasks.*`
туди. Конкурентність 1 — свідомо: Batch API і так асинхронний, а паралельні воркери
лише швидше з'їдять rate-limit і місячний бюджет.

Пайплайн (TRANSLATION.md §6.2):

    sync_prices / sync_specs
            ↓
    collect_dictionary_task   ← словник має бути схвалений ПЕРШИМ: 600 назв характеристик
            ↓                   фіксують термінологію в 10 000 товарів і в глосарії
    collect_targets_task
            ↓
    translate_pending_task    → MACHINE (чекає людину) або FAILED
            ↓
    retry_failed_task         → 1 спроба на Opus 4.8 → далі в «Потребує уваги»
            ↓
    apply_approved_task       → тільки СХВАЛЕНЕ → `<field>_ru`

    rebuild_glossary_task     → нічний cron, бампає glossary_version
"""

from __future__ import annotations

import logging

from celery import shared_task

from translation import conf
from translation.client import BudgetExceeded
from translation.services import (
    apply_approved,
    collect_dictionary,
    collect_targets,
    estimate_queue,
    rebuild_glossary,
    translate_pending,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def translate_pending_task(
    self,
    kind: str | None = None,
    limit: int | None = 500,
    use_batch: bool = True,
    only_failed: bool = False,
):
    """Прогін черги. За замовчуванням — через Batch API (−50%)."""
    try:
        return dict(
            translate_pending(kind=kind, limit=limit, use_batch=use_batch, only_failed=only_failed)
        )
    except BudgetExceeded as exc:
        # НЕ ретраїмо: бюджет — це не тимчасова помилка, це рішення людини.
        logger.error("TRANSLATION: %s", exc)
        raise
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@shared_task
def retry_failed_task(kind: str | None = None, limit: int | None = 200):
    """Одна спроба перекласти те, що не пройшло валідацію — на сильнішій моделі.

    _pick_model() сам підніме модель до Opus 4.8, бо в записі непорожній validation_errors.
    """
    return dict(translate_pending(kind=kind, limit=limit, only_failed=True, use_batch=True))


@shared_task
def apply_approved_task(kind: str | None = None, limit: int | None = None):
    """Схвалене → вітрина. Ідемпотентно, самолікування після відкатів/падінь воркера."""
    return {"written": apply_approved(kind=kind, limit=limit)}


@shared_task
def collect_dictionary_task(limit: int | None = None):
    """Весь словник у чергу. ~4 900 рядків на каталог із 10 000 товарів."""
    return dict(collect_dictionary(limit=limit))


@shared_task
def collect_targets_task(kinds: list[str] | None = None, limit: int | None = None):
    return dict(collect_targets(set(kinds) if kinds else None, limit=limit))


@shared_task
def rebuild_glossary_task():
    """Нічний cron. Бампає glossary_version — а не кожне схвалення терміна, інакше кеш
    Claude не доживе до кінця батчу."""
    block = rebuild_glossary()
    return {"chars": len(block)}


@shared_task
def budget_report_task():
    """Скільки коштуватиме те, що зараз висить у черзі. Для дашборд-віджета."""
    est = estimate_queue()
    return {
        "entries": est.total_entries,
        "requests": est.total_requests,
        "cost_usd": str(est.total_cost),
        "budget_usd": str(conf.monthly_budget_usd()),
    }
