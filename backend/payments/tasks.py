"""Платежі — Celery-задачі.

🔴 `reconcile_pending_payments` (beat, кожні 5 хв) — це не «додатковий комфорт», а умова
   існування магазину. Вебхук може НЕ ДІЙТИ: бекенд лежав 10 хв на деплої, Caddy віддав
   502, LiqPay вичерпав ретраї. Без звірки: гроші з покупця зняті, Payment ВІЧНО PENDING,
   Order ВІЧНО «очікує оплати».

   Розклад уже є в `config/settings/base.py::CELERY_BEAT_SCHEDULE`:
       "reconcile-pending-payments": crontab(minute="*/5")
       "purge-webhook-events":       crontab(minute=30, hour=4)

🔴 Проводиться все ЧЕРЕЗ `services.apply_payment_status()` — ту саму функцію, що й вебхук.
   Ніякого другого мапінгу статусів (INTEGRATIONS §3.6).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from orders.models import Order
from payments.models import Payment, PaymentWebhookEvent
from payments.providers import ProviderNotFound, get_provider
from payments.services import alert, apply_payment_status

log = logging.getLogger(__name__)

#: Фора вебхуку: молодші платежі не смикаємо — вебхук майже завжди приходить за секунди.
RECONCILE_MIN_AGE = timedelta(minutes=5)
#: Старші за це — провайдер уже нічого не розкаже, це протермінований чекаут.
RECONCILE_MAX_AGE = timedelta(hours=48)
#: Скільки платежів опитуємо за один прогін (щоб не впертись у ліміти LiqPay).
RECONCILE_BATCH = 200
#: Ретеншн журналу вебхуків.
WEBHOOK_RETENTION_DAYS = 90


@shared_task(bind=True, max_retries=3, default_retry_delay=60, queue="default")
def reconcile_pending_payments(self, limit: int = RECONCILE_BATCH) -> dict[str, int]:
    """Добрати статуси, яких не принесли вебхуки. Beat: кожні 5 хвилин."""
    now = timezone.now()
    stats = {"polled": 0, "applied": 0, "not_found": 0, "errors": 0, "expired": 0}

    stale = list(
        Payment.objects.filter(
            status__in=Payment.RECONCILABLE_STATUSES,
            created_at__lt=now - RECONCILE_MIN_AGE,
            created_at__gte=now - RECONCILE_MAX_AGE,
        ).order_by("created_at")[:limit]
    )

    for payment in stale:
        try:
            provider = get_provider(payment.provider)
            status = provider.get_status(str(payment.reference))
        except ProviderNotFound:
            # Покупець так і не відкрив чекаут — у провайдера рахунку немає. Це НЕ помилка
            # і НЕ «платіж провалився»: він ще може заплатити. Дочекається EXPIRED (48 год).
            stats["not_found"] += 1
            _touch(payment, now)
            continue
        except Exception as exc:  # падіння одного платежу не валить прогін
            log.warning("reconcile: %s не опитався: %s", payment.reference, exc)
            stats["errors"] += 1
            continue

        stats["polled"] += 1
        result = apply_payment_status(payment, status, source="reconcile")
        if result == PaymentWebhookEvent.Result.APPLIED:
            stats["applied"] += 1
        _touch(payment, now)

    stats["expired"] = expire_stale_payments(now)

    if stats["polled"] or stats["expired"]:
        log.info("reconcile: %s", stats)
    return stats


def _touch(payment: Payment, now) -> None:
    Payment.objects.filter(pk=payment.pk).update(last_polled_at=now)


@transaction.atomic
def expire_stale_payments(now=None) -> int:
    """Платіж старший 48 год і досі не оплачений → EXPIRED + алерт менеджеру.

    ⚠️ ТЗ каже «→ FAILED», модель (`Payment.Status.EXPIRED`, коментар у `payments/models.py`)
    каже «>48 год у PENDING (reconcile)». Беремо EXPIRED — це те саме «не оплачено», але
    відрізняє «покупець не заплатив» від «банк відмовив», і саме так це описано в моделі,
    яку правити не можна. На ЗАМОВЛЕННІ це однаково `payment_status = FAILED`.
    """
    now = now or timezone.now()
    cutoff = now - RECONCILE_MAX_AGE

    qs = Payment.objects.select_for_update().filter(
        status__in=(Payment.Status.CREATED, Payment.Status.PENDING),
        created_at__lt=cutoff,
    )
    doomed = list(qs.values_list("id", "order_id", "reference"))
    if not doomed:
        return 0

    Payment.objects.filter(id__in=[p[0] for p in doomed]).update(
        status=Payment.Status.EXPIRED, updated_at=now
    )
    Order.objects.filter(id__in=[p[1] for p in doomed]).exclude(
        payment_status=Order.PaymentStatus.PAID
    ).update(payment_status=Order.PaymentStatus.FAILED, updated_at=now)

    alert(
        "Платежі протерміновано (>48 год без оплати)",
        count=len(doomed),
        references=[str(p[2]) for p in doomed[:20]],
    )
    return len(doomed)


@shared_task(queue="default")
def purge_webhook_events(days: int = WEBHOOK_RETENTION_DAYS) -> int:
    """Ретеншн журналу вебхуків: `raw_body` потрібен лише для форензики (90 днів)."""
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = PaymentWebhookEvent.objects.filter(created_at__lt=cutoff).delete()
    if deleted:
        log.info("purge_webhook_events: видалено %s подій старших %s днів", deleted, days)
    return deleted
