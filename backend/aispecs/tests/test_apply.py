"""apply_job — записує ЛИШЕ підтверджене, тими самими правилами, що синк, і не чіпає ручне."""

from __future__ import annotations

from decimal import Decimal

import pytest

from aispecs.models import SpecHarvestJob
from aispecs.services import ApplyError, apply_job, reject_job
from catalog.enums import Availability, ProductSource
from catalog.models import (
    Attribute,
    AttributeOption,
    Category,
    ProductAttributeValue,
    Product,
    Unit,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def product() -> Product:
    cat = Category.objects.create(name="Вбудовані посудомийні машини", slug="posudomyiky")
    p = Product(
        sku="DW-1",
        name="Посудомийна машина Bosch SMV4HVX07E",
        slug="dw-1",
        category=cat,
        base_price=Decimal("20000"),
        price=Decimal("20000"),
        availability=Availability.IN_STOCK,
    )
    p.name_uk, p.slug_uk = p.name, p.slug
    p.save()
    return p


def _job(product: Product, **over) -> SpecHarvestJob:
    specs = over.pop("specs", None) or [
        {"key": "place_settings", "num": 14, "confidence": "high", "exact_code": True,
         "source_url": "https://bosch.example/spec.pdf"},
        {"key": "water_l", "num": 9, "confidence": "high", "exact_code": True},
        {"key": "energy_class", "text": "D", "confidence": "high", "exact_code": True},
        {"key": "installation_type", "text": "Повністю вбудована", "confidence": "high",
         "exact_code": True},
        {"key": "third_rack", "text": "Так — висувна шухляда Vario3", "confidence": "high",
         "exact_code": True},
        # не підтверджено точним кодом → має бути пропущено
        {"key": "noise_db", "num": 47, "confidence": "medium", "exact_code": False},
    ]
    return SpecHarvestJob.objects.create(
        product=product,
        category_key="dishwasher",
        status=SpecHarvestJob.Status.NEEDS_REVIEW,
        model_code="SMV4HVX07E",
        matched=True,
        match_confidence="high",
        proposed_specs=specs,
        proposed_programs=over.pop("programs", ["Еко", "Інтенсивна"]),
        proposed_description=over.pop("description", "Гарна посудомийка."),
        **over,
    )


def test_apply_writes_confirmed_only(product: Product) -> None:
    job = _job(product)
    count = apply_job(job)

    job.refresh_from_db()
    assert job.status == SpecHarvestJob.Status.APPLIED
    assert job.reviewed_at is not None

    # число
    place = ProductAttributeValue.objects.get(product=product, attribute__code="place_settings")
    assert place.value_number == Decimal("14")
    assert place.source == ProductSource.AI
    assert place.raw_value == "https://bosch.example/spec.pdf"  # провенанс збережено

    # опція
    ec = ProductAttributeValue.objects.get(product=product, attribute__code="energy_class")
    assert ec.option is not None and ec.option.value == "D"

    # рядок
    tr = ProductAttributeValue.objects.get(product=product, attribute__code="third_rack")
    assert tr.value_string.startswith("Так")

    # програми записались окремим рядком
    assert ProductAttributeValue.objects.filter(
        product=product, attribute__code="programs_list"
    ).exists()

    # НЕпідтверджене (noise_db, exact_code=False) НЕ записано
    assert not ProductAttributeValue.objects.filter(
        product=product, attribute__code="noise_db"
    ).exists()
    # count: 5 підтверджених + програми
    assert count == 6


def test_created_attributes_are_review_gated(product: Product) -> None:
    apply_job(_job(product))
    attr = Attribute.objects.get(code="place_settings")
    assert attr.needs_review is True
    assert attr.is_filterable is False
    assert attr.filter_widget == Attribute.FilterWidget.NONE
    assert attr.unit is not None and attr.unit.code == "set"
    # опція теж needs_review
    assert AttributeOption.objects.get(attribute__code="energy_class", value="D").needs_review


def test_description_set_only_when_empty(product: Product) -> None:
    apply_job(_job(product, description="Опис від ШІ."))
    product.refresh_from_db()
    assert product.description_uk == "Опис від ШІ."

    # другий товар із ручним описом — не перезаписуємо
    product.description_uk = "Ручний опис"
    product.description = "Ручний опис"
    product.save(update_fields=["description", "description_uk"])
    job2 = _job(product, description="Новий ШІ-опис")
    job2.status = SpecHarvestJob.Status.NEEDS_REVIEW
    job2.save()
    apply_job(job2)
    product.refresh_from_db()
    assert product.description_uk == "Ручний опис"


def test_manual_value_not_overwritten(product: Product) -> None:
    # людина вже виставила Витрату води вручну
    attr = Attribute.objects.create(
        code="water_l", name="Витрата води",
        group=__import__("aispecs.services", fromlist=["_get_group"])._get_group("efficiency"),
        value_type=Attribute.ValueType.NUMBER,
    )
    ProductAttributeValue.objects.create(
        product=product, attribute=attr, value_number=Decimal("7"),
        source=ProductSource.MANUAL,
    )
    apply_job(_job(product))
    manual = ProductAttributeValue.objects.get(product=product, attribute=attr)
    assert manual.value_number == Decimal("7")  # ручне недоторкане
    assert manual.source == ProductSource.MANUAL


def test_cannot_apply_twice(product: Product) -> None:
    job = _job(product)
    apply_job(job)
    with pytest.raises(ApplyError):
        apply_job(job)


def test_reject(product: Product) -> None:
    job = _job(product)
    reject_job(job)
    job.refresh_from_db()
    assert job.status == SpecHarvestJob.Status.REJECTED
    assert not ProductAttributeValue.objects.filter(product=product).exists()
