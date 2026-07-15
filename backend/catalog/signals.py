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

from django.db.models.signals import post_delete, post_save
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
