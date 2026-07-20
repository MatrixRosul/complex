"""Єдиний канал алертів — Telegram (ADR-019).

Досі точок відправки не існувало: `payments.services.alert()` писав `log.critical` з
коментарем «поки в core немає єдиного каналу — точка розширення рівно одна». Це вона.

⚠️ АЛЕРТ НІКОЛИ НЕ ЛАМАЄ ВИКЛИКАЧА. Жодна функція тут не кидає виняток назовні: якщо
   Telegram лежить, впав токен або немає мережі — ми пишемо в лог і повертаємо False.
   Протилежне означало б, що недоступність чужого API скасовує замовлення покупця —
   рівно та помилка, від якої проєкт захищається circuit breaker'ом у Новій Пошті
   (ADR-020). Сповіщення — побічний ефект, а не частина транзакції.

⚠️ ЗВІДКИ БЕРУТЬСЯ КЛЮЧІ. Проєкт послідовно розділяє секрети й налаштування бізнесу:
     * `TELEGRAM_BOT_TOKEN` — env / Docker secret (як NP_API_KEY, LIQPAY_PRIVATE_KEY).
       У БД секретів не тримаємо взагалі.
     * `chat_id` — `SiteSettings.telegram_chat_id`, тобто адмінка. Замовник може
       перенаправити алерти в інший чат БЕЗ деплою. `settings.TELEGRAM_CHAT_ID` —
       лише фолбек для середовищ, де БД ще не наповнена (перший деплой, CI).

⚠️ ЧОМУ СИНХРОННИЙ httpx, А НЕ async. Викликається з Celery-воркера (черга `default`),
   де async не дає нічого, крім ускладнення. З HTTP-запиту цю функцію не викликають —
   для цього є `core.tasks.send_telegram_alert.delay()` (див. модуль поруч).
"""

from __future__ import annotations

import html
import logging
from typing import Any

import httpx
from django.conf import settings

log = logging.getLogger(__name__)

__all__ = [
    "TELEGRAM_MAX_LEN",
    "escape",
    "send_telegram",
    "telegram_configured",
]

#: Telegram ріже повідомлення довші за 4096 символів. Ріжемо самі — з явним маркером,
#: інакше довгий алерт мовчки приходить обрубаним на півслові.
TELEGRAM_MAX_LEN = 4096
_TRUNCATION_MARK = "\n…(обрізано)"

#: Жорсткий таймаут: алерт не має тримати воркер. Ретраї — на рівні Celery-задачі.
_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0)

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def escape(value: Any) -> str:
    """Екранувати значення для parse_mode=HTML.

    Telegram приймає лише підмножину HTML, і будь-який `<` у даних (назва товару,
    коментар покупця) ламає ВСЕ повідомлення: API відповідає 400 і алерт не доходить.
    Тобто екранування тут — не косметика, а умова доставки.
    """
    return html.escape(str(value), quote=False)


def _chat_id() -> str:
    """Куди слати: спершу адмінка, потім env.

    БД може бути недоступна (алерт про падіння самої БД) — тоді просто беремо env,
    а не роняємо відправку разом із усім іншим.
    """
    try:
        from core.models import SiteSettings

        chat_id = (SiteSettings.get_solo().telegram_chat_id or "").strip()
        if chat_id:
            return chat_id
    except Exception:
        log.warning("Telegram: не вдалось прочитати SiteSettings, беру chat_id з env")

    return (getattr(settings, "TELEGRAM_CHAT_ID", "") or "").strip()


def telegram_configured() -> bool:
    """Чи є сенс намагатись відправити.

    Потрібне і для тестів, і щоб у dev не сипались WARNING на кожне замовлення:
    локально ні токена, ні чату немає, і це нормальний стан, а не збій.
    """
    return bool((getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip() and _chat_id())


def send_telegram(text: str, *, disable_preview: bool = True) -> bool:
    """Надіслати повідомлення в Telegram. Повертає успіх, НЕ кидає винятків.

    `text` очікується вже екранованим (див. `escape`) і у форматі HTML —
    жирний `<b>`, моноширинний `<code>`, посилання `<a href>`.
    """
    token = (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = _chat_id()

    if not token or not chat_id:
        # DEBUG, не WARNING: у local/CI це штатна конфігурація.
        log.debug("Telegram не налаштований (немає токена або chat_id) — пропускаю алерт")
        return False

    if len(text) > TELEGRAM_MAX_LEN:
        text = text[: TELEGRAM_MAX_LEN - len(_TRUNCATION_MARK)] + _TRUNCATION_MARK

    try:
        response = httpx.post(
            _API_URL.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": disable_preview,
            },
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        log.error("Telegram: мережева помилка — %s", exc)
        return False

    if response.status_code != 200:
        # Тіло відповіді критично важливе: саме тут Telegram пише «chat not found»
        # (бота не додали в групу) і «can't parse entities» (не екранували HTML).
        log.error(
            "Telegram: HTTP %s — %s",
            response.status_code,
            response.text[:500],
        )
        return False

    return True
