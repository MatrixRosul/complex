"""Фіди — Celery-задачі.

Імена задач ЗБІГАЮТЬСЯ з CELERY_BEAT_SCHEDULE у config/settings/base.py:
    feeds.tasks.generate_hotline_feed   — 6:00 / 14:00 / 22:00
    feeds.tasks.heal_hotline_feed       — щогодини (:17)

🔴 `sync_hotline_tree` РОЗКЛАДУ ЩЕ НЕМАЄ — потрібен запис у beat (раз на тиждень). Див. звіт.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction

from catalog.models import Category
from catalog.services.hotline import set_hotline_for_category as _set_hotline_for_category
from core.models import SiteSettings
from feeds import rubricator, services
from sync.models import SyncRun

log = logging.getLogger(__name__)

__all__ = [
    "generate_hotline_feed",
    "heal_hotline_feed",
    "set_hotline_for_category",
    "sync_hotline_tree",
]


@shared_task(name="feeds.tasks.generate_hotline_feed", queue="default")
def generate_hotline_feed(trigger: str = SyncRun.Trigger.CRON, user_id: int | None = None) -> dict:
    """Генерація за розкладом + позначення поточного артефакту (робить services.set_current)."""
    site = SiteSettings.get_solo()
    if not site.hotline_enabled:
        # Kill-switch. Свідоме «фід вимкнено» — не помилка, тому не алертимо.
        log.info("hotline_feed_disabled: SiteSettings.hotline_enabled=False")
        return {"status": "disabled"}

    artifact = services.generate_hotline_feed(trigger=trigger, user_id=user_id)
    return {
        "status": "ok",
        "artifact_id": artifact.pk,
        "items": artifact.items_count,
        "skipped": artifact.skipped_count,
        "skipped_reasons": artifact.skipped_reasons,
        "no_mpn": artifact.no_mpn_count,
    }


@shared_task(name="feeds.tasks.heal_hotline_feed", queue="default")
def heal_hotline_feed() -> dict:
    """Watchdog: артефакт старший 24 год АБО файлу на диску немає → регенерація.

    🔴 Фід публікується 7 днів. Якщо за цей час не завантажено оновлений — публікація
       ПРИПИНЯЄТЬСЯ і весь каталог зникає з майданчика. Мовчки. Тому watchdog, а не надія на beat.
    """
    site = SiteSettings.get_solo()
    if not site.hotline_enabled:
        return {"status": "disabled"}

    stale, reason = services.feed_is_stale()
    if not stale:
        return {"status": "fresh"}

    log.warning("hotline_feed_heal reason=%s", reason)
    artifact = services.generate_hotline_feed(trigger=SyncRun.Trigger.HEAL)
    return {"status": "healed", "reason": reason, "artifact_id": artifact.pk}


@shared_task(name="feeds.tasks.set_hotline_for_category", queue="default")
def set_hotline_for_category(
    category_id: int,
    value: bool,
    include_descendants: bool = True,
    user_id: int | None = None,
) -> dict:
    """Масове вмикання/вимикання Hotline на категорію ТА ВСІ ЇЇ ПІДКАТЕГОРІЇ (вимога ТЗ).

    Асинхронно, бо 10k UPDATE не мають жити в HTTP-запиті адмінки.

    ⚡ Заразом переставляється `Category.hotline_enabled_default` — його успадковують НОВІ товари
       при створенні в синку. Без цього синхронізація 4×/добу створювала б у цій же категорії
       нові товари з `hotline_enabled=False`, і категорія «протікала» б повз фід НАЗАВЖДИ
       (саме так і було в v1).
    """
    category = Category.objects.get(pk=category_id)
    products, categories = _set_hotline_for_category(
        category, enabled=value, include_descendants=include_descendants
    )
    log.info(
        "hotline_bulk_set category=%s value=%s products=%s categories=%s user=%s",
        category_id,
        value,
        products,
        categories,
        user_id,
    )
    return {
        "status": "ok",
        "category_id": category_id,
        "value": value,
        "products": products,
        "categories": categories,
    }


@shared_task(name="feeds.tasks.sync_hotline_tree", queue="default")
def sync_hotline_tree() -> dict:
    """Раз на тиждень тягне рубрикатор Hotline (cp1251-«CSV» з відступами).

    Без цієї таблиці ЖОДЕН товар не потрапить у фід: Category.hotline_category порожній →
    skip `no_hotline_category` на всьому каталозі.
    """
    text = rubricator.fetch_tree()
    nodes = rubricator.parse_tree(text)
    with transaction.atomic():
        result = rubricator.import_tree(nodes)
    return {"status": "ok", **result}
