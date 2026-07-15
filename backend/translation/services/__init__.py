"""
Сервісний шар модуля перекладу.

⚠️ Це ПАКЕТ, а не services.py — так вимагає вже написаний translation/models.py:
       from translation.services.writeback import write_back_to_model
   (TranslationEntry.approve() викликає його на on_commit).

Публічний API (те, чим користуються tasks.py, management-команда й адмінка):

    enqueue_translation(obj, field, kind)  → TranslationEntry   ідемпотентно, по source_hash
    collect_dictionary()                   → ставить у чергу ВЕСЬ словник
    collect_targets(kinds)                 → ставить у чергу все інше
    translate_pending(kind, limit)         → прогін черги: PENDING → MACHINE / FAILED
    apply_approved()                       → APPROVED → `<field>_ru` (і НІЯК інакше)
    estimate_queue()                       → скільки коштуватиме те, що в черзі
    rebuild_glossary()                     → нічний перезбір глосарію
"""

from __future__ import annotations

from translation.services.enqueue import (
    collect_dictionary,
    collect_targets,
    enqueue_translation,
    source_value,
)
from translation.services.estimate import Estimate, KindEstimate, estimate_queue
from translation.services.glossary import build_block, current_version, rebuild_glossary
from translation.services.runner import translate_pending
from translation.services.writeback import apply_approved, write_back_to_model

__all__ = [
    "Estimate",
    "KindEstimate",
    "apply_approved",
    "build_block",
    "collect_dictionary",
    "collect_targets",
    "current_version",
    "enqueue_translation",
    "estimate_queue",
    "rebuild_glossary",
    "source_value",
    "translate_pending",
    "write_back_to_model",
]
