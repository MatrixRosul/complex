"""
ГОЛОВНИЙ ТЕСТ МОДУЛЯ: словниковий підхід (TRANSLATION.md §2).

Він доводить не «код працює», а що АРХІТЕКТУРА правильна:
  • 100 товарів з тією самою характеристикою дають 1 запис у черзі, а не 100;
  • «Чорний» фізично не може перекластись двома способами — він лежить в ОДНОМУ рядку БД;
  • повторний прогін не коштує нічого.

Якщо цей файл почервонів — значить хтось почав перекладати характеристики ВСЕРЕДИНІ
товару, і рахунок виріс у ~140 разів, а фасетний фільтр розсипався на дублікати.
"""

from __future__ import annotations

import pytest

from catalog.models import Attribute, AttributeOption, ProductAttributeValue
from translation.models import TranslationEntry, TranslationKind, TranslationStatus
from translation.services import collect_dictionary, translate_pending

pytestmark = pytest.mark.django_db


def test_100_products_same_attribute_gives_one_queue_entry(
    make_product, attribute, option, attr_group
):
    """100 товарів × «Колір виробу: Чорний» → 2 записи (назва + значення), НЕ 200."""
    for i in range(100):
        p = make_product(f"Холодильник двокамерний Bosch KGN{i}X", sku=f"SKU-{i}")
        ProductAttributeValue.objects.create(
            product=p, attribute=attribute, option=option, value_string=""
        )

    assert ProductAttributeValue.objects.count() == 100

    collect_dictionary()

    name_entries = TranslationEntry.objects.filter(kind=TranslationKind.ATTRIBUTE_NAME)
    value_entries = TranslationEntry.objects.filter(kind=TranslationKind.ATTRIBUTE_VALUE)

    # Ось воно: 100 товарів — і РІВНО ОДИН запис на назву характеристики.
    assert name_entries.filter(source_text="Колір виробу").count() == 1
    assert value_entries.filter(source_text="Чорний").count() == 1

    # Наївний підхід дав би 100 + 100. Різниця у 100× на ОДНІЙ характеристиці.
    assert value_entries.count() < 5


def test_dictionary_translation_is_structurally_consistent(
    make_product, attribute, option, fake_client, approve
):
    """Один рядок БД → один переклад → у всіх 100 товарів той самий «Черный».

    Це і є структурна гарантія консистентності: не «модель постаралась», а
    «двох варіантів не існує, бо джерело одне».
    """
    for i in range(100):
        p = make_product(f"Холодильник двокамерний Bosch KGN{i}X", sku=f"SKU-{i}")
        ProductAttributeValue.objects.create(product=p, attribute=attribute, option=option)

    collect_dictionary()
    translate_pending(kind=TranslationKind.ATTRIBUTE_VALUE, client=fake_client)

    entry = TranslationEntry.objects.get(kind=TranslationKind.ATTRIBUTE_VALUE, source_text="Чорний")
    assert entry.target_text == "Черный"

    approve(entry)
    option.refresh_from_db()
    assert option.value_ru == "Черный"

    # 100 товарів дивляться на ОДИН рядок опції → двох варіантів фізично немає.
    assert AttributeOption.objects.filter(value_ru="Черный").count() == 1
    assert AttributeOption.objects.exclude(value_ru="Черный").exclude(value_ru="").count() == 0


def test_only_one_api_request_for_whole_dictionary(make_product, attribute, option, fake_client):
    """Увесь словник з ~5 рядків їде в 1-2 запити, а не в 100."""
    for i in range(100):
        p = make_product(f"Холодильник двокамерний Bosch KGN{i}X", sku=f"SKU-{i}")
        ProductAttributeValue.objects.create(product=p, attribute=attribute, option=option)

    collect_dictionary()
    translate_pending(client=fake_client)

    # BATCH_SIZES: 50 сегментів на запит. Словник тут — одиниці рядків.
    assert len(fake_client.requests) <= 4
    # І жодного товару в запитах — тільки словник.
    assert "Холодильник двокамерний Bosch KGN0X" not in fake_client.sent_texts


def test_reimport_is_free(attribute, fake_client):
    """Повторний collect із незмінним джерелом → 0 нових записів, 0 запитів, $0.

    Саме це ламає наївний підхід: там реімпорт прайсу = повторна оплата всього.
    """
    collect_dictionary()
    before = TranslationEntry.objects.count()
    hashes = set(TranslationEntry.objects.values_list("source_hash", flat=True))

    translate_pending(client=fake_client)
    n_requests = len(fake_client.requests)

    collect_dictionary()  # ще раз, джерело не змінилось

    assert TranslationEntry.objects.count() == before
    assert set(TranslationEntry.objects.values_list("source_hash", flat=True)) == hashes

    # Нічого нового не з'явилось у PENDING → другий прогін не зробить запитів.
    fresh = FakeCounter(fake_client)
    translate_pending(client=fresh)
    assert len(fresh.requests) == 0, "реімпорт не має коштувати нічого"
    assert n_requests > 0


class FakeCounter:
    """Обгортка, щоб порахувати запити другого прогону окремо."""

    is_dry_run = False

    def __init__(self, inner):
        self.inner = inner
        self.requests = []

    def translate(self, request, glossary_block):
        self.requests.append(request)
        return self.inner.translate(request, glossary_block)

    def translate_many(self, requests, glossary_block):
        return [self.translate(r, glossary_block) for r in requests]


def test_non_translatable_values_never_reach_the_model(attr_group, fake_client):
    """«284», «Gorenje», «A+++» — ~35% значень. Платити за їх «переклад» не треба."""
    attr = Attribute.objects.create(
        code="brandname", name="Виробник", group=attr_group, value_type="option"
    )
    for i, val in enumerate(["Gorenje", "284", "A+++", "Нержавіюча сталь"]):
        AttributeOption.objects.create(attribute=attr, value=val, slug=f"v{i}")

    collect_dictionary()
    translate_pending(client=fake_client)

    sent = fake_client.sent_texts
    assert "Gorenje" not in sent
    assert "284" not in sent
    assert "A+++" not in sent
    assert "Нержавіюча сталь" in sent  # а оце — перекладаємо

    skipped = TranslationEntry.objects.filter(status=TranslationStatus.DO_NOT_TRANSLATE)
    assert set(skipped.values_list("source_text", flat=True)) >= {"Gorenje", "284", "A+++"}
