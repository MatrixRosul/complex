"""
Конфіг ШІ-асистента: модель, параметри виклику, ліміти, бюджет.

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ ⚠️ ЧОМУ ТУТ ВЛАСНИЙ _resolve(), А НЕ translation.conf.spec()                          ║
║                                                                                      ║
║ translation.conf.spec() (translation/conf.py:159) на невідомій моделі МОВЧКИ віддає   ║
║ ModelSpec Sonnet-5:                                                                  ║
║     return MODELS.get(model) or MODELS[DEFAULT_BULK_MODEL]                            ║
║                                                                                      ║
║ Для перекладу це свідомий фолбек. Для нас — прихована фінансова помилка: ASSISTANT_   ║
║ MODEL з одруком у .env → усі витрати рахуються за цінами Sonnet ($3/$15) там, де      ║
║ реально працює Opus ($5/$25), і місячний hard-cap спрацює приблизно ніколи. Помилки   ║
║ при цьому НЕ БУДЕ — ні в лозі, ні в коді.                                            ║
║                                                                                      ║
║ Тому: прайс-лист і compute_cost() беремо з translation.conf (вони публічні, коректні  ║
║ і не варті дубляжу), а ВАЛІДАЦІЮ моделі робимо самі — з гучним ValueError.            ║
║ Краще не піднятись, ніж піднятись і брехати про гроші.                                ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

Параметри виклику (звірено з докою Claude API 14.07.2026):
  * thinking = adaptive + display="summarized". Дефолт display — "omitted", тобто
    thinking-блоки приходять ПОРОЖНІ, і стрім мовчить рівно стільки, скільки модель думає.
    Нам потрібна подія «лисичка думає», а не пауза.
  * effort = "medium", НЕ "low": Opus 4.8 і без того недо-тягується до інструментів,
    низький effort це посилює.
  * max_tokens = 16000. max_tokens — це стеля на thinking + текст + tool_use РАЗОМ.
    4096 дало б stop_reason="max_tokens" і обрізану на пів-речення відповідь. Стрімимо —
    отже платимо за фактичні токени, а не за стелю.
  * budget_tokens / temperature / top_p / top_k — на Opus 4.8 це 400. Не використовуємо.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation

from django.conf import settings

from translation.conf import MODELS, ModelSpec, compute_cost

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MODEL",
    "EFFORT",
    "MAX_ITERATIONS",
    "MAX_TOKENS",
    "THINKING",
    "compute_cost",
    "max_input_chars",
    "max_messages",
    "min_cache_prefix",
    "model",
    "monthly_budget_usd",
    "spec",
]

# ---------------------------------------------------------------------------
# Модель
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-opus-4-8"


def _env(name: str, default: str = "") -> str:
    """Читання конфіга: спершу оточення (django-environ кладе .env у os.environ),
    потім settings. Окремих ASSISTANT_*-налаштувань у config/settings/base.py немає —
    і це нормально: правити спільні файли цьому модулю не можна."""
    raw = os.environ.get(name) or getattr(settings, name, "")
    return str(raw or "").strip() or default


def _resolve(configured: str) -> str:
    """Приймаємо конфіг — але тільки той, ціни якого ми справді знаємо.

    Порожньо → дефолт. Невідома модель → ValueError, і додаток не піднімається.
    Мовчазної підміни (як у translation.conf.spec) тут немає СВІДОМО: див. шапку модуля.
    """
    configured = (configured or "").strip()
    if not configured:
        return DEFAULT_MODEL
    if configured in MODELS:
        return configured
    known = ", ".join(sorted(MODELS))
    raise ValueError(
        f"ASSISTANT_MODEL={configured!r} — невідома модель. Для неї немає ні цін, ні "
        f"min_cache_prefix, тож вартість чату рахувалась би за чужим прайсом. "
        f"Відомі моделі: {known}. Полагодь ASSISTANT_MODEL у .env."
    )


def model() -> str:
    """Модель асистента. Не кешуємо: підміна settings/env у тестах має спрацьовувати."""
    return _resolve(_env("ASSISTANT_MODEL", DEFAULT_MODEL))


def spec(model_name: str | None = None) -> ModelSpec:
    """ModelSpec (ціни, min_cache_prefix) — з валідацією, а не з тихим фолбеком."""
    return MODELS[_resolve(model_name or "")] if model_name else MODELS[model()]


def min_cache_prefix(model_name: str | None = None) -> int:
    """Мінімальний КЕШОВАНИЙ префікс, токенів (Opus 4.8 — 4096).

    Коротший префікс кеш просто НЕ створює: cache_creation_input_tokens=0, без жодної
    помилки. Тому cache_control ставимо лише коли поріг перекрито — і міряємо його через
    messages.count_tokens(), а не len(рядка).
    """
    return spec(model_name).min_cache_prefix


# ---------------------------------------------------------------------------
# Параметри виклику (див. шапку модуля)
# ---------------------------------------------------------------------------

MAX_TOKENS = 16000
EFFORT = "medium"
THINKING: dict[str, str] = {"type": "adaptive", "display": "summarized"}

# Скільки разів модель може сходити в інструменти в межах одного повідомлення.
MAX_ITERATIONS = 6


# ---------------------------------------------------------------------------
# Ліміти й бюджет
# ---------------------------------------------------------------------------

DEFAULT_MONTHLY_BUDGET_USD = Decimal("50")
DEFAULT_MAX_MESSAGES = 30
DEFAULT_MAX_INPUT_CHARS = 1000


def monthly_budget_usd() -> Decimal:
    """Hard-cap витрат на місяць. Публічний LLM-ендпоінт без стелі — це відкритий гаманець.

    ⚠️ SiteSettings.ai_monthly_budget_usd і AI_MONTHLY_BUDGET_USD свідомо НЕ читаємо:
    вони зараз не є джерелом правди ні для чого, і зшивати з ними бюджет асистента —
    значить успадкувати чужий напівживий контракт.
    """
    raw = _env("ASSISTANT_MONTHLY_BUDGET_USD")
    if not raw:
        return DEFAULT_MONTHLY_BUDGET_USD
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        log.warning(
            "ASSISTANT_MONTHLY_BUDGET_USD=%r — не число. Використовую $%s.",
            raw,
            DEFAULT_MONTHLY_BUDGET_USD,
        )
        return DEFAULT_MONTHLY_BUDGET_USD


def _int_env(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warning("%s=%r — не ціле число. Використовую %d.", name, raw, default)
        return default
    if value <= 0:
        log.warning("%s=%d — має бути > 0. Використовую %d.", name, value, default)
        return default
    return value


def max_messages() -> int:
    """Стеля повідомлень на сесію: після неї чат ввічливо пропонує менеджера."""
    return _int_env("ASSISTANT_MAX_MESSAGES", DEFAULT_MAX_MESSAGES)


def max_input_chars() -> int:
    """Стеля довжини одного питання. Захист від «вставив у чат весь прайс»."""
    return _int_env("ASSISTANT_MAX_INPUT_CHARS", DEFAULT_MAX_INPUT_CHARS)
