"""🔴 Регресійний тест на головний блокер архітектури асистента.

Проєкт крутиться на ASGI (gunicorn + UvicornWorker). Коли Django отримує в
`StreamingHttpResponse` *синхронний* ітератор, воно робить
`await sync_to_async(list)(streaming_content)` — тобто вичерпує генератор
ДО КІНЦЯ і лише потім починає віддавати чанки. Для чату це означає: жодного
токена, доки вся відповідь (усі ітерації tool-loop) не завершиться. Стрімінгу
немає.

Найпідступніше: `runserver` — WSGI, тому в деві sync-генератор «працює».
Баг видно ТІЛЬКИ в проді. Цей тест — єдине, що ловить його на CI.
"""

from __future__ import annotations

import inspect

from assistant.services import chat


def test_run_chat_is_async_generator() -> None:
    """`run_chat` МУСИТЬ бути async-генератором, інакше стрімінгу немає в проді."""
    assert inspect.isasyncgenfunction(chat.run_chat), (
        "run_chat став синхронним — під ASGI Django ковтне його через "
        "sync_to_async(list)(...) і чат перестане стрімити В ПРОДІ "
        "(у деві на WSGI-runserver це не видно). Поверни async def + yield."
    )
