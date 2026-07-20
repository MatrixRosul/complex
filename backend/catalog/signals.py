"""Інвалідація кешу каталогу (ARCHITECTURE §2.2).

Що саме змушує кеш протухнути:
  * `Category`      — дерево мегаменю (назва, іконка, порядок, активність);
  * `Attribute` / `AttributeOption` / `Unit` / `AttributeGroup` — ПІДПИСИ фасетів;
  * `Brand` / `Country` — підписи системних фасетів.

⚠️ `Product` тут НЕМАЄ навмисно. Кеш не тримає ані товарів, ані лічильників фасетів (cache.py
   пояснює чому), а лічильник товарів у дереві живе під TTL 1 год. Підписувати кожен з 10 000
   товарів на інвалідацію дерева означало б знімати кеш 10 000 разів за прогін синку — тобто
   не мати кешу взагалі саме тоді, коли він найпотрібніший.

⚠️ `post_delete` теж підписаний: видалення бренду або категорії має знести кеш так само, як
   зміна. Інакше зникла категорія лишається в мегаменю до кінця TTL.
"""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from catalog.models import (
    Attribute,
    AttributeGroup,
    AttributeOption,
    Brand,
    Category,
    Country,
    Product,
    Unit,
)
from catalog.services.cache import invalidate_catalog_cache, invalidate_collections_cache

_WATCHED = (Category, Attribute, AttributeGroup, AttributeOption, Unit, Brand, Country)


@receiver(post_save, sender=Category)
@receiver(post_save, sender=Attribute)
@receiver(post_save, sender=AttributeGroup)
@receiver(post_save, sender=AttributeOption)
@receiver(post_save, sender=Unit)
@receiver(post_save, sender=Brand)
@receiver(post_save, sender=Country)
@receiver(post_delete, sender=Category)
@receiver(post_delete, sender=Attribute)
@receiver(post_delete, sender=AttributeGroup)
@receiver(post_delete, sender=AttributeOption)
@receiver(post_delete, sender=Unit)
@receiver(post_delete, sender=Brand)
@receiver(post_delete, sender=Country)
def _invalidate(sender, **kwargs) -> None:
    invalidate_catalog_cache()


# ---------------------------------------------------------------------------
# Порядок характеристик: адмінка → specs_json
# ---------------------------------------------------------------------------
#
# ⚡ ЧОМУ ЦЕ ТУТ. Порядок характеристик на картці бере не адмінка «наживо», а денормалізована
#    проєкція `Product.specs_json` (її складає sync.services.rebuild_denorm). Тобто замовник
#    міняє `sort_order` в адмінці — і не бачить НІЧОГО: кеш каталогу знявся, а specs_json лишився
#    зі старим порядком до наступного прогону синку. Скарга «поміняв порядок, на сайті те саме»
#    росте звідси.
#
# ⚡ ЧОМУ САМЕ dirty, А НЕ ПЕРЕБУДОВА ТУТ-ТАКИ. Одна характеристика — це тисячі товарів;
#    перебудовувати їх синхронно в запиті адмінки = таймаут на «Зберегти». Ставимо
#    `denorm_dirty=True`, а `heal_denorm` (beat, кожні 5 хв) добере — той самий механізм,
#    що й для решти змін (ADR-011).
#
# ⚠️ Тільки на РЕАЛЬНУ зміну порядку/групи. Синк зберігає атрибути пачками, і мітити тисячі
#    товарів брудними на кожен його дотик означало б ганяти денорм по колу без потреби.

_ORDER_FIELDS = ("sort_order", "group_id")


@receiver(pre_save, sender=Attribute)
@receiver(pre_save, sender=AttributeGroup)
def _remember_order(sender, instance, **kwargs) -> None:
    if not instance.pk:
        instance._order_changed = False
        return
    old = sender.objects.filter(pk=instance.pk).values(*_fields_for(sender)).first()
    instance._order_changed = bool(old) and any(
        old[field] != getattr(instance, field) for field in _fields_for(sender)
    )


def _fields_for(sender) -> tuple[str, ...]:
    """У групи немає `group_id` — у неї порядок задає лише власний `sort_order`."""
    return _ORDER_FIELDS if sender is Attribute else ("sort_order",)


@receiver(post_save, sender=Attribute)
@receiver(post_save, sender=AttributeGroup)
def _mark_specs_dirty(sender, instance, created, **kwargs) -> None:
    if created or not getattr(instance, "_order_changed", False):
        return

    values = {"attr_values__attribute": instance}
    if sender is AttributeGroup:
        values = {"attr_values__attribute__group": instance}

    # ⚠️ .update(), а не .save() у циклі: post_save на Product тут не потрібен (добірки на
    #    головній від порядку характеристик не залежать), а тисяча сигналів — залежить.
    Product.objects.filter(**values).distinct().update(denorm_dirty=True)


@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
def _invalidate_collections(sender, **kwargs) -> None:
    """Товар змінився → добірки на головній могли поїхати.

    ⚠️ Знімає ТІЛЬКИ ключі добірок (2 штуки), а не весь кеш каталогу. Цей сигнал стріляє на
       КОЖНЕ збереження товару, і тягнути за собою перебудову дерева категорій + метаданих
       фасетів було б чистим марнотратством: до одного холодильника вони стосунку не мають.

    ⚠️ Синк оновлює товари через bulk_update — сигналів він НЕ шле, і це нормально: там кеш
       добирає своє через TTL (5 хв). Сигнал тут заради адмінки — замовник ставить галочку
       «Рекомендований» і хоче побачити товар у «Хітах», а не чекати TTL.
    """
    invalidate_collections_cache()
