"""
Ручне схвалення — не опція, а конструкція (TRANSLATION.md §6.1).

Машинний переклад НЕ потрапляє в каталог, поки людина не натисне «Схвалити».
Це перевіряється тут, і саме це замовник просив.
"""

from __future__ import annotations

import pytest

from translation.client import DryRunClient, get_client
from translation.models import TranslationEntry, TranslationKind, TranslationStatus
from translation.services import apply_approved, collect_dictionary, translate_pending

pytestmark = pytest.mark.django_db


def test_machine_translation_never_reaches_ru_column(attribute, option, fake_client):
    """MACHINE → у _ru порожньо. І крапка."""
    collect_dictionary()
    translate_pending(client=fake_client)

    entry = TranslationEntry.objects.get(kind=TranslationKind.ATTRIBUTE_VALUE, source_text="Чорний")
    assert entry.status == TranslationStatus.MACHINE
    assert entry.target_text == "Черный"  # чернетка є
    assert entry.published_text == ""  # опублікованого — немає

    option.refresh_from_db()
    assert option.value_ru in ("", None), "машинний переклад просочився у вітрину!"

    # apply_approved теж не має його підхопити — він не схвалений.
    assert apply_approved() == 0
    option.refresh_from_db()
    assert option.value_ru in ("", None)


def test_approve_publishes_to_ru_column(attribute, option, fake_client, approve):
    collect_dictionary()
    translate_pending(client=fake_client)
    entry = TranslationEntry.objects.get(kind=TranslationKind.ATTRIBUTE_VALUE, source_text="Чорний")

    approve(entry)

    entry.refresh_from_db()
    option.refresh_from_db()
    assert entry.status == TranslationStatus.APPROVED
    assert entry.published_text == "Черный"
    assert entry.published_at is not None
    assert option.value_ru == "Черный"  # ось тепер — вітрина


def test_editor_edit_then_approve_publishes_the_edit(attribute, option, fake_client, approve):
    """Редактор править target_text; у вітрину йде саме його правка, а не машинний варіант."""
    collect_dictionary()
    translate_pending(client=fake_client)
    entry = TranslationEntry.objects.get(kind=TranslationKind.ATTRIBUTE_VALUE, source_text="Чорний")

    entry.target_text = "Чёрный"  # редактор наполіг
    entry.save(update_fields=["target_text"])
    approve(entry)

    option.refresh_from_db()
    assert option.value_ru == "Чёрный"


def test_failed_translation_is_not_approvable_content(make_product):
    """FAILED не має published_text і не потрапляє у вітрину навіть через apply_approved."""
    from translation.services import collect_targets
    from translation.tests.conftest import FakeClient

    evil = FakeClient(mangle=lambda uk: "Мощность 2000 Па")  # число підмінено
    p = make_product("Робот-пилосос Xiaomi S20", sku="F-1", short_description="Потужність 2700 Па")
    collect_targets({TranslationKind.PRODUCT_SHORT_DESCRIPTION})
    translate_pending(kind=TranslationKind.PRODUCT_SHORT_DESCRIPTION, client=evil)

    entry = TranslationEntry.objects.get(kind=TranslationKind.PRODUCT_SHORT_DESCRIPTION)
    assert entry.status == TranslationStatus.FAILED
    assert "numbers" in entry.validation_errors

    assert apply_approved() == 0
    p.refresh_from_db()
    assert p.short_description_ru in ("", None)


def test_apply_approved_is_idempotent_selfhealing(attribute, option, fake_client, approve):
    """Впав вочер / відкотилась транзакція → apply_approved() полагодить вітрину."""
    collect_dictionary()
    translate_pending(client=fake_client)
    entry = TranslationEntry.objects.get(kind=TranslationKind.ATTRIBUTE_VALUE, source_text="Чорний")
    approve(entry)

    # Симулюємо втрачений write-back.
    option.refresh_from_db()
    option.value_ru = ""
    option.save(update_fields=["value_ru"])

    assert apply_approved() >= 1
    option.refresh_from_db()
    assert option.value_ru == "Черный"

    # Другий виклик нічого не переписує — вітрина вже збігається.
    assert apply_approved() == 0


# ---------------------------------------------------------------------------
# DRY-RUN без ключа
# ---------------------------------------------------------------------------


def test_no_api_key_falls_back_to_dry_run(settings):
    """Без ANTHROPIC_API_KEY не падаємо — повертаємо заглушку і пишемо в лог."""
    settings.ANTHROPIC_API_KEY = ""
    client = get_client()
    assert isinstance(client, DryRunClient)


def test_dry_run_costs_nothing_and_changes_no_catalog(attribute, option, settings):
    settings.ANTHROPIC_API_KEY = ""
    collect_dictionary()
    stats = translate_pending(dry_run=True)

    assert stats["entries"] > 0
    entries = TranslationEntry.objects.filter(status=TranslationStatus.MACHINE)
    assert all(e.cost_usd == 0 for e in entries)

    option.refresh_from_db()
    assert option.value_ru in ("", None)  # dry-run каталог не чіпає
