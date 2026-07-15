"""
Вартість, вибір моделі й кеш (TRANSLATION.md §5.1, §7).

Тут ловляться помилки, які НЕ дають виключення, а просто подвоюють рахунок.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from translation import conf
from translation.models import TranslationKind
from translation.prompts import (
    TRANSLATION_SCHEMA,
    TRANSLATOR_RULES,
    build_glossary_block,
    build_payload,
    build_system,
    estimate_tokens,
    render_user_message,
)
from translation.services import collect_dictionary, estimate_queue

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Вибір моделі
# ---------------------------------------------------------------------------


def test_stale_settings_model_is_rejected_not_silently_used(settings):
    """config/settings/base.py досі має claude-sonnet-4-5 — модель БЕЗ structured outputs.

    Мовчки взяти її = отримати JSON «як вийде» і розсипати парсер на першому ж описі.
    Тому: гучний WARN і фолбек на правильну модель.
    """
    settings.ANTHROPIC_TRANSLATION_MODEL = "claude-sonnet-4-5"
    assert conf.bulk_model() == "claude-sonnet-5"


def test_valid_model_from_settings_is_honoured(settings):
    settings.ANTHROPIC_TRANSLATION_MODEL = "claude-opus-4-8"
    assert conf.bulk_model() == "claude-opus-4-8"


def test_dictionary_goes_to_the_strong_model(attribute, option):
    """Словник — на Opus 4.8: обсяг мізерний, а вплив реплікується в 300 000 місць."""
    from translation.models import TranslationEntry, TranslationStatus
    from translation.services.runner import _pick_model

    collect_dictionary()
    dict_entry = TranslationEntry.objects.filter(kind=TranslationKind.ATTRIBUTE_VALUE).first()
    assert _pick_model(dict_entry) == conf.dict_model() == "claude-opus-4-8"

    # А відхилене людиною — теж на сильнішу модель (ретрай).
    dict_entry.status = TranslationStatus.REJECTED
    assert _pick_model(dict_entry) == "claude-opus-4-8"


# ---------------------------------------------------------------------------
# Ціни
# ---------------------------------------------------------------------------


def test_batch_is_half_price():
    kw = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    sync = conf.compute_cost("claude-opus-4-8", **kw, batch=False)
    batch = conf.compute_cost("claude-opus-4-8", **kw, batch=True)
    assert sync == Decimal("30.000000")  # $5 + $25
    assert batch == Decimal("15.000000")  # −50% на input І output


def test_cache_read_is_ten_percent_and_stacks_with_batch():
    full = conf.compute_cost("claude-opus-4-8", input_tokens=1_000_000, batch=True)
    cached = conf.compute_cost("claude-opus-4-8", cache_read_tokens=1_000_000, batch=True)
    assert full == Decimal("2.500000")
    assert cached == Decimal("0.250000")  # base × 0.1 × 0.5 — знижки стакаються


def test_sonnet5_intro_price_expires():
    intro = dt.date(2026, 8, 1)
    after = dt.date(2026, 9, 1)
    kw = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "batch": True}
    assert conf.compute_cost("claude-sonnet-5", **kw, on=intro) == Decimal("6.000000")  # $2+$10
    assert conf.compute_cost("claude-sonnet-5", **kw, on=after) == Decimal("9.000000")  # $3+$15


# ---------------------------------------------------------------------------
# Кеш системного блоку
# ---------------------------------------------------------------------------


def test_cache_control_is_not_set_when_prefix_is_too_short():
    """Мінімальний кешований префікс Opus 4.8 = 4096 токенів.

    Коротший system → кеш МОВЧКИ не запишеться (cache_creation_input_tokens = 0, без
    жодної помилки). Ставити cache_control у цьому разі — самообман, тому не ставимо.
    """
    tiny = build_system("[глосарій]\nЧорний = Черный\n", "claude-opus-4-8")
    assert "cache_control" not in tiny[-1]

    # А з великим глосарієм (поріг перекрито) — breakpoint на ОСТАННЬОМУ system-блоці.
    fat = build_system("Термін = Термин\n" * 2000, "claude-opus-4-8")
    assert fat[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "cache_control" not in fat[0]  # не на першому — інакше два breakpoint'и намарно


def test_min_cache_prefix_is_4096_for_opus():
    assert conf.spec("claude-opus-4-8").min_cache_prefix == 4096


def test_glossary_is_capped_by_token_budget():
    """Глосарій НЕ можна пускати в system цілком — він множиться на кількість запитів.

    Описи йдуть 1 товар = 1 запит (6 000 запитів). Повний глосарій (~4 800 термінів ≈
    43 000 tok) читався б з кешу 6 000 разів → 258 M cache-read токенів → +$26 на каталог
    рівно за те, що ми возимо всі значення характеристик у кожен запит про опис.

    Бюджет має бути одночасно:
      • БІЛЬШИЙ за 4 096 (мін. кешований префікс Opus 4.8) — інакше кеш не спрацює взагалі;
      • ДОСТАТНЬО МАЛИЙ, щоб не множитись на 6 000.
    """

    class T:
        def __init__(self, section, s, t):
            self.section = section
            self.source_term = s
            self.target_term = t

    huge = [
        T("value", f"Значення характеристики номер {i}", f"Значение характеристики {i}")
        for i in range(4800)
    ]
    block = build_glossary_block(huge)

    assert estimate_tokens(block) <= conf.GLOSSARY_MAX_TOKENS * 1.1
    # Модель має знати, що глосарій неповний — інакше вирішить, що решту не перекладають.
    assert "обрізано за бюджетом" in block

    # І разом із правилами поріг 4096 перекрито → кеш реально вмикається.
    system = build_system(block, "claude-opus-4-8")
    assert system[-1].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}


def test_glossary_truncation_keeps_high_impact_sections_first():
    """Обрізаємо значення, а не одиниці й назви характеристик."""

    class T:
        def __init__(self, section, s, t):
            self.section = section
            self.source_term = s
            self.target_term = t

    terms = [T("unit", "міс", "мес"), T("attribute", "Колір виробу", "Цвет изделия")]
    terms += [
        T("value", f"Дуже довге значення характеристики {i}", f"Значение {i}") for i in range(4000)
    ]

    block = build_glossary_block(terms)
    assert "міс = мес" in block  # одиниці вижили
    assert "Колір виробу = Цвет изделия" in block  # назви характеристик вижили
    assert "обрізано за бюджетом" in block  # а значення — обрізані


def test_user_payload_is_deterministic():
    """json.dumps без sort_keys — «тихий інвалідатор» кешу. Перевіряємо, що його немає."""
    payload = build_payload(TranslationKind.ATTRIBUTE_VALUE, [{"uk": "Чорний", "id": "te-1"}])
    a = render_user_message(payload)
    b = render_user_message(
        build_payload(TranslationKind.ATTRIBUTE_VALUE, [{"id": "te-1", "uk": "Чорний"}])
    )
    assert a == b
    assert json.loads(a)["mode"] == "attribute_value"


def test_system_prompt_has_no_silent_invalidators():
    """Ані дати, ані uuid, ані лічильника в system — інакше кеш не спрацює жодного разу."""
    assert "datetime" not in TRANSLATOR_RULES
    assert str(dt.date.today().year) not in TRANSLATOR_RULES
    # Двічі зібраний system мусить бути байт-у-байт однаковим.
    assert build_system("g", "claude-sonnet-5") == build_system("g", "claude-sonnet-5")


def test_schema_forbids_extra_fields():
    assert TRANSLATION_SCHEMA["additionalProperties"] is False
    assert TRANSLATION_SCHEMA["properties"]["translations"]["items"]["required"] == ["id", "ru"]


# ---------------------------------------------------------------------------
# Оцінка
# ---------------------------------------------------------------------------


def test_estimate_queue_reports_cost(attribute, option, unit):
    collect_dictionary()
    est = estimate_queue(batch=True)

    assert est.total_entries > 0
    assert est.total_requests > 0
    assert est.total_cost > 0
    # Batch дешевший за sync — рівно вдвічі за токени.
    assert estimate_queue(batch=False).total_cost > est.total_cost
