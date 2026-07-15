"""
Клієнт Claude для чату: АСИНХРОННИЙ і СТРІМІНГОВИЙ. Написаний з нуля.

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ ЧОМУ НЕ «ЯК У translation/client.py»                                                  ║
║                                                                                      ║
║ Там стрімінгу немає ВЗАГАЛІ: лише messages.create / batches / count_tokens, і все     ║
║ синхронне. Скопіювати звідти нічого — беремо тільки патерн (dry-run без ключа,        ║
║ ретраї на боці SDK, Usage з підрахунком вартості) і перевикористовуємо два готові     ║
║ шматки: translation.client.Usage (він generic, має from_api) і conf.compute_cost.     ║
║                                                                                      ║
║ Чому саме async. Проєкт крутиться на ASGI (infra/Dockerfile.backend: gunicorn +       ║
║ UvicornWorker). Django, отримавши СИНХРОННИЙ ітератор у StreamingHttpResponse, робить ║
║ await sync_to_async(list)(streaming_content) — тобто ВИЧЕРПУЄ генератор до кінця і    ║
║ лише потім віддає відповідь. Стрімінгу немає, користувач дивиться в порожнечу.        ║
║ Найпідступніше: runserver — WSGI, і в деві це «працює». Баг видно тільки в проді.     ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

Контракт (BaseChatClient): stream() — асинхронний генератор подій.
    ThinkingDelta → «лисичка думає» (SSE-подія thinking)
    TextDelta     → токен відповіді
    Completed     → фінал ітерації: сирі content-блоки (для messages), stop_reason, Usage

Completed.content — це РІВНО те, що треба покласти назад у messages як
{"role": "assistant", "content": completed.content}: у справжнього клієнта це блоки SDK
(включно з tool_use), у dry-run — один текстовий блок-словник. І те, й те API приймає.

Ретраї. max_retries налаштовуємо НА КЛІЄНТІ — SDK сам ретраїть 408/409/429/5xx з
експоненційним backoff. Свій ретрай зверху НЕ пишемо: у живому HTTP-запиті це дало б
до 9 спроб і хвилини очікування на тому боці, де людина дивиться в чат.
4xx крім 429 не ретраїмо взагалі — це наш баг у запиті, а не збій мережі.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from django.conf import settings

from assistant import conf
from translation.client import Usage

log = logging.getLogger(__name__)

__all__ = [
    "AssistantError",
    "AsyncAnthropicClient",
    "BaseChatClient",
    "BudgetExceeded",
    "ChatEvent",
    "Completed",
    "DryRunClient",
    "TextDelta",
    "ThinkingDelta",
    "get_client",
]


class AssistantError(RuntimeError):
    """Будь-яка помилка асистента, яку в'юха має перетворити на SSE-подію error."""


class BudgetExceeded(AssistantError):
    """Місячний hard-cap вичерпано. Ретраїти безглуздо — це не збій, а стеля."""


# ---------------------------------------------------------------------------
# Події стріму
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThinkingDelta:
    """Модель думає. Текст — summarized-виклад роздумів (display="summarized")."""

    text: str


@dataclass(frozen=True)
class TextDelta:
    """Черговий шматок відповіді користувачеві."""

    text: str


@dataclass(frozen=True)
class Completed:
    """Кінець однієї ітерації діалогу з моделлю."""

    content: list[Any]  # сирі блоки → messages[{"role": "assistant", "content": ...}]
    text: str  # зібраний текст відповіді (для ChatMessage.content)
    stop_reason: str  # end_turn · tool_use · max_tokens · refusal
    usage: Usage
    stop_details: Any | None = None  # заповнене ЛИШЕ при stop_reason="refusal"
    tool_uses: list[Any] = field(default_factory=list)  # блоки tool_use цієї ітерації


ChatEvent = ThinkingDelta | TextDelta | Completed


# ---------------------------------------------------------------------------
# Контракт
# ---------------------------------------------------------------------------


