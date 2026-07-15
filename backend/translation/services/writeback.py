"""
Write-back: TranslationEntry.published_text → `<field>_ru` моделі каталогу.

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ ЄДИНІ ДВЕРІ, ЧЕРЕЗ ЯКІ МАШИННИЙ ПЕРЕКЛАД ПОТРАПЛЯЄ НА САЙТ.                            ║
║                                                                                      ║
║   модель пише  → target_text                                                          ║
║   редактор     → target_text                                                          ║
║   «Схвалити»   → published_text  → І ТІЛЬКИ ТОДІ → `<field>_ru`                        ║
║                                                                                      ║
║ Поки статус не APPROVED, у `_ru` не потрапляє НІЧОГО. «На сайті раптом з'явився        ║
║ неперевірений текст» тут неможливий за побудовою, а не за домовленістю.               ║
║                                                                                      ║
║ Джерело змінилось (STALE) → published_text НЕ чіпаємо → сайт далі віддає старий,       ║
║ СХВАЛЕНИЙ російський текст, поки новий машинний варіант чекає в черзі.                ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

TranslationEntry — джерело правди для черги, аудиту й витрат.
`<field>_ru` — ВІТРИНА: її читає API/Ninja, нуль джойнів на рендері.
"""

from __future__ import annotations

import logging

from django.db import transaction

from translation.models import TranslationEntry, TranslationStatus

logger = logging.getLogger(__name__)


def write_back_to_model(entry_pk: int) -> bool:
    """Скопіювати published_text у `<field>_ru`. Викликається ПІСЛЯ коміту (models.approve()).

    Повертає True, якщо вітрина реально змінилась.
    """
    entry = TranslationEntry.objects.filter(pk=entry_pk).select_related("content_type").first()
    if entry is None:
        logger.warning("TRANSLATION write-back: запис %s зник", entry_pk)
        return False

    if entry.status != TranslationStatus.APPROVED or not entry.published_text:
        # Захист від випадкового виклику: у вітрину пише ТІЛЬКИ схвалене.
        logger.debug("TRANSLATION write-back пропущено: %s status=%s", entry_pk, entry.status)
        return False

    obj = entry.target  # GenericForeignKey
    if obj is None:
        logger.warning(
            "TRANSLATION write-back: об'єкт %s#%s зник", entry.content_type, entry.object_id
        )
        return False

    attr = f"{entry.field}_{entry.target_lang}"
    if not hasattr(obj, attr):
        logger.error(
            "TRANSLATION write-back: у %s немає поля %s — це поле не зареєстроване "
            "в <app>/translation.py (modeltranslation)",
            type(obj).__name__,
            attr,
        )
        return False

    if getattr(obj, attr) == entry.published_text:
        return False

    setattr(obj, attr, entry.published_text)
    obj.save(update_fields=[attr])
    logger.info("TRANSLATION write-back: %s.%s ← запис %s", type(obj).__name__, attr, entry_pk)
    return True


def apply_approved(*, kind: str | None = None, limit: int | None = None) -> int:
    """Пройтись по всіх APPROVED і синхронізувати вітрину.

    Потрібно тому, що write-back може не відбутися: відкат транзакції, впав воркер,
    поле дописали в translation.py пізніше. Це — самолікування, ідемпотентне.

    ⚠️ НЕ схвалює нічого сам. Тільки переносить УЖЕ схвалене.
    """
    qs = (
        TranslationEntry.objects.filter(status=TranslationStatus.APPROVED, target_lang="ru")
        .exclude(published_text="")
        .order_by("pk")
    )
    if kind:
        qs = qs.filter(kind=kind)
    if limit:
        qs = qs[:limit]

    written = 0
    for pk in list(qs.values_list("pk", flat=True)):
        with transaction.atomic():
            if write_back_to_model(pk):
                written += 1

    logger.info("TRANSLATION apply_approved: оновлено %d полів", written)
    return written
