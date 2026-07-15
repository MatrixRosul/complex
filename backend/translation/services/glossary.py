"""
Глосарій → system-блок промпта (TRANSLATION.md §8.2).

Словник платить ДВІЧІ: спершу він дає консистентні характеристики, а потім схвалені
терміни їдуть у промпт як ОБОВ'ЯЗКОВА термінологія — і описи товарів починають
перекладатися тими самими словами, що й характеристики.

⚠️ Рендер ЗАВЖДИ детермінований (.order_by("pk")): кеш Claude — префіксний, будь-який
   зміщений байт інвалідує все далі.
⚠️ Версія бампається окремою задачею раз на добу, а не при кожному схваленні терміна:
   інакше кеш не доживе до кінця батчу.
"""

from __future__ import annotations

import logging

from django.core.cache import cache

from translation.models import GlossaryTerm, TranslationEntry, TranslationStatus
from translation.prompts import build_glossary_block, glossary_version

logger = logging.getLogger(__name__)

CACHE_KEY = "translation:glossary_block"
CACHE_TTL = 60 * 60 * 24

# Схвалені записи черги, які варто підмішувати в глосарій як термінологію.
GLOSSARY_FROM_ENTRIES = {
    "attribute_name": GlossaryTerm.Section.ATTRIBUTE,
    "attribute_value": GlossaryTerm.Section.VALUE,
    "unit": GlossaryTerm.Section.UNIT,
    "category_name": GlossaryTerm.Section.CATEGORY,
}


class _Row:
    """Легкий рядок для build_glossary_block (щоб не плодити GlossaryTerm у пам'яті)."""

    __slots__ = ("section", "source_term", "target_term")

    def __init__(self, section: str, source_term: str, target_term: str):
        self.section = section
        self.source_term = source_term
        self.target_term = target_term


def _rows() -> list[_Row]:
    rows: list[_Row] = []

    # 1) Ручний глосарій — має пріоритет: редактор править термінологію, не чіпаючи чергу.
    seen: set[str] = set()
    for t in GlossaryTerm.objects.filter(is_active=True).order_by("section", "sort_order", "pk"):
        key = t.source_term.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(_Row(t.section, t.source_term, t.target_term))

    # 2) Схвалені терміни зі словникової частини черги.
    qs = (
        TranslationEntry.objects.filter(
            kind__in=GLOSSARY_FROM_ENTRIES,
            status=TranslationStatus.APPROVED,
            target_lang="ru",
        )
        .exclude(published_text="")
        .order_by("pk")  # детермінізм — не set(), не .all() без order_by
    )
    for e in qs.iterator(chunk_size=1000):
        key = e.source_text.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(_Row(GLOSSARY_FROM_ENTRIES[e.kind], e.source_text, e.published_text))

    return rows


def build_block(*, use_cache: bool = True) -> str:
    if use_cache:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached
    block = build_glossary_block(_rows())
    if use_cache:
        cache.set(CACHE_KEY, block, CACHE_TTL)
    return block


def rebuild_glossary() -> str:
    """Нічний cron: перезібрати блок і бампнути версію."""
    cache.delete(CACHE_KEY)
    block = build_block()
    version = glossary_version(block)
    logger.info("TRANSLATION: глосарій перезібрано, версія=%s, %d симв.", version, len(block))
    return block


def current_version(block: str | None = None) -> str:
    return glossary_version(block if block is not None else build_block())
