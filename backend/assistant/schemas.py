"""
Схеми ендпоінта чату. Тут ЛИШЕ вхід — виходу-схеми немає і бути не може.

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ ЧОМУ НЕМАЄ ChatOut                                                                    ║
║                                                                                      ║
║ Відповідь чату — це SSE-стрім (text/event-stream), а не JSON-об'єкт. Якби ми оголосили ║
║ у @router.post(..., response=ChatOut), ninja спробував би СЕРІАЛІЗУВАТИ                ║
║ StreamingHttpResponse у цю схему — і з'їв би її (ninja/operation.py:360 віддає         ║
║ HttpResponseBase як є ТІЛЬКИ за відсутності response=). Тому тут — тільки вхід.        ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

Довжину `message` НЕ валідуємо тут (max_length у Schema дав би 422 з технічним тілом
посеред чату). Ліміт ASSISTANT_MAX_INPUT_CHARS перевіряє сервіс і віддає людську
SSE-подію `error` — користувач бачить пояснення в чаті, а не зламаний запит.
"""

from __future__ import annotations

from ninja import Schema

__all__ = ["ChatIn"]


class ChatIn(Schema):
    """Одне повідомлення користувача до Лисички."""

    #: Публічний токен сесії (UUID). Порожньо/невідомий → сервіс заводить нову сесію.
    session_token: str | None = None
    message: str
    #: Локаль фронту: `ua`/`uk`/`ru`. Нормалізацію робить catalog.services.lang.normalize_lang.
    lang: str = "uk"
