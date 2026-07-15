"""Без ANTHROPIC_API_KEY усе працює на DryRunClient — і це не «деградація», а вимога.

Тести МАЮТЬ проходити без ключа й без мережі (принцип, успадкований від
translation/tests). Тому фабрика клієнта без ключа віддає DryRunClient, який
стрімить детерміновану заглушку з нульовою вартістю.
"""

from __future__ import annotations

import pytest

from assistant import conf
from assistant.client import DryRunClient, get_client
from assistant.models import ChatMessage, ChatSession
from assistant.tests._stream import collect, types

pytestmark = pytest.mark.django_db


def test_get_client_without_key_is_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Немає ключа → DryRunClient, і він чесно позначає себе `is_dry_run`."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = get_client()
    assert isinstance(client, DryRunClient)
    assert client.is_dry_run is True


def test_dry_run_streams_and_persists_zero_cost() -> None:
    """Повний прохід чату на dry-run: сесія відкрилась, відповідь застрімилась, вартість $0."""
    events = collect(message="Порадь холодильник", locale="uk")

    kinds = types(events)
    assert "session" in kinds
    assert "token" in kinds  # заглушка все одно стрімить текст
    assert kinds[-1] == "done"

    # Повідомлення записались, але грошей не витрачено — бюджет чіпати не можна.
    session = ChatSession.objects.get()
    assert ChatMessage.objects.filter(session=session).exists()
    assert session.cost_usd == 0

    # dry-run НЕ впирається в місячний бюджет (інакше тести залежали б від залишку).
    assert conf.monthly_budget_usd() > 0