class BaseChatClient(Protocol):
    """Свій Protocol, а не translation.client.BaseClient: той про translate/translate_many
    і для чату непридатний ні за сигнатурами, ні за синхронністю."""

    is_dry_run: bool

    def stream(
        self,
        *,
        system: Any,
        messages: Sequence[Any],
        tools: Sequence[Any] | None = ...,
        model: str | None = ...,
        max_tokens: int | None = ...,
        effort: str | None = ...,
    ) -> AsyncIterator[ChatEvent]: ...

    async def count_tokens(
        self,
        *,
        system: Any,
        messages: Sequence[Any],
        tools: Sequence[Any] | None = ...,
        model: str | None = ...,
    ) -> int: ...


# ---------------------------------------------------------------------------
# DRY-RUN
# ---------------------------------------------------------------------------

DRY_RUN_REPLY = (
    "Я зараз працюю без доступу до моделі, тому справжньої відповіді дати не можу. "
    "Зателефонуйте, будь ласка, менеджеру — він підбере техніку швидше за мене."
)


class DryRunClient:
    """Заглушка без мережі й без витрат. Саме вона робить тести зеленими без ANTHROPIC_API_KEY.

    Навмисно НЕ вигадує змістовну відповідь: якби вигадувала, тест «асистент не галюцинує»
    перевіряв би заглушку, а не пайплайн. І з тієї ж причини не імітує виклики інструментів —
    stop_reason завжди end_turn, цикл tool-loop завершується на першій ітерації.
    """

    is_dry_run = True

    async def stream(
        self,
        *,
        system: Any,
        messages: Sequence[Any],
        tools: Sequence[Any] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[ChatEvent]:
        model_name = model or conf.DEFAULT_MODEL
        log.warning(
            "ASSISTANT dry-run: модель НЕ викликається, відповідь-заглушка, витрати $0. "
            "Це не помилка — але й не відповідь асистента."
        )
        yield ThinkingDelta(text="")
        yield TextDelta(text=DRY_RUN_REPLY)
        yield Completed(
            content=[{"type": "text", "text": DRY_RUN_REPLY}],
            text=DRY_RUN_REPLY,
            stop_reason="end_turn",
            usage=Usage(model=model_name),  # cost_usd = Decimal("0")
        )

    async def count_tokens(
        self,
        *,
        system: Any,
        messages: Sequence[Any],
        tools: Sequence[Any] | None = None,
        model: str | None = None,
    ) -> int:
        # 0 < min_cache_prefix → гейт кешу не відкривається, system лишається без
        # cache_control. Для тестів це і потрібно: жодної мережі й повна детермінованість.
        return 0


# ---------------------------------------------------------------------------
# Claude (AsyncAnthropic)
# ---------------------------------------------------------------------------


class AsyncAnthropicClient:
    """Реальний клієнт. Один інстанс на процес — AsyncAnthropic тримає пул з'єднань."""

    is_dry_run = False

    def __init__(self, api_key: str, *, max_retries: int = 2) -> None:
        import anthropic

        self._anthropic = anthropic
        # Ретраї — на клієнті. SDK ретраїть 408/409/429/5xx сам; свого зверху не додаємо.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=max_retries)

    # -- параметри запиту ----------------------------------------------------

    def _params(
        self,
        *,
        system: Any,
        messages: Sequence[Any],
        tools: Sequence[Any] | None,
        model: str | None,
        max_tokens: int | None,
        effort: str | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model or conf.model(),
            "max_tokens": max_tokens or conf.MAX_TOKENS,
            "system": system,
            "messages": list(messages),
            # Без display="summarized" thinking-блоки приходять порожні, і стрім мовчить
            # рівно стільки, скільки модель думає (conf.THINKING).
            "thinking": dict(conf.THINKING),
            "output_config": {"effort": effort or conf.EFFORT},
        }
        if tools:
            params["tools"] = list(tools)
        return params

    def _wrap_api_error(self, exc: Exception) -> AssistantError:
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and 400 <= status < 500 and status != 429:
            # Наш баг у запиті (крива схема інструмента, зайвий параметр, побитий messages).
            # Ретраїти нема сенсу — впаде так само.
            log.error("ASSISTANT: %s від Claude — помилка в нашому запиті: %s", status, exc)
            return AssistantError(f"api_{status}: {exc}")
        log.warning("ASSISTANT: виклик Claude не вдався (%s): %s", status or "network", exc)
        return AssistantError(f"api_error: {exc}")

    # -- стрім ---------------------------------------------------------------

    async def stream(
        self,
        *,
        system: Any,
        messages: Sequence[Any],
        tools: Sequence[Any] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[ChatEvent]:
        params = self._params(
            system=system,
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            effort=effort,
        )
        model_name: str = params["model"]

        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    delta = event.delta
                    if delta.type == "thinking_delta":
                        yield ThinkingDelta(text=delta.thinking)
                    elif delta.type == "text_delta":
                        yield TextDelta(text=delta.text)
                message = await stream.get_final_message()
        except self._anthropic.APIError as exc:
            raise self._wrap_api_error(exc) from exc

        text = "".join(b.text for b in message.content if b.type == "text")
        tool_uses = [b for b in message.content if b.type == "tool_use"]
        usage = Usage.from_api(model_name, message.usage)

        # Кеш: на другому й далі повідомленнях cache_read має бути > 0. Нуль — або «тихий
        # інвалідатор» у system (datetime.now(), uuid, ітерація по set), або префікс
        # коротший за min_cache_prefix (Opus 4.8 — 4096 токенів).
        if usage.cache_read_tokens == 0 and usage.cache_write_tokens == 0:
            log.debug("ASSISTANT: кеш не спрацював (%s). Перевір префікс tools+system.", model_name)

        yield Completed(
            content=list(message.content),
            text=text,
            stop_reason=message.stop_reason or "end_turn",
            usage=usage,
            stop_details=getattr(message, "stop_details", None),
            tool_uses=tool_uses,
        )

    # -- утиліти -------------------------------------------------------------

    async def count_tokens(
        self,
        *,
        system: Any,
        messages: Sequence[Any],
        tools: Sequence[Any] | None = None,
        model: str | None = None,
    ) -> int:
        """Точний підрахунок префікса для гейту кешу.

        Саме токени, а не len(рядка): поріг min_cache_prefix — у токенах, і len() промахується
        на кирилиці в рази. tools рендеряться ПЕРЕД system, тому в префікс входять і вони —
        рахуємо разом.
        """
        params: dict[str, Any] = {
            "model": model or conf.model(),
            "system": system,
            "messages": list(messages),
        }
        if tools:
            params["tools"] = list(tools)
        try:
            result = await self._client.messages.count_tokens(**params)
        except self._anthropic.APIError as exc:
            # Гейт кешу — оптимізація, а не функціональність. Не можемо порахувати —
            # просто не ставимо cache_control, замість того щоб валити чат.
            log.warning("ASSISTANT: count_tokens не вдався: %s. Кеш цього разу без гейту.", exc)
            return 0
        return int(result.input_tokens)


# ---------------------------------------------------------------------------
# Фабрика
# ---------------------------------------------------------------------------


def get_client(*, dry_run: bool = False) -> BaseChatClient:
    """Без ключа не падаємо, а йдемо в dry-run. Тести МАЮТЬ проходити без ANTHROPIC_API_KEY."""
    api_key = (getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip()
    if dry_run or not api_key:
        if not dry_run:
            log.warning(
                "ASSISTANT: ANTHROPIC_API_KEY не заданий — працюю в DRY-RUN. Лисичка "
                "відповідатиме заглушкою і відправлятиме до менеджера."
            )
        return DryRunClient()
    return AsyncAnthropicClient(api_key)
