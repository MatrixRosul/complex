"""
Роутер ШІ-асистента: один ендпоінт, який віддає SSE-стрім.

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ 🔴 @router.post БЕЗ response=-СХЕМИ — І ЦЕ НЕ НЕДОГЛЯД                               ║
║                                                                                      ║
║ ninja віддає HttpResponseBase як є ТІЛЬКИ тоді, коли response= не оголошено           ║
║ (ninja/operation.py:352-361: «if isinstance(result, HttpResponseBase): return         ║
║ result»). Варто написати response=ChatOut — і ninja спробує серіалізувати наш          ║
║ StreamingHttpResponse у pydantic-модель. Стріму не буде.                              ║
║ Через це ж у schemas.py немає ChatOut: віддавати тут нічого, крім самого стріму.       ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

Заголовки. Cache-Control: no-cache — щоб проксі не віддавав учорашню розмову. Свій
X-Accel-Buffering: no — щоб Caddy/nginx не БУФЕРИЗУВАВ відповідь: інакше він чесно
дочекається кінця стріму й віддасть усе пачкою, і весь async-код вище стане марним
рівно так само, як стало б із синхронним генератором.

В'юха нічого не вирішує: дістає IP, кличе сервіс, обгортає в StreamingHttpResponse.
Уся логіка (гарди, бюджет, tool-loop, персист) — у services/chat.py.

⚠️ У config/api.py роутер уже змонтований під /assistant/ — цей модуль існував заглушкою
   саме тому, що без нього Django не стартував узагалі.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest, StreamingHttpResponse
from ninja import Router
from ninja.throttling import AnonRateThrottle, BaseThrottle

from assistant.schemas import ChatIn
from assistant.services import chat

log = logging.getLogger(__name__)

__all__ = ["CHAT_THROTTLE", "router"]

router = Router(tags=["assistant"])

#: Публічний неавторизований ендпоінт, за яким стоїть платна модель. Без тротлінгу це
#: просто відкритий гаманець: 20/хв на IP — стеля, за якою вже видно не людину, а скрипт.
#: (Патерн — delivery/api.py:50. Це не єдиний рубіж: є ще ліміт повідомлень на сесію
#: і місячний hard-cap у сервісі.)
#: Анотація list[BaseThrottle] — не косметика: без неї mypy лається на інваріантність
#: list[AnonRateThrottle] (як зараз у delivery/api.py:50).
CHAT_THROTTLE: list[BaseThrottle] = [AnonRateThrottle("20/m")]


def _client_ip(request: HttpRequest) -> str:
    """IP відвідувача. Порядок — як у django-axes (config/settings/base.py:221):
    спершу X-Forwarded-For (ми за проксі), потім REMOTE_ADDR.

    Сирий IP далі не їде: сервіс кладе в БД лише sha256(IP + SECRET_KEY)[:32].
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return str(request.META.get("REMOTE_ADDR", "") or "")


@router.post("/chat", throttle=CHAT_THROTTLE, summary="Чат з асистентом (SSE)")
async def chat_stream(request: HttpRequest, payload: ChatIn) -> StreamingHttpResponse:
    """POST /api/v1/assistant/chat → text/event-stream.

    Події: session · thinking · token · tool · products · link · done · error.
    Помилки теж їдуть подією `error` всередині стріму — 200 і потік, а не 500: заголовки
    віддано ще до першого токена, і перетворити виняток на код відповіді вже нікуди.

    POST, а не GET, — тому фронт і не може взяти EventSource (той уміє лише GET):
    там fetch + res.body.getReader().
    """
    stream = chat.run_chat(
        message=payload.message,
        session_token=payload.session_token,
        locale=payload.lang,
        ip=_client_ip(request),
    )

    response = StreamingHttpResponse(stream, content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    # Без цього Caddy/nginx збере весь стрім у буфер і віддасть однією пачкою в кінці.
    response["X-Accel-Buffering"] = "no"
    return response
