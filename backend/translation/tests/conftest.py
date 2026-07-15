"""
Фікстури тестів перекладу.

⚠️ ЖОДНОГО МЕРЕЖЕВОГО ВИКЛИКУ. Клієнт Claude завжди мокається (FakeClient).
   Тести МАЮТЬ проходити без ANTHROPIC_API_KEY — і вони його не читають узагалі.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from catalog.models import (
    Attribute,
    AttributeGroup,
    AttributeOption,
    Brand,
    Category,
    Product,
    Unit,
)
from translation.client import TranslationRequest, TranslationResponse, Usage

# Маленький «перекладач»: рівно те, що потрібно тестам. Реальна модель тут не потрібна —
# нас цікавить ПАЙПЛАЙН (що поїхало в модель, що з ним сталось на зворотному шляху),
# а не якість Claude.
RU = {
    "Колір виробу": "Цвет изделия",
    "Чорний": "Черный",
    "Сенсорне": "Сенсорное",
    "Тип управління": "Тип управления",
    "Варильна поверхня газова": "Варочная поверхность газовая",
    "Холодильник двокамерний": "Холодильник двухкамерный",
    "міс": "мес",
    "Побутова техніка": "Бытовая техника",
    "Гарантійний термін": "Гарантийный срок",
    "Потужне всмоктування <0>2700 Па</0> прибирає навіть шерсть.": (
        "Мощное всасывание <0>2700 Па</0> убирает даже шерсть."
    ),
    "Час роботи до 180 хв": "Время работы до 180 мин",
    "Фото режиму вологого прибирання": "Фото режима влажной уборки",
    "Купуйте <0>тут</0>": "Покупайте <0>здесь</0>",
    "Опис": "Описание",
}


class FakeClient:
    """Мок Claude: словниковий переклад + запис усього, що йому давали.

    `requests` дозволяє тестам перевірити НЕ результат, а сам ФАКТ — що саме поїхало
    в модель. Наприклад: що «Bosch PNK6B2P40R» у запиті не з'явився взагалі.
    """

    is_dry_run = False

    def __init__(self, mapping: dict[str, str] | None = None, *, notes=None, mangle=None):
        self.mapping = {**RU, **(mapping or {})}
        self.notes = notes or {}
        self.mangle = mangle  # callable(uk) -> ru, щоб зімітувати ЗІПСОВАНУ відповідь
        self.requests: list[TranslationRequest] = []

    def _ru(self, uk: str) -> str:
        if self.mangle:
            return self.mangle(uk)
        return self.mapping.get(uk, uk)

    def translate(self, request: TranslationRequest, glossary_block: str) -> TranslationResponse:
        self.requests.append(request)
        return TranslationResponse(
            custom_id=request.custom_id,
            translations={s["id"]: self._ru(s["uk"]) for s in request.segments},
            notes={s["id"]: self.notes[s["uk"]] for s in request.segments if s["uk"] in self.notes},
            usage=Usage(
                model=request.model,
                input_tokens=100,
                output_tokens=100,
                cost_usd=Decimal("0.001"),
            ),
        )

    def translate_many(self, requests, glossary_block):
        return [self.translate(r, glossary_block) for r in requests]

    # -- зручні перевірки ---------------------------------------------------

    @property
    def sent_texts(self) -> list[str]:
        """Усе, що РЕАЛЬНО поїхало в модель."""
        return [s["uk"] for r in self.requests for s in r.segments]


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def approve(django_capture_on_commit_callbacks):
    """Схвалити запис ТАК, ЯК ЦЕ РОБИТЬ АДМІНКА — разом із on_commit-хуком.

    TranslationEntry.approve() робить write-back через transaction.on_commit(), щоб відкат
    транзакції не лишив вітрину з текстом, якого немає в черзі. Під pytest-django кожен
    тест — це транзакція, яка відкочується, тому on_commit сам по собі НЕ спрацював би,
    і тест «переклад дійшов до _ru» був би зеленим брехуном.
    """

    def _approve(entry, user=None):
        with django_capture_on_commit_callbacks(execute=True):
            entry.approve(user)
        entry.refresh_from_db()
        return entry

    return _approve


@pytest.fixture
def category(db):
    return Category.objects.create(
        external_id="cat-1", path="1", name="Побутова техніка", slug="pobutova-tehnika"
    )


@pytest.fixture
def brand(db):
    return Brand.objects.create(name="Bosch", slug="bosch")


@pytest.fixture
def attr_group(db):
    return AttributeGroup.objects.create(code="main", name="Основні")


@pytest.fixture
def attribute(db, attr_group):
    return Attribute.objects.create(
        code="color", name="Колір виробу", group=attr_group, value_type="option"
    )


@pytest.fixture
def option(db, attribute):
    return AttributeOption.objects.create(attribute=attribute, value="Чорний", slug="chornyi")


@pytest.fixture
def unit(db):
    return Unit.objects.create(code="month", name="міс")


@pytest.fixture
def make_product(db, category, brand):
    def _make(name: str, sku: str, **kwargs) -> Product:
        return Product.objects.create(
            sku=sku,
            name=name,
            slug=sku.lower(),
            category=category,
            brand=kwargs.pop("brand", brand),
            base_price=Decimal("1000"),
            price=Decimal("1000"),
            **kwargs,
        )

    return _make
