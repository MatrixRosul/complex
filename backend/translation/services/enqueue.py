"""
Постановка в чергу — ІДЕМПОТЕНТНА, по source_hash (TRANSLATION.md §5.4).

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ ЧОМУ ТУТ ЖОДЕН ТОВАР НЕ ПЛАТИТЬ ЗА ХАРАКТЕРИСТИКИ                                     ║
║ collect_dictionary() ходить по Attribute / AttributeOption / Unit / Category — тобто   ║
║ по НОРМАЛІЗОВАНИХ ТАБЛИЦЯХ. 100 товарів з характеристикою «Колір виробу: Чорний»       ║
║ дають РІВНО 2 записи в черзі (назва + значення), а не 200. Це не оптимізація — це      ║
║ єдина архітектура, за якої «Чорний» фізично не може перекластись двома способами.      ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

Пайплайн для одного поля:
    new_hash == old_hash        → 0 запитів, $0        (95% випадків при щоденному синку)
    запису немає                → шукаємо в Translation Memory (той самий текст в іншого
                                  об'єкта, вже APPROVED) → копіюємо, $0
                                → не знайшли → PENDING
    хеш змінився                → STALE (published_text НЕ ЧІПАЄМО: сайт далі показує
                                  старий СХВАЛЕНИЙ RU, поки новий чекає в черзі)
"""

from __future__ import annotations

import logging
from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from translation.models import TranslationEntry, TranslationKind, TranslationStatus
from translation.segmentation import is_translatable
from translation.services.targets import DICTIONARY_TARGETS, Target, targets_for_kinds

logger = logging.getLogger(__name__)


def source_value(obj, field: str) -> str:
    """Джерело — ЗАВЖДИ українська колонка. Не `obj.name` (він залежить від активної мови
    і в celery-воркері віддасть що завгодно), а саме `obj.name_uk`."""
    val = getattr(obj, f"{field}_uk", None)
    if val is None:
        val = getattr(obj, field, None)
    return str(val or "")


def enqueue_translation(
    obj,
    field: str,
    kind: str,
    *,
    target_lang: str = "ru",
) -> TranslationEntry | None:
    """Поставити одне поле одного об'єкта в чергу. Ідемпотентно.

    Повертає TranslationEntry або None, якщо перекладати нічого (порожньо).
    """
    source = source_value(obj, field)
    ct = ContentType.objects.get_for_model(obj, for_concrete_model=True)

    entry = TranslationEntry.objects.filter(
        content_type=ct, object_id=obj.pk, field=field, target_lang=target_lang
    ).first()

    if not source.strip():
        return entry  # порожнє джерело — нема чого перекладати й нема за що платити

    # Чисті числа, бренди, коди («284», «Gorenje», «A+++») — ~35% значень характеристик.
    # Гнати їх у модель = платити за те, щоб вона повернула їх без змін.
    # Фолбек modeltranslation (ru → uk) і так покаже оригінал, тому в _ru нічого не пишемо.
    if not is_translatable(source):
        if entry is None:
            entry = TranslationEntry.objects.create(
                content_type=ct,
                object_id=obj.pk,
                field=field,
                kind=kind,
                target_lang=target_lang,
                source_text=source,
                target_text=source,
                status=TranslationStatus.DO_NOT_TRANSLATE,
            )
        return entry

    from translation.models import compute_source_hash

    new_hash = compute_source_hash(source)

    if entry is not None:
        if entry.source_hash == new_hash:
            return entry  # 0 запитів, 0 грошей
        if entry.status == TranslationStatus.DO_NOT_TRANSLATE:
            entry.source_text = source
            entry.source_hash = new_hash
            entry.status = TranslationStatus.PENDING
            entry.save(update_fields=["source_text", "source_hash", "status", "updated_at"])
            return entry
        entry.mark_stale(source)  # published_text лишається — сайт не побачить чернетки
        return entry

    # --- Translation Memory: той самий текст уже перекладали в іншого об'єкта? ---
    memory = (
        TranslationEntry.objects.filter(
            kind=kind,
            source_hash=new_hash,
            target_lang=target_lang,
            status=TranslationStatus.APPROVED,
        )
        .exclude(published_text="")
        .first()
    )

    entry = TranslationEntry.objects.create(
        content_type=ct,
        object_id=obj.pk,
        field=field,
        kind=kind,
        target_lang=target_lang,
        source_text=source,
        status=TranslationStatus.PENDING,
    )

    if memory is not None:
        # $0 витрат: дублікати назв/описів між товарами перекладаються рівно один раз.
        entry.target_text = memory.published_text
        entry.published_text = memory.published_text
        entry.status = TranslationStatus.APPROVED
        entry.engine_model = memory.engine_model
        entry.prompt_version = memory.prompt_version
        entry.model_note = "З пам'яті перекладів (той самий текст уже схвалено)"
        entry.save()
        from translation.services.writeback import write_back_to_model

        transaction.on_commit(lambda pk=entry.pk: write_back_to_model(pk))

    return entry


def _collect(targets: list[Target], *, limit: int | None = None) -> Counter[str]:
    stats: Counter[str] = Counter()
    for target in targets:
        qs = target.model.objects.all().order_by("pk")
        if limit:
            qs = qs[:limit]
        for obj in qs.iterator(chunk_size=500):
            entry = enqueue_translation(obj, target.field, target.kind)
            if entry is None:
                stats["empty"] += 1
                continue
            stats[entry.status] += 1
            stats[f"kind:{entry.kind}"] += 1
    return stats


def collect_dictionary(*, limit: int | None = None) -> Counter[str]:
    """Поставити в чергу ВЕСЬ СЛОВНИК: Attribute.name, AttributeOption.value, Unit.name,
    AttributeGroup.name, Category.name.

    Це ~4 900 рядків на каталог із 10 000 товарів — і рівно вони визначають, як
    виглядатимуть характеристики в усіх 300 000 місць каталогу.
    """
    stats = _collect(DICTIONARY_TARGETS, limit=limit)
    logger.info("TRANSLATION collect_dictionary: %s", dict(stats))
    return stats


def collect_targets(kinds: set[str] | None = None, *, limit: int | None = None) -> Counter[str]:
    """Поставити в чергу все (або обрані kind'и): словник + назви + описи + SEO + сторінки."""
    stats = _collect(targets_for_kinds(kinds), limit=limit)
    logger.info("TRANSLATION collect_targets(%s): %s", kinds or "all", dict(stats))
    return stats


__all__ = [
    "TranslationKind",
    "collect_dictionary",
    "collect_targets",
    "enqueue_translation",
    "source_value",
]
