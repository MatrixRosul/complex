"""Гварди діалогу: довгий інпут, ліміт повідомлень, порожнє питання.

Це друга лінія оборони поверх rate-limit (20 запитів/хв на IP). Кожен гвард
мусить дати м'яку SSE-подію `error` + `done`, а НЕ виняток: в'юха вже віддала
200 і text/event-stream, тому падіння тут обірвало б стрім на півслові.
"""

from __future__ import annotations

import pytest

from assistant import conf
from assistant.models import ChatMessage, ChatSession
from assistant.tests._stream import codes, collect, types

pytestmark = pytest.mark.django_db


def test_empty_message_is_rejected_softly() -> None:
    """Порожнє питання → error, не виняток."""
    events = collect(message="   ", locale="uk")
    assert "error" in types(events)
    assert types(events)[-1] == "done"


def test_too_long_input_is_rejected() -> None:
    """Інпут довший за ASSISTANT_MAX_INPUT_CHARS → error `too_long`, без звернення до моделі."""
    events = collect(message="x" * (conf.max_input_chars() + 1), locale="uk")
    assert "too_long" in codes(events)
    # Жодного токена відповіді — гвард спрацював ДО моделі.
    assert "token" not in types(events)


def test_message_limit_gives_soft_refusal() -> None:
    """Сесія, що вичерпала ліміт повідомлень, отримує м'яку відмову з контактом менеджера."""
    session = ChatSession.objects.create(locale="uk", message_count=conf.max_messages())
    events = collect(
        message="ще одне питання",
        session_token=str(session.public_token),
        locale="uk",
    )
    assert "too_many" in codes(events)
    assert "token" not in types(events)
    # Нове повідомлення понад ліміт у журнал не пишеться.
    assert ChatMessage.objects.filter(session=session).count() == 0
