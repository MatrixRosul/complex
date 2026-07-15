"""Фото товару: головне фото і збереження inline-формсету.

⚠️ `uniq_main_image` — ЧАСТКОВИЙ unique-індекс `(product) WHERE is_main`, а такий індекс
   НЕ МОЖЕ бути DEFERRABLE. Тобто перевірка спрацьовує на КОЖНОМУ рядку, а не в кінці
   транзакції. Наслідок: зміна головного фото ЗАВЖДИ двокрокова — спершу гасимо старий
   прапорець, і лише потім ставимо новий.

   Inline-формсет Django зберігає рядки ПО ОДНОМУ в довільному порядку. Якщо просто віддати
   йому дані, де новий рядок має is_main=True, а старий ще не погашений, — IntegrityError
   прилетить приблизно завжди. Саме тому ProductAdmin.save_formset() делегує сюди.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction

from catalog.models import Product, ProductImage

if TYPE_CHECKING:
    from django.forms.models import BaseInlineFormSet

log = logging.getLogger(__name__)

__all__ = ["save_image_formset", "set_main_image"]


@transaction.atomic
def set_main_image(product: Product, image: ProductImage) -> None:
    """Робить `image` головним фото товару. Двокроково — див. шапку модуля."""
    ProductImage.objects.filter(product=product, is_main=True).exclude(pk=image.pk).update(
        is_main=False
    )
    if not image.is_main:
        image.is_main = True
        image.save(update_fields=["is_main", "updated_at"])


@transaction.atomic
def save_image_formset(product: Product, formset: BaseInlineFormSet) -> None:
    """Зберігає inline-формсет фото, не вбиваючись об `uniq_main_image`.

    Порядок критичний:
      1. видалення;
      2. ГАСИМО is_main усюди, де він стоїть зараз (і в БД, і серед незбережених рядків);
      3. зберігаємо рядки;
      4. якщо головного фото не лишилось — призначаємо перше за позицією.

    Крок 4 не косметика: `main_image_url` у денормалізації і фото у фіді Hotline беруться з
    головного. Товар без головного фото = товар без фото у видачі й у фіді.
    """
    instances = formset.save(commit=False)

    for obj in formset.deleted_objects:
        obj.delete()

    # Останній відмічений рядок виграє (користувач міг поставити галочку на двох).
    wanted: ProductImage | None = None
    for inst in instances:
        if inst.is_main:
            wanted = inst

    if wanted is not None:
        stale = ProductImage.objects.filter(product=product, is_main=True)
        if wanted.pk:
            stale = stale.exclude(pk=wanted.pk)
        stale.update(is_main=False)  # ← крок 2: спершу погасити
        for inst in instances:
            if inst is not wanted:
                inst.is_main = False

    for inst in instances:
        inst.product = product
        inst.save()

    formset.save_m2m()

    if not ProductImage.objects.filter(product=product, is_main=True).exists():
        first = ProductImage.objects.filter(product=product).order_by("position", "id").first()
        if first is not None:
            ProductImage.objects.filter(pk=first.pk).update(is_main=True)
