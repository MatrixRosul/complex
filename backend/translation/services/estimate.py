"""
Оцінка вартості ПО РЕАЛЬНИХ ДАНИХ БД, а не по припущеннях із документа.

Рахуємо те, що реально лежить у черзі / у каталозі:
  input  ≈ токени сегментів + системний блок (кешується → 0.1×)
  output ≈ токени перекладу ≈ input × OUTPUT_RATIO (російська трохи довша за українську)

Оцінка груба (евристика 2,5 симв/токен). TRANSLATION.md §7.4 прямо каже: перед першим
повним прогоном заміряти client.messages.count_tokens() на 50 реальних описах і
перерахувати — описи це 83% рахунку, і саме вони роблять оцінку чутливою.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from translation import conf
from translation.models import TranslationEntry, TranslationStatus
from translation.prompts import TRANSLATOR_RULES, estimate_tokens
from translation.segmentation import parse_html
from translation.services import glossary

# Російський переклад української дає приблизно стільки ж токенів (± 5%).
OUTPUT_RATIO = Decimal("1.0")


@dataclass
class KindEstimate:
    kind: str
    entries: int = 0
    requests: int = 0
    source_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = ""
    cost_usd: Decimal = Decimal("0")


@dataclass
class Estimate:
    batch: bool = True
    system_tokens: int = 0
    cached: bool = False
    kinds: dict[str, KindEstimate] = field(default_factory=dict)

    @property
    def total_cost(self) -> Decimal:
        return sum((k.cost_usd for k in self.kinds.values()), Decimal("0"))

    @property
    def total_entries(self) -> int:
        return sum(k.entries for k in self.kinds.values())

    @property
    def total_requests(self) -> int:
        return sum(k.requests for k in self.kinds.values())


def _segment_tokens(entry: TranslationEntry) -> int:
    """Скільки токенів реально поїде в модель для цього запису."""
    if entry.kind in conf.HTML_KINDS:
        doc = parse_html(entry.source_text)
        return sum(estimate_tokens(s.uk) for s in doc.segments)
    return estimate_tokens(entry.source_text)


def estimate_queue(*, kind: str | None = None, batch: bool = True) -> Estimate:
    """Оцінка вартості того, що ЗАРАЗ у черзі (PENDING / STALE / REJECTED)."""
    block = glossary.build_block()
    system_text = TRANSLATOR_RULES + block
    system_tokens = estimate_tokens(system_text)

    est = Estimate(batch=batch, system_tokens=system_tokens)

    qs = TranslationEntry.objects.filter(
        status__in=(
            TranslationStatus.PENDING,
            TranslationStatus.STALE,
            TranslationStatus.REJECTED,
        ),
        target_lang="ru",
    )
    if kind:
        qs = qs.filter(kind=kind)

    for entry in qs.iterator(chunk_size=500):
        model = conf.dict_model() if entry.kind in conf.DICTIONARY_KINDS else conf.bulk_model()
        ke = est.kinds.setdefault(entry.kind, KindEstimate(kind=entry.kind, model=model))
        ke.entries += 1
        ke.source_tokens += _segment_tokens(entry)

    for ke in est.kinds.values():
        size = conf.BATCH_SIZES.get(ke.kind, conf.DEFAULT_BATCH_SIZE)
        ke.requests = -(-ke.entries // size)  # ceil

        s = conf.spec(ke.model)
        cacheable = system_tokens >= s.min_cache_prefix
        est.cached = est.cached or cacheable

        ke.input_tokens = ke.source_tokens
        ke.output_tokens = int(Decimal(ke.source_tokens) * OUTPUT_RATIO)

        if cacheable:
            # system читається з кешу на кожному запиті (0.1×), пишеться один раз (2.0×)
            ke.cache_read_tokens = system_tokens * ke.requests
            cache_write = system_tokens
        else:
            # кеш не спрацює — system іде за повною ціною на КОЖНОМУ запиті
            ke.input_tokens += system_tokens * ke.requests
            cache_write = 0

        ke.cost_usd = conf.compute_cost(
            ke.model,
            input_tokens=ke.input_tokens,
            output_tokens=ke.output_tokens,
            cache_read_tokens=ke.cache_read_tokens,
            cache_write_tokens=cache_write,
            batch=batch,
        )

    return est
