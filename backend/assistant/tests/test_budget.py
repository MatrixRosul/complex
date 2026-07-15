"""Місячний hard-cap: вичерпаний бюджет → м'яка відмова, БЕЗ ретраю і БЕЗ звернення до моделі.

Публічний LLM-ендпоінт без стелі — це відкритий гаманець. Стеля тут не «на майбутнє»:
один бот за ніч здатен спалити місячний бюджет. При перевищенні асистент дає телефон
менеджера, а не падає з 500 і не повторює запит (повтор лише спалив би ще один виклик).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from assistant import conf
from assistant.models import ChatMessage, ChatSession
from assistant.tests._stream import codes, collect, types

pytestmark = pytest.mark.django_db


def _burn_budget(session: ChatSession) -> None:
    """Записати витрати, що дорівнюють місячному ліміту (щоб наступний запит уперся у стелю)."""
    ChatMessage.objects.create(
        session=session,
        role="assistant",
        content="попередня відповідь",
        cost_usd=conf.monthly_budget_usd(),
    )


def test_exhausted_budget_gives_soft_refusal(fake_client: object) -> None:
    """Витрати ≥ ліміту → error `budget`, і модель (fake_client) НЕ викликається жодного разу."""
    session = ChatSession.objects.create(locale="uk")
    _burn_budget(session)

    events = _collect_with_client(
        fake_client,
        message="Порадь пральну машину",
        session_token=str(session.public_token),
        locale="uk",
    )

    assert "budget" in codes(events)
    assert "token" not in types(events)
    # Головне: жодного звернення до моделі — стеля спрацювала ДО виклику.
    assert getattr(fake_client, "calls", []) == []


def test_dry_run_ignores_budget() -> None:
    """dry-run бюджет не витрачає, тому в стелю не впирається (інакше тести залежали б від залишку)."""
    session = ChatSession.objects.create(locale="uk")
    # cost_usd — Decimal(8,6), стеля ~99.99. Беремо значення, що вже перевищує бюджет ($50),
    # але вміщається в поле: сенс тесту — «навіть при перевитраті dry-run не блокується».
    ChatMessage.objects.create(
        session=session,
        role="assistant",
        content="дорога відповідь",
        cost_usd=min(conf.monthly_budget_usd() + Decimal("1"), Decimal("99.999999")),
    )
    events = collect(message="ще питання", session_token=str(session.public_token), locale="uk")
    # dry-run відповідає нормально, попри «перевитрату».
    assert "budget" not in codes(events)
    assert "token" in types(events)


def _collect_with_client(client: object, **kwargs: object) -> list[dict[str, object]]:
    """Як _stream.collect, але з переданим (не dry-run) клієнтом."""
    import json

    from asgiref.sync import async_to_sync

    from assistant.services import chat

    async def _run() -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        async for chunk in chat.run_chat(client=client, **kwargs):  # type: ignore[arg-type]
            line = chunk.decode().removeprefix("data: ").strip()
            if line:
                events.append(json.loads(line))
        return events

    return async_to_sync(_run)()
