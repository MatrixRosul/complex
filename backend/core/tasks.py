"""Core — Celery-задачі загального призначення.

Поки тут лише доставка алертів. Модуль створено саме як `core.tasks`, бо на це ім'я вже
посилається коментар у `config/settings/base.py::CELERY_BEAT_SCHEDULE`.

⚠️ РОЗКЛАДУ В BEAT ЦЕЙ МОДУЛЬ НЕ ДОДАЄ. У `base.py` описаний реальний баг проєкту:
   у брокер публікувалось ім'я задачі, якої воркер не знав, і запис мовчки відкидався
   як unregistered. Тутешні задачі викликаються лише через `.delay()` з коду й
   підхоплюються `autodiscover_tasks()` — beat їх не торкається. Watchdog «давно не було
   синку», згаданий у тому TODO, — окрема задача й окреме рішення.
"""

from __future__ import annotations

import logging

from celery import shared_task

from core.alerts import send_telegram

log = logging.getLogger(__name__)

__all__ = ["send_telegram_alert"]


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    retry_backoff=True,
    queue="default",
    # ⚠️ acks_late=False СВІДОМО (на відміну від sync/payments). Алерт — не грошова
    #    операція: краще втратити повідомлення при падінні воркера, ніж надіслати його
    #    двічі. Дубль алерту про замовлення = менеджер дзвонить покупцеві двічі.
    acks_late=False,
)
def send_telegram_alert(self, text: str) -> bool:
    """Надіслати готове (вже екрановане) HTML-повідомлення в Telegram.

    Ретраїмо лише транспортну невдачу. `send_telegram` не кидає винятків, тому рішення
    про ретрай приймається за повернутим False — але ТІЛЬКИ якщо канал узагалі
    налаштований: інакше в dev кожен алерт молотив би три спроби на порожньому токені.
    """
    from core.alerts import telegram_configured

    if not telegram_configured():
        return False

    if send_telegram(text):
        return True

    log.warning("Telegram-алерт не доставлено, спроба %s", self.request.retries + 1)
    raise self.retry(exc=RuntimeError("Telegram sendMessage failed"))
