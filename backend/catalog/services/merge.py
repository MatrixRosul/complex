"""Злиття дублів словників (DATA_MODEL §2.2).

⚠️ ЧОМУ ЦЕ ІСНУЄ ЗАМІСТЬ КНОПКИ «ВИДАЛИТИ». Синк створює бренди/країни/опції автоматично, і
   в прайсах вони приходять як «SHARP», «Sharp», «шарп». Видалити дубль напряму НЕ МОЖНА:
     • `Product.brand` має on_delete=SET_NULL → видалення дубля мовчки обнулило б бренд у
       сотень товарів і вибило б їх з фасета «Виробник»;
     • `ProductAttributeValue.option` має on_delete=PROTECT → видалення опції просто впаде.
   Тому в адмінці delete для Brand/Country/AttributeOption вимкнено, а дедуп робиться ТУТ:
   товари переїжджають на «переможця», ім'я й синоніми дубля стають його синонімами (щоб
   наступний синк упізнав той самий рядок прайсу), і лише тоді дубль видаляється.

Усі три функції ставлять `denorm_dirty=True` на зачеплених товарах: filter_tokens містить
"brand:<slug>" / "country:<slug>" / "<attr>:<option-slug>", тобто після злиття вони брехливі,
поки `rebuild_product_denorm` їх не перебудує.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from catalog.models import (
    Attribute,
    AttributeOption,
    Brand,
    Country,
    Product,
    ProductAttributeValue,
)

log = logging.getLogger(__name__)

__all__ = ["MergeError", "merge_brands", "merge_countries", "merge_options"]


class MergeError(ValueError):
    """Злиття неможливе (сам у себе, різні атрибути тощо)."""


def _merged_aliases(
    dst_aliases: list[str], dst_name: str, src_name: str, src_aliases: list[str]
) -> list[str]:
    """Синоніми переможця = його власні + ім'я та синоніми дубля (без власного імені)."""
    merged = {*dst_aliases, src_name, *src_aliases}
    merged.discard(dst_name)
    merged.discard("")
    return sorted(merged)


@transaction.atomic
def merge_brands(src: Brand, dst: Brand) -> int:
    """Зливає бренд `src` у `dst`. Повертає кількість перенесених товарів."""
    if src.pk == dst.pk:
        raise MergeError("Не можна злити бренд сам у себе.")

    now = timezone.now()
    moved = Product.objects.filter(brand=src).update(brand=dst, denorm_dirty=True, updated_at=now)

    dst.aliases = _merged_aliases(dst.aliases, dst.name, src.name, src.aliases)
    dst.needs_review = False
    dst.save(update_fields=["aliases", "needs_review", "updated_at"])

    src.delete()
    log.info("merge_brands src=%s dst=%s moved=%s", src.pk, dst.pk, moved)
    return moved


@transaction.atomic
def merge_countries(src: Country, dst: Country) -> int:
    """Зливає країну `src` у `dst`. Повертає кількість перенесених товарів."""
    if src.pk == dst.pk:
        raise MergeError("Не можна злити країну саму в себе.")

    now = timezone.now()
    moved = Product.objects.filter(country=src).update(
        country=dst, denorm_dirty=True, updated_at=now
    )

    dst.aliases = _merged_aliases(dst.aliases, dst.name, src.name, src.aliases)
    dst.needs_review = False
    dst.save(update_fields=["aliases", "needs_review", "updated_at"])

    src.delete()
    log.info("merge_countries src=%s dst=%s moved=%s", src.pk, dst.pk, moved)
    return moved


@transaction.atomic
def merge_options(src: AttributeOption, dst: AttributeOption) -> int:
    """Зливає варіант `src` у `dst` (у межах ОДНІЄЇ характеристики).

    ⚠️ Тут не досить `update(option=dst)`: якщо товар уже має рядок і зі `src`, і з `dst`
       (реальний випадок для multi-value: «Сріблястий» + «Silver» на одному товарі), простий
       UPDATE впаде на `uniq_pav_option`. Тому спершу прибираємо рядки-колізії.
    """
    if src.pk == dst.pk:
        raise MergeError("Не можна злити варіант сам у себе.")
    if src.attribute_id != dst.attribute_id:
        raise MergeError(
            "Варіанти належать різним характеристикам — злиття зруйнувало б фасети. "
            "Спершу перенесіть значення в потрібну характеристику."
        )

    collision_products = ProductAttributeValue.objects.filter(option=dst).values_list(
        "product_id", flat=True
    )
    ProductAttributeValue.objects.filter(option=src, product_id__in=collision_products).delete()

    moved_ids = list(
        ProductAttributeValue.objects.filter(option=src).values_list("product_id", flat=True)
    )
    ProductAttributeValue.objects.filter(option=src).update(option=dst, updated_at=timezone.now())

    Product.objects.filter(pk__in=moved_ids).update(denorm_dirty=True, updated_at=timezone.now())

    dst.aliases = _merged_aliases(dst.aliases, dst.value, src.value, src.aliases)
    dst.needs_review = False
    dst.save(update_fields=["aliases", "needs_review", "updated_at"])

    src.delete()
    log.info("merge_options src=%s dst=%s moved=%s", src.pk, dst.pk, len(moved_ids))
    return len(moved_ids)


def mergeable_targets(obj: Brand | Country | AttributeOption):
    """Кандидати-переможці для діалогу злиття."""
    model = type(obj)
    qs = model.objects.exclude(pk=obj.pk)
    if isinstance(obj, AttributeOption):
        qs = qs.filter(attribute_id=obj.attribute_id)
    return qs


def unused_attribute(attribute: Attribute) -> bool:  # pragma: no cover - хелпер для адмінки
    return not ProductAttributeValue.objects.filter(attribute=attribute).exists()
