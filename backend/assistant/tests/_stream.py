"""Хелпер: прокрутити async-генератор `run_chat` із СИНХРОННОГО тесту.

У проєкті немає ні pytest-asyncio, ні anyio (жодного async-тесту), і тягнути
нову залежність заради кількох тестів не варто. `asgiref.async_to_sync` уже є
(Django-залежність) і робить рівно те, що треба: зганяє async-генератор у список.
"""

from __future__ import annotations

import json
from typing import Any

from asgiref.sync import async_to_sync

from assistant.client import DryRunClient
from assistant.services import chat


def collect(**kwargs: Any) -> list[dict[str, Any]]:
    """Прокрутити run_chat (dry-run) у список розпарсених SSE-подій."""

    async def _run() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for chunk in chat.run_chat(client=DryRunClient(), **kwargs):
            line = chunk.decode().removeprefix("data: ").strip()
            if line:
                events.append(json.loads(line))
        return events

    return async_to_sync(_run)()


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def codes(events: list[dict[str, Any]]) -> list[str]:
    return [e.get("code", "") for e in events if e["type"] == "error"]
