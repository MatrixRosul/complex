"""
Конфіг модуля перекладу: моделі, ціни, ліміти, бюджет.

Джерело правди — docs/research/TRANSLATION.md §5, §7 (звірено з докою Claude API 13.07.2026).

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ ⚠️ ЧОМУ ТУТ Є ВАЛІДАЦІЯ МОДЕЛІ, А НЕ ПРОСТО settings.ANTHROPIC_TRANSLATION_MODEL      ║
║                                                                                      ║
║ У config/settings/base.py:399-400 дефолти застарілі:                                  ║
║     ANTHROPIC_TRANSLATION_MODEL = "claude-sonnet-4-5"                                 ║
║     ANTHROPIC_HARVEST_MODEL     = "claude-opus-4-5"                                   ║
║                                                                                      ║
║ На claude-sonnet-4-5 дизайн із TRANSLATION.md ФІЗИЧНО не працює:                      ║
║   • немає structured outputs (`output_config.format`) → модель поверне JSON у тексті   ║
║     «як вийде», і парсер розсиплеться на першому ж описі з лапками;                    ║
║   • немає `output_config.effort` → 400;                                               ║
║   • інший (старий) токенізатор → уся оцінка вартості з §7 не сходиться.               ║
║                                                                                      ║
║ Тому: значення з settings ПРИЙМАЄТЬСЯ, але спершу перевіряється по MODELS. Якщо модель ║
║ не вміє потрібного — гучний WARN у лог і фолбек на правильну. Мовчазної деградації     ║
║ (коли переклад начебто працює, а насправді пише сміття в каталог) тут не буде.        ║
║ Правильні значення треба покласти в .env / .env.example (див. звіт агента).           ║
╚══════════════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)

PROMPT_VERSION = "p1"


# ---------------------------------------------------------------------------
# Прайс-лист і можливості моделей
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    """Ціни — $ за 1 000 000 токенів (TRANSLATION.md §7.1)."""

    id: str
    input_usd: Decimal
    output_usd: Decimal
    min_cache_prefix: int  # мінімальний КЕШОВАНИЙ префікс, токенів
    structured_outputs: bool  # output_config.format
    effort: bool  # output_config.effort
    thinking_disabled_ok: bool  # thinking={"type": "disabled"} приймається

    # Інтро-ціна (Sonnet 5 діє до 31.08.2026 включно), після — базова.
    intro_input_usd: Decimal | None = None
    intro_output_usd: Decimal | None = None
    intro_until: dt.date | None = None

    def prices(self, on: dt.date | None = None) -> tuple[Decimal, Decimal]:
        on = on or dt.date.today()
        if self.intro_until and self.intro_input_usd is not None and on <= self.intro_until:
            return self.intro_input_usd, self.intro_output_usd  # type: ignore[return-value]
        return self.input_usd, self.output_usd


MODELS: dict[str, ModelSpec] = {
    "claude-opus-4-8": ModelSpec(
        id="claude-opus-4-8",
        input_usd=Decimal("5"),
        output_usd=Decimal("25"),
        min_cache_prefix=4096,
        structured_outputs=True,
        effort=True,
        thinking_disabled_ok=True,
    ),
    "claude-sonnet-5": ModelSpec(
        id="claude-sonnet-5",
        input_usd=Decimal("3"),
        output_usd=Decimal("15"),
        # ⚠️ Для Sonnet 5 офіційна таблиця мінімального префікса НЕ підтверджена
        #    (TRANSLATION.md §7.6). Плануємо консервативно на 4096 — тоді поріг перекрито
        #    для будь-якої з моделей, і кеш або спрацює, або ми свідомо не ставимо breakpoint.
        min_cache_prefix=4096,
        structured_outputs=True,
        effort=True,
        thinking_disabled_ok=True,
        intro_input_usd=Decimal("2"),
        intro_output_usd=Decimal("10"),
        intro_until=dt.date(2026, 8, 31),
    ),
    "claude-haiku-4-5": ModelSpec(
        id="claude-haiku-4-5",
        input_usd=Decimal("1"),
        output_usd=Decimal("5"),
        min_cache_prefix=4096,
        structured_outputs=True,
        effort=True,
        thinking_disabled_ok=True,
    ),
}

# Моделі, які ми ЗНАЄМО і які НЕ підходять під цей пайплайн. Тримаємо їх у мапі явно,
# щоб у лог падало осмислене пояснення, а не «unknown model».
UNSUPPORTED: dict[str, str] = {
    "claude-sonnet-4-5": "немає structured outputs і output_config.effort; старий токенізатор",
    "claude-opus-4-5": "немає output_config.effort у потрібному вигляді; застаріла для перекладу",
    "claude-sonnet-4-6": "структуровані виходи не гарантовані для цього пайплайна",
    "claude-fable-5": "удвічі дорожча за Opus; thinking вимкнути не можна (400)",
}

DEFAULT_BULK_MODEL = "claude-sonnet-5"  # обсяг: назви, описи, SEO
DEFAULT_DICT_MODEL = "claude-opus-4-8"  # СЛОВНИК + ретраї після FAILED/REJECTED


@lru_cache(maxsize=32)
def _resolve(configured: str, default: str, purpose: str) -> str:
    """Приймаємо конфіг, але не даємо собі вистрелити в ногу застарілою моделлю.

    lru_cache — щоб WARN був ОДИН на процес, а не по разу на кожен запис черги
    (інакше оцінка на 10 000 товарів топить лог у 10 000 однакових рядків).
    Ключ кеша — самі аргументи, тому підміна settings у тестах працює коректно.
    """
    configured = (configured or "").strip()
    if not configured:
        return default
    if configured in MODELS:
        return configured
    reason = UNSUPPORTED.get(configured, "модель невідома цьому модулю")
    logger.warning(
        "TRANSLATION: модель %r (%s) не підходить: %s. Використовую %r. "
        "Полагодь ANTHROPIC_TRANSLATION_MODEL у .env — див. TRANSLATION.md §7.2.",
        configured,
        purpose,
        reason,
        default,
    )
    return default


def bulk_model() -> str:
    return _resolve(
        getattr(settings, "ANTHROPIC_TRANSLATION_MODEL", ""), DEFAULT_BULK_MODEL, "обсяг"
    )


def dict_model() -> str:
    # Окремої настройки в settings немає (правити settings цьому агенту заборонено),
    # тому читаємо env напряму: django-environ.read_env() кладе .env у os.environ.
    return _resolve(
        os.environ.get("ANTHROPIC_TRANSLATION_DICT_MODEL", ""), DEFAULT_DICT_MODEL, "словник"
    )


def spec(model: str) -> ModelSpec:
    return MODELS.get(model) or MODELS[DEFAULT_BULK_MODEL]


# ---------------------------------------------------------------------------
# Вартість
# ---------------------------------------------------------------------------

BATCH_DISCOUNT = Decimal("0.5")  # Batch API: −50% на input і output, стакається з кешем
CACHE_READ_MULT = Decimal("0.1")  # читання кешу = 0.1 × base input
CACHE_WRITE_1H_MULT = Decimal("2.0")  # запис кешу з ttl=1h = 2.0 × base input


def compute_cost(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = False,
    on: dt.date | None = None,
) -> Decimal:
    """Вартість одного виклику, $. Знижка Batch стакається з множниками кешу."""
    s = spec(model)
    price_in, price_out = s.prices(on)
    mult = BATCH_DISCOUNT if batch else Decimal("1")
    per_m = Decimal("1000000")

    cost = (
        Decimal(input_tokens) / per_m * price_in
        + Decimal(output_tokens) / per_m * price_out
        + Decimal(cache_read_tokens) / per_m * price_in * CACHE_READ_MULT
        + Decimal(cache_write_tokens) / per_m * price_in * CACHE_WRITE_1H_MULT
    ) * mult
    # cost_usd у моделі — DecimalField(max_digits=8, decimal_places=6)
    return cost.quantize(Decimal("0.000001"))


# ---------------------------------------------------------------------------
# Ліміти
# ---------------------------------------------------------------------------

# Скільки сегментів кладемо в ОДИН запит (TRANSLATION.md §5.3).
BATCH_SIZES: dict[str, int] = {
    "attribute_name": 50,
    "attribute_value": 50,
    "unit": 50,
    "category_name": 50,
    "product_name": 25,
    "seo_title": 25,
    "seo_description": 25,
    "product_short_desc": 25,
    # HTML: 1 товар = 1 запит (потрібен контекст усього опису для узгодженості)
    "product_description": 1,
    "page_html": 1,
    "news_html": 1,
    "other": 25,
}
DEFAULT_BATCH_SIZE = 25

# Види, чиє джерело — rich HTML і які йдуть через сегментний переклад по DOM (§4).
HTML_KINDS = frozenset({"product_description", "page_html", "news_html"})

# Види СЛОВНИКА: перекладаються один раз, ідуть на сильнішу модель (§2, §7.2).
DICTIONARY_KINDS = frozenset({"attribute_name", "attribute_value", "unit", "category_name"})

MAX_TOKENS = 16000
EFFORT = "low"  # переклад — не задача на ризонінг

# ╔══════════════════════════════════════════════════════════════════════════════════════╗
# ║ БЮДЖЕТ ГЛОСАРІЯ — і чому це не «просто константа»                                     ║
# ║                                                                                      ║
# ║ TRANSLATION.md має внутрішню суперечність, яку видно тільки на арифметиці:            ║
# ║   §5.1 каже: «тримай system ≥ 4 096 токенів, інакше кеш на Opus 4.8 мовчки не         ║
# ║              спрацює» → спокуса запхати в system ВЕСЬ глосарій (4 800 термінів);      ║
# ║   §7.4 рахує: «system+глосарій ≈ 1 500 токенів» → і виводить $52 на каталог.          ║
# ║ Обидва разом не бувають.                                                             ║
# ║                                                                                      ║
# ║ Ціна помилки рахується так. Кеш читається НА КОЖНОМУ запиті (0.1 × base input).       ║
# ║ Описи — це 1 товар = 1 запит (§5.3), тобто 6 000 запитів. Повний глосарій = ~43 000   ║
# ║ токенів → 6 000 × 43 000 = 258 M токенів cache-read → +$26 НА РІВНОМУ МІСЦІ,          ║
# ║ тільки за те, що ми возимо 4 800 значень характеристик у кожен запит про опис.        ║
# ║                                                                                      ║
# ║ Тому глосарій ОБРІЗАЄТЬСЯ за бюджетом: достатньо великий, щоб перекрити поріг 4 096   ║
# ║ (інакше кеш не спрацює взагалі), і достатньо малий, щоб не множитись на 6 000.        ║
# ║ Пріоритет обрізання — за впливом на опис: одиниці → назви характеристик → категорії   ║
# ║ → значення. Консистентність фасетів від цього НЕ страждає: її забезпечує сам словник  ║
# ║ (один рядок у БД), а не глосарій. Глосарій потрібен лише щоб ОПИСИ говорили тими      ║
# ║ самими словами, що й характеристики.                                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════════════╝
GLOSSARY_MAX_TOKENS = 5000

# Порядок пріоритету при обрізанні (що важливіше для узгодженості описів).
GLOSSARY_SECTION_PRIORITY = ("general", "unit", "category", "attribute", "value")


def monthly_budget_usd() -> Decimal:
    """Hard-cap витрат за місяць (TRANSLATION.md §7.5). Дешевше поставити запобіжник,
    ніж пояснювати рахунок."""
    raw = os.environ.get("TRANSLATION_MONTHLY_BUDGET_USD") or getattr(
        settings, "TRANSLATION_MONTHLY_BUDGET_USD", "50"
    )
    try:
        return Decimal(str(raw))
    except (TypeError, ValueError):
        return Decimal("50")


# Ступенева політика схвалення (TRANSLATION.md §6.4). Замовник може будь-коли
# перемкнути все на 100% ручне, не чіпаючи код.
DEFAULT_AUTO_APPROVE: dict[str, bool] = {
    "attribute_name": False,
    "attribute_value": False,
    "unit": False,
    "category_name": False,
    "product_name": False,
    "product_short_desc": False,
    "product_description": False,
    "seo_title": False,
    "seo_description": False,
    "page_html": False,
    "news_html": False,
    "other": False,
}


def auto_approve(kind: str) -> bool:
    policy = {**DEFAULT_AUTO_APPROVE, **getattr(settings, "TRANSLATION_AUTO_APPROVE", {})}
    return bool(policy.get(kind, False))
