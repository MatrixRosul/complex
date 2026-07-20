"""Застосування пропозиції ШІ в каталог — єдине місце, де harvest торкається `Product`.

Правила (ті самі, що в синку — sync/services.py):
  * атрибути/опції/одиниці, створені тут, отримують `needs_review=True`,
    `is_filterable=False`, `filter_widget=NONE` — у фільтри не лізуть до схвалення людиною;
  * ручне значення (`source=manual`) НЕ перезаписується;
  * пишемо `source=ai`, у `raw_value` — URL джерела (аудит провенансу);
  * у каталог іде ЛИШЕ підтверджене точним кодом (`exact_code != False`);
  * після запису — `rebuild_denorm` (specs_json / filter_tokens / specs_num / FTS).
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from aispecs.category_specs import GROUPS, UNITS, get_template
from aispecs.models import SpecHarvestJob
from catalog.enums import ProductSource
from catalog.models import Attribute, AttributeGroup, AttributeOption, ProductAttributeValue, Unit

log = logging.getLogger(__name__)


class ApplyError(Exception):
    """Застосувати не можна (не той статус / немає шаблону категорії)."""


def _get_group(code: str) -> AttributeGroup:
    name, order = GROUPS.get(code, (code.title(), 100))
    grp, _ = AttributeGroup.objects.get_or_create(
        code=code, defaults={"name": name, "sort_order": order}
    )
    return grp


def _get_unit(code: str | None) -> Unit | None:
    if not code:
        return None
    name, aliases = UNITS.get(code, (code, [code]))
    unit, _ = Unit.objects.get_or_create(
        code=code, defaults={"name": name, "aliases": aliases, "needs_review": True}
    )
    return unit


def _get_attribute(key: str, tdef: tuple[str, str, str, str | None], order: int) -> Attribute:
    attr = Attribute.objects.filter(code=key).first()
    if attr is not None:
        return attr
    name, group_code, value_type, unit_code = tdef
    attr = Attribute.objects.create(
        code=key[:80],
        name=name[:160],
        group=_get_group(group_code),
        unit=_get_unit(unit_code),
        value_type=value_type,
        is_filterable=False,
        filter_widget=Attribute.FilterWidget.NONE,
        needs_review=True,
        sort_order=order,
    )
    # modeltranslation: гарантуємо укр-колонку явно (як усюди в проєкті).
    attr.name_uk = name[:160]
    attr.save(update_fields=["name_uk"])
    return attr


def _get_option(attr: Attribute, value: str) -> AttributeOption:
    value = value.strip()[:160]
    opt = (
        AttributeOption.objects.filter(attribute=attr, value_uk__iexact=value).first()
        or AttributeOption.objects.filter(attribute=attr, aliases__contains=[value]).first()
    )
    if opt is not None:
        return opt
    base = slugify(value) or "opt"
    slug, n = base, 1
    while AttributeOption.objects.filter(attribute=attr, slug=slug).exists():
        n += 1
        slug = f"{base}-{n}"
    opt = AttributeOption.objects.create(
        attribute=attr, value=value, slug=slug[:180], needs_review=True
    )
    opt.value_uk = value
    opt.save(update_fields=["value_uk"])
    return opt


_BOOL_TRUE = {"так", "є", "yes", "true", "1", "+", "да", "наявний", "присутній"}
_BOOL_FALSE = {"ні", "немає", "no", "false", "0", "-", "нет", "відсутній"}


def _infer_type(spec: dict) -> str:
    """Тип значення для характеристики ПОЗА шаблоном (як sync._value_type)."""
    if spec.get("num") is not None:
        return Attribute.ValueType.NUMBER
    text = (spec.get("text") or "").strip().casefold()
    if text in _BOOL_TRUE or text in _BOOL_FALSE:
        return Attribute.ValueType.BOOL
    return Attribute.ValueType.STRING


def _get_group_by_name(name: str) -> AttributeGroup:
    name = (name or "Основні").strip()[:120]
    grp = AttributeGroup.objects.filter(name__iexact=name).first()
    if grp is not None:
        return grp
    code = slugify(name) or f"g{abs(hash(name)) % 10**5}"
    base, n = code, 1
    while AttributeGroup.objects.filter(code=code).exists():
        n += 1
        code = f"{base}-{n}"
    grp = AttributeGroup.objects.create(code=code[:60], name=name, sort_order=60)
    grp.name_uk = name
    grp.save(update_fields=["name_uk"])
    return grp


def _get_unit_by_name(name: str | None) -> Unit | None:
    if not name:
        return None
    name = name.strip()[:32]
    if not name:
        return None
    unit = (
        Unit.objects.filter(name_uk__iexact=name).first()
        or Unit.objects.filter(aliases__contains=[name]).first()
    )
    if unit is not None:
        return unit
    code = slugify(name) or f"u{abs(hash(name)) % 10**5}"
    base, n = code, 1
    while Unit.objects.filter(code=code).exists():
        n += 1
        code = f"{base}-{n}"
    unit = Unit.objects.create(code=code[:32], name=name, aliases=[name], needs_review=True)
    unit.name_uk = name
    unit.save(update_fields=["name_uk"])
    return unit


def _get_attribute_by_name(
    name: str, group_name: str, value_type: str, unit_name: str | None
) -> Attribute:
    """Знайти/створити характеристику за НАЗВОЮ — для категорій без шаблону.

    Пошук по name_uk та aliases (як sync._find_by_name) — щоб «Витрата води» з різних
    джерел не плодила дублі. Нова — needs_review=True, is_filterable=False.
    """
    name = name.strip()[:160]
    attr = (
        Attribute.objects.filter(name_uk__iexact=name).first()
        or Attribute.objects.filter(aliases__contains=[name]).first()
    )
    if attr is not None:
        return attr
    code = slugify(name) or f"attr-{abs(hash(name)) % 10**6}"
    base, n = code, 1
    while Attribute.objects.filter(code=code).exists():
        n += 1
        code = f"{base}-{n}"
    attr = Attribute.objects.create(
        code=code[:80],
        name=name,
        group=_get_group_by_name(group_name),
        unit=_get_unit_by_name(unit_name) if value_type == Attribute.ValueType.NUMBER else None,
        value_type=value_type,
        is_filterable=False,
        filter_widget=Attribute.FilterWidget.NONE,
        needs_review=True,
        sort_order=100,
    )
    attr.name_uk = name
    attr.save(update_fields=["name_uk"])
    return attr


def _to_decimal(spec: dict) -> Decimal | None:
    num = spec.get("num")
    if num is None:
        raw = (spec.get("text") or "").replace(",", ".").strip()
        try:
            return Decimal(raw)
        except (InvalidOperation, ValueError):
            return None
    try:
        return Decimal(str(num))
    except (InvalidOperation, ValueError):
        return None


def _set_value(product, attr: Attribute, value_type: str, spec: dict, source: str) -> bool:
    """Записати одне значення. Повертає True, якщо записали; False — якщо пропустили."""
    # ручне значення людини недоторкане
    if ProductAttributeValue.objects.filter(
        product=product, attribute=attr, source=ProductSource.MANUAL
    ).exists():
        return False
    # прибираємо попереднє НЕ-ручне значення цього атрибута (idempotent re-apply)
    ProductAttributeValue.objects.filter(product=product, attribute=attr).exclude(
        source=ProductSource.MANUAL
    ).delete()

    raw = (spec.get("source_url") or "")[:500]

    if value_type == Attribute.ValueType.NUMBER:
        dec = _to_decimal(spec)
        if dec is None:
            return False
        ProductAttributeValue.objects.create(
            product=product, attribute=attr, value_number=dec, source=source, raw_value=raw
        )
        return True

    if value_type == Attribute.ValueType.OPTION:
        text = (spec.get("text") or "").strip()
        if not text:
            return False
        opt = _get_option(attr, text)
        ProductAttributeValue.objects.create(
            product=product, attribute=attr, option=opt, source=source, raw_value=raw
        )
        return True

    if value_type == Attribute.ValueType.BOOL:
        text = (spec.get("text") or "").strip().casefold()
        val = text.startswith(("так", "yes", "є", "да"))
        ProductAttributeValue.objects.create(
            product=product, attribute=attr, value_bool=val, source=source, raw_value=raw
        )
        return True

    # STRING
    text = (spec.get("text") or "").strip()
    if not text:
        return False
    val = text[:500]
    pav = ProductAttributeValue.objects.create(
        product=product, attribute=attr, value_string=val, source=source, raw_value=raw
    )
    pav.value_string_uk = val
    pav.save(update_fields=["value_string_uk"])
    return True


NO_DATA = "Немає даних"


def _set_no_data(product, attr: Attribute, source: str) -> bool:
    """Заповнити рядок сітки заглушкою «Немає даних» (щоб грід був повний у всіх товарів)."""
    if ProductAttributeValue.objects.filter(
        product=product, attribute=attr, source=ProductSource.MANUAL
    ).exists():
        return False
    ProductAttributeValue.objects.filter(product=product, attribute=attr).exclude(
        source=ProductSource.MANUAL
    ).delete()
    pav = ProductAttributeValue.objects.create(
        product=product, attribute=attr, value_string=NO_DATA, source=source
    )
    pav.value_string_uk = NO_DATA
    pav.save(update_fields=["value_string_uk"])
    return True


@transaction.atomic
def apply_job(
    job: SpecHarvestJob, user=None, *, set_description: bool = True, fill_empty: bool = True
) -> int:
    """Застосувати пропозицію: записати підтверджені характеристики + опис у товар.

    Повертає кількість записаних полів. Кидає ApplyError, якщо статус не той.
    """
    if job.status not in (SpecHarvestJob.Status.NEEDS_REVIEW, SpecHarvestJob.Status.FAILED):
        raise ApplyError(
            f"Джобу {job.pk} не застосувати зі статусу «{job.get_status_display()}» — "
            "лише з «На перегляд»."
        )
    # Немає сітки → generic-режим: усі характеристики за назвою (name_uk), без «Немає даних».
    # Це дозволяє покрити будь-яку категорію; уніфікацію в такому разі дає саме джерело
    # (ek.ua вживає однакові назви параметрів у межах категорії).
    template = get_template(job.category_key)
    order_by_key = {key: (i + 1) * 10 for i, key in enumerate(template)}
    product = job.product
    count = 0
    filled: set[str] = set()

    for spec in job.proposed_specs or []:
        if spec.get("exact_code") is False:  # лише підтверджене точним кодом
            continue
        key = spec.get("key")
        tdef = template.get(key) if key else None
        if tdef:
            # характеристика зі стандартного шаблону категорії (уніфікована)
            attr = _get_attribute(key, tdef, order_by_key.get(key, 100))
            value_type = tdef[2]
        elif (spec.get("name_uk") or "").strip():
            # характеристика поза шаблоном — створюємо за назвою через словник
            value_type = _infer_type(spec)
            attr = _get_attribute_by_name(
                spec["name_uk"], spec.get("group_uk") or "Основні", value_type, spec.get("unit")
            )
        else:
            continue
        if _set_value(product, attr, value_type, spec, ProductSource.AI):
            count += 1
            if key:
                filled.add(key)

    # перелік програм — окремий рядок-характеристика
    if job.proposed_programs and "programs_list" in template:
        attr = _get_attribute(
            "programs_list", template["programs_list"], order_by_key.get("programs_list", 100)
        )
        joined = ", ".join(str(p) for p in job.proposed_programs)
        if _set_value(product, attr, "string", {"text": joined}, ProductSource.AI):
            count += 1
            filled.add("programs_list")

    # ГРІД: заповнюємо решту рядків сітки заглушкою «Немає даних», щоб структура була
    # однакова в УСІХ товарів категорії (вимога замовника). У applied_count не рахуємо —
    # це не реальні дані.
    if fill_empty and template:
        for key, tdef in template.items():
            if key in filled or key.endswith("_list"):
                continue
            attr = _get_attribute(key, tdef, order_by_key.get(key, 100))
            _set_no_data(product, attr, ProductSource.AI)

    # опис — лише якщо порожній (ручну роботу не чіпаємо)
    if set_description and job.proposed_description and not (product.description_uk or "").strip():
        product.description = job.proposed_description
        product.description_uk = job.proposed_description
        product.save(update_fields=["description", "description_uk"])

    # денормалізація (specs_json / filter_tokens / specs_num / FTS)
    from sync.services import rebuild_denorm

    rebuild_denorm([product.pk])

    job.status = SpecHarvestJob.Status.APPLIED
    job.applied_count = count
    job.reviewed_by = user
    job.reviewed_at = timezone.now()
    job.error = ""
    job.save()
    return count


def reject_job(job: SpecHarvestJob, user=None) -> None:
    job.status = SpecHarvestJob.Status.REJECTED
    job.reviewed_by = user
    job.reviewed_at = timezone.now()
    job.save()
