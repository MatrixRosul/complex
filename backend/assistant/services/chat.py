"""
Серце асистента: асинхронний tool-loop зі стрімінгом у SSE.

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ 🔴 ЧОМУ ТУТ УСЕ ASYNC — І ЧОМУ ЦЕ НЕ СТИЛЬ, А ЄДИНИЙ РОБОЧИЙ ВАРІАНТ                  ║
║                                                                                      ║
║ Прод крутиться на ASGI (infra/Dockerfile.backend: gunicorn + UvicornWorker). Django,  ║
║ отримавши СИНХРОННИЙ ітератор у StreamingHttpResponse, робить                         ║
║     for part in await sync_to_async(list)(self.streaming_content)                     ║
║ (django/http/response.py:544) — тобто ВИЧЕРПУЄ генератор до кінця і лише потім віддає ║
║ відповідь. Стрімінгу немає: людина дивиться в порожнечу 30 секунд, а потім отримує    ║
║ усе пачкою. Асинхронний генератор іде іншою гілкою (response.py:533, `async for`) —   ║
║ і саме він стрімить.                                                                  ║
║                                                                                      ║
║ Найпідступніше: `runserver` — WSGI, і в деві синхронна версія «працює». Баг видно      ║
║ ТІЛЬКИ в проді. Тому run_chat — справжній async generator, і в тестах це перевіряється ║
║ через inspect.isasyncgenfunction.                                                     ║
║                                                                                      ║
║ Наслідок: КОЖЕН дотик ORM / catalog.services.* / tools.dispatch — через               ║
║ sync_to_async(..., thread_sensitive=True). thread_sensitive не для краси: він тримає   ║
║ усі синхронні виклики в ОДНОМУ потоці, тож з'єднання з БД і транзакції поводяться так, ║
║ як Django очікує.                                                                     ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ 🔴 КЕШ: РІВНО 2 BREAKPOINT'И, І ДРУГИЙ ПЕРЕЇЖДЖАЄ, А НЕ НАКОПИЧУЄТЬСЯ                 ║
║                                                                                      ║
║ Ліміт API — 4 cache_control-блоки на запит. У нас: 1 на system (ttl 1h, ставить        ║
║ prompts.build_system) + 1 «рухомий» на останньому повідомленні кожної ітерації (5m).   ║
║ Якби ми на кожній ітерації ДОДАВАЛИ новий breakpoint, не знімаючи попередній, то на    ║
║ 4-й ітерації tool-loop прилетів би 400 — і чат падав би рівно на складних питаннях,    ║
║ тобто там, де він потрібен найбільше. Тому _move_cache_breakpoint() спершу знімає      ║
║ cache_control з усіх повідомлень і лише потім ставить на останнє.                      ║
║                                                                                      ║
║ Читання кешу від цього не страждає: API сам шукає збіг префікса на попередніх межах    ║
║ блоків (до 20 блоків назад), тож рухомий breakpoint дає cache_read на кожній ітерації. ║
║ Без нього кожна з 6 ітерацій перечитувала б усю зростаючу історію за повну ціну.       ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

Гілки stop_reason — це не «повнота», а чотири різні баги, якщо їх нема:
  * refusal    → модель відмовилась (класифікатор безпеки). stop_details заповнене ТІЛЬКИ
                 тут, у решті випадків None — звідси гарди перед читанням.
  * max_tokens → відповідь обрізана на півслові. Без цієї гілки користувач отримає
                 недописане речення і НІКОЛИ не дізнається, що це не вся відповідь.
  * tool_use   → продовжуємо цикл.
  * інше       → кінець ходу.

Помилка інструмента ніколи не летить нагору винятком: tools.dispatch() віддає
tool_result(is_error=True) з людським текстом, і модель має шанс виправитись у наступній
ітерації. Виняток убив би стрім посеред відповіді.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from assistant import conf, prompts, tools
from assistant.client import (
    AssistantError,
    BaseChatClient,
    BudgetExceeded,
    Completed,
    TextDelta,
    ThinkingDelta,
    get_client,
)
from assistant.models import ChatMessage, ChatRole, ChatSession, hash_ip
from catalog.services.lang import normalize_lang
from cms.models import PickupPoint

log = logging.getLogger(__name__)

__all__ = [
    "MAX_HISTORY_MESSAGES",
    "BudgetExceeded",
    "run_chat",
    "sse",
]

# Скільки останніх повідомлень сесії віддаємо моделі. 20 — це 10 обмінів: далі контекст
# коштує грошей, а користь падає (людина вже давно говорить про інший холодильник).
MAX_HISTORY_MESSAGES = 20

# Скільки карток товарів віддаємо фронту за одну ітерацію. Стеля тут, а не в tools:
# паралельних викликів може бути кілька, і сума карток здатна вирости несподівано.
MAX_PRODUCTS_PER_EVENT = 12


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


def sse(event: str, **payload: Any) -> bytes:
    """Одна SSE-подія: `data: {json}\\n\\n`, вже в bytes.

    Стрімимо bytes, а не str, свідомо: StreamingHttpResponse інакше кодував би кожен
    чанк сам, а ми хочемо контролювати рівно те, що йде в сокет.

    ensure_ascii=False — інакше кирилиця роздувається в \\uXXXX і трафік росте втричі
    на рівному місці.
    """
    body = json.dumps({"type": event, **payload}, ensure_ascii=False)
    return f"data: {body}\n\n".encode()


# Тексти відмов. НІЧОГО не вигадуємо про магазин: телефон беремо з cms.PickupPoint
# (реальні дані замовника), а якщо його там немає — просто не називаємо жодного.
_REFUSAL = "Вибачте, на це питання я відповісти не можу."
_TRUNCATED = "Відповідь вийшла надто довгою і обірвалась. Спробуйте запитати конкретніше."
_BUSY = "Зараз я не можу відповісти. "
_TOO_LONG = "Питання задовге. Напишіть, будь ласка, коротше — до {limit} символів."
_TOO_MANY = "Ця розмова вже довга. Почніть нову або зверніться до менеджера."
_ITERATIONS = "Не вдалося зібрати відповідь. Спробуйте сформулювати питання простіше."
_BROKEN = "Щось пішло не так на нашому боці. Спробуйте, будь ласка, ще раз."


def _manager_contact_sync() -> str:
    """«Зверніться до менеджера» + РЕАЛЬНИЙ телефон із cms.PickupPoint, якщо він там є.

    Свідомо не тримаємо телефон константою в коді: вигаданий або застарілий номер у чаті
    гірший за його відсутність. Немає в БД — не називаємо жодного.
    """
    phone = (
        PickupPoint.objects.filter(is_active=True)
        .exclude(phone="")
        .values_list("phone", flat=True)
        .first()
    )
    return (
        f"Зателефонуйте менеджеру: {phone}." if phone else "Зверніться, будь ласка, до менеджера."
    )


_manager_contact = sync_to_async(_manager_contact_sync, thread_sensitive=True)


# ---------------------------------------------------------------------------
# Облік витрат
# ---------------------------------------------------------------------------


@dataclass
class _Spend:
    """Сума usage за ВСІ ітерації одного питання: у БД це один рядок ChatMessage."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))

    def add(self, usage: Any) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read_tokens += usage.cache_read_tokens
        self.cache_write_tokens += usage.cache_write_tokens
        # cost_usd уже порахований у Usage.from_api через translation.conf.compute_cost
        # (Decimal, quantize до 0.000001). Своєї арифметики над цінами не заводимо.
        self.cost_usd += usage.cost_usd


def _month_spend_sync() -> Decimal:
    """Витрати асистента за поточний місяць. Патерн — з translation/services/runner.py:176."""
    start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = ChatMessage.objects.filter(created_at__gte=start).aggregate(s=Sum("cost_usd"))["s"]
    return Decimal(total or 0)


_month_spend = sync_to_async(_month_spend_sync, thread_sensitive=True)


# ---------------------------------------------------------------------------
# Сесія та історія
# ---------------------------------------------------------------------------


def _get_session_sync(token: str | None, lang: str, ip: str | None) -> ChatSession:
    """Знайти сесію за public_token або завести нову.

    Невідомий/битий токен — НЕ помилка: це або протухла вкладка, або хтось підставив
    чуже значення. І там, і там правильна поведінка одна — тиха нова сесія. Кидати 404
    в чат-віджет означало б зламати чат людині, яка ні в чому не винна.
    """
    if token:
        try:
            existing = ChatSession.objects.filter(public_token=UUID(str(token))).first()
        except (ValueError, AttributeError, TypeError):
            existing = None
        if existing is not None:
            return existing
    return ChatSession.objects.create(locale=lang, ip_hash=hash_ip(ip))


_get_session = sync_to_async(_get_session_sync, thread_sensitive=True)


def _load_history_sync(session: ChatSession) -> list[dict[str, Any]]:
    """Історія розмови для messages.

    ⚠️ Віддаємо ТІЛЬКИ текст — без tool_use/tool_result/thinking-блоків попередніх ходів.
       Це свідомо: (1) відтворити валідні пари tool_use↔tool_result з БД — це третє джерело
       правди і вічне джерело 400-х; (2) результати інструментів протухають (ціни міняються
       кілька разів на добу), і згодовувати моделі вчорашню наявність як факт — гірше, ніж
       не згодовувати нічого. Треба свіжі дані — модель викличе інструмент ще раз.

    Перше повідомлення мусить бути user (вимога API) — тому провідні assistant-и зрізаємо.
    """
    rows = list(
        session.messages.filter(content__gt="")
        .order_by("-created_at", "-id")
        .values("role", "content")[:MAX_HISTORY_MESSAGES]
    )
    rows.reverse()

    while rows and rows[0]["role"] != ChatRole.USER:
        rows.pop(0)

    return [
        {"role": row["role"], "content": [{"type": "text", "text": row["content"]}]} for row in rows
    ]


_load_history = sync_to_async(_load_history_sync, thread_sensitive=True)


def _persist_user_sync(session: ChatSession, text: str) -> None:
    """Питання зберігаємо ДО виклику моделі.

    Якщо людина закриє вкладку посеред відповіді, генератор обірветься — і запис, зроблений
    «наприкінці», не відбувся б. А лічильник повідомлень (антизловживання) мусить рости від
    самого факту питання, а не від успішної відповіді.
    """
    with transaction.atomic():
        ChatMessage.objects.create(session=session, role=ChatRole.USER, content=text)
        ChatSession.objects.filter(pk=session.pk).update(
            message_count=F("message_count") + 1,
            updated_at=timezone.now(),
        )


_persist_user = sync_to_async(_persist_user_sync, thread_sensitive=True)


def _persist_assistant_sync(
    session: ChatSession,
    text: str,
    tool_calls: list[dict[str, Any]],
    spend: _Spend,
) -> None:
    """Відповідь + usage + вартість. Один рядок на питання, скільки б ітерацій не було."""
    with transaction.atomic():
        ChatMessage.objects.create(
            session=session,
            role=ChatRole.ASSISTANT,
            content=text,
            tool_calls=tool_calls,
            input_tokens=spend.input_tokens,
            output_tokens=spend.output_tokens,
            cache_read_tokens=spend.cache_read_tokens,
            cache_write_tokens=spend.cache_write_tokens,
            cost_usd=spend.cost_usd,
        )
        ChatSession.objects.filter(pk=session.pk).update(
            cost_usd=F("cost_usd") + spend.cost_usd,
            updated_at=timezone.now(),
        )


_persist_assistant = sync_to_async(_persist_assistant_sync, thread_sensitive=True)


# ---------------------------------------------------------------------------
# System-блоки та гейт кешу
# ---------------------------------------------------------------------------

# Рішення «чи перекриває префікс min_cache_prefix» міряється РАЗ на процес, а не на запит:
# count_tokens — це мережевий виклик, і платити ним за кожне питання, щоб дізнатись те саме
# число, безглуздо. Ключ — (модель, мова): у них різні дерева категорій, отже різні префікси.
_CACHE_GATE: dict[tuple[str, str], bool] = {}

_build_system = sync_to_async(prompts.build_system, thread_sensitive=True)


async def _system_blocks(client: BaseChatClient, lang: str, model: str) -> list[dict[str, Any]]:
    """System для запиту: правила + карта каталогу, з cache_control лише якщо він спрацює.

    ⚠️ prompts.should_cache() тут НЕ використовуємо: він синхронний і чекає СИРИЙ SDK-клієнт
       (звертається до `client.messages.count_tokens`), а в нас — асинхронна обгортка
       BaseChatClient. Викликати його з нашим клієнтом означало б тихо отримати False
       (він ловить будь-який виняток) і НАЗАВЖДИ лишитись без кешу, не помітивши цього.
       Тому гейт складено тут з двох публічних частин: client.count_tokens() і
       prompts.min_cache_prefix(). Логіка та сама, джерело правди — те саме.
    """
    key = (model, lang)
    cacheable = _CACHE_GATE.get(key)

    if cacheable is None:
        probe = await _build_system(lang, cacheable=False)
        tokens = await client.count_tokens(
            system=probe,
            messages=[{"role": "user", "content": "."}],
            tools=tools.TOOLS,
            model=model,
        )
        threshold = prompts.min_cache_prefix(model)
        cacheable = tokens >= threshold
        _CACHE_GATE[key] = cacheable
        log.info(
            "assistant: префікс %s/%s — %d ток. (мінімум %d) → кеш %s",
            model,
            lang,
            tokens,
            threshold,
            "УВІМКНЕНО" if cacheable else "вимкнено",
        )

    return await _build_system(lang, cacheable=cacheable)


def _move_cache_breakpoint(messages: list[dict[str, Any]]) -> None:
    """Перенести 5-хвилинний breakpoint на останній блок останнього повідомлення.

    🔴 Спершу ЗНІМАЄМО з усіх, потім ставимо на одне. Накопичення breakpoint'ів = 400 на
       4-й ітерації (ліміт API — 4 на запит, і один уже витрачено на system).

    Останнє повідомлення в кожній ітерації — завжди наше (user: питання або пачка
    tool_result), тобто список dict-ів, які ми самі й побудували. Блоки assistant'а —
    це об'єкти SDK, і ми їх не чіпаємо взагалі.
    """
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block.pop("cache_control", None)

    last = messages[-1].get("content")
    if isinstance(last, list) and last and isinstance(last[-1], dict):
        # ttl не вказуємо: дефолт — 5 хвилин, рівно стільки живе одна розмова в чаті.
        # Година тут була б удвічі дорожчою на запис і нікому не потрібною.
        last[-1]["cache_control"] = {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Інструменти
# ---------------------------------------------------------------------------

_dispatch = sync_to_async(tools.dispatch, thread_sensitive=True)


async def _run_tool(name: str, tool_input: Any, lang: str) -> tools.ToolOutcome:
    """Один інструмент. Ніколи не кидає: dispatch() сам перетворює будь-яку помилку в is_error."""
    payload = tool_input if isinstance(tool_input, dict) else {}
    return await _dispatch(name, payload, lang)


# ---------------------------------------------------------------------------
# Головний цикл
# ---------------------------------------------------------------------------


async def run_chat(
    *,
    message: str,
    session_token: str | None = None,
    locale: str = "uk",
    ip: str | None = None,
    client: BaseChatClient | None = None,
) -> AsyncIterator[bytes]:
    """Питання → потік SSE-подій. Справжній async generator (див. шапку модуля).

    Назовні НЕ кидає нічого: будь-яка помилка стає SSE-подією `error`. В'юха вже віддала
    заголовки 200 і text/event-stream — виняток звідси не перетворився б на 500, він просто
    обірвав би стрім на півслові, і фронт побачив би мовчання.
    """
    lang = normalize_lang(locale)
    text = (message or "").strip()
    chat_client = client or get_client()
    model = conf.model()

    # -- сесія ---------------------------------------------------------------
    try:
        session = await _get_session(session_token, lang, ip)
    except Exception:
        log.exception("assistant: не вдалося відкрити сесію")
        yield sse("error", code="internal", message=_BROKEN)
        yield sse("done")
        return

    # Токен — перша ж подія: фронт мусить знати його ще до відповіді, інакше при обриві
    # наступне питання поїде вже в НОВУ сесію, і людина втратить контекст розмови.
    yield sse("session", token=str(session.public_token))

    # -- гарди ---------------------------------------------------------------
    if not text:
        yield sse("error", code="empty", message="Напишіть, будь ласка, питання.")
        yield sse("done")
        return

    limit = conf.max_input_chars()
    if len(text) > limit:
        yield sse("error", code="too_long", message=_TOO_LONG.format(limit=limit))
        yield sse("done")
        return

    if session.message_count >= conf.max_messages():
        contact = await _manager_contact()
        yield sse("error", code="too_many", message=f"{_TOO_MANY} {contact}")
        yield sse("done")
        return

    # Місячний hard-cap. dry-run нічого не витрачає — його не блокуємо (інакше тести
    # почали б залежати від залишку бюджету).
    if not chat_client.is_dry_run:
        spent = await _month_spend()
        budget = conf.monthly_budget_usd()
        if spent >= budget:
            # М'яка відмова БЕЗ ретраю: це не збій, а стеля. Повторна спроба лише
            # спалила б ще один запит.
            log.error(
                "assistant: місячний бюджет вичерпано — $%s при ліміті $%s. Чат відповідає "
                "заглушкою. Підніми ASSISTANT_MONTHLY_BUDGET_USD або розберись із витратами.",
                spent,
                budget,
            )
            contact = await _manager_contact()
            yield sse("error", code="budget", message=f"{_BUSY}{contact}")
            yield sse("done")
            return

    # -- підготовка ----------------------------------------------------------
    try:
        system = await _system_blocks(chat_client, lang, model)
        history = await _load_history(session)
        await _persist_user(session, text)
    except Exception:
        log.exception("assistant: підготовка запиту впала")
        yield sse("error", code="internal", message=_BROKEN)
        yield sse("done")
        return

    messages: list[dict[str, Any]] = [
        *history,
        {"role": "user", "content": [{"type": "text", "text": text}]},
    ]

    spend = _Spend()
    reply_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    failure: str | None = None

    # -- tool-loop -----------------------------------------------------------
    for iteration in range(conf.MAX_ITERATIONS):
        _move_cache_breakpoint(messages)

        completed: Completed | None = None
        try:
            async for event in chat_client.stream(
                system=system,
                messages=messages,
                tools=tools.TOOLS,
                model=model,
            ):
                if isinstance(event, ThinkingDelta):
                    # Порожній thinking-текст усе одно подія: фронту важливий сам факт
                    # «модель думає», щоб увімкнути анімацію, а не текст роздумів.
                    yield sse("thinking", text=event.text)
                elif isinstance(event, TextDelta):
                    reply_parts.append(event.text)
                    yield sse("token", text=event.text)
                elif isinstance(event, Completed):
                    completed = event
        except AssistantError as exc:
            log.warning("assistant: ітерація %d не вдалася: %s", iteration, exc)
            failure = _BROKEN
            break
        except Exception:
            log.exception("assistant: несподівана помилка в ітерації %d", iteration)
            failure = _BROKEN
            break

        if completed is None:
            log.error("assistant: стрім завершився без Completed (ітерація %d)", iteration)
            failure = _BROKEN
            break

        spend.add(completed.usage)

        # ── refusal ────────────────────────────────────────────────────────
        # stop_details заповнене ТІЛЬКИ тут. У решті гілок воно None — звідси гард.
        if completed.stop_reason == "refusal":
            details = completed.stop_details
            log.warning(
                "assistant: модель відмовилась (category=%s)",
                getattr(details, "category", None) if details is not None else None,
            )
            contact = await _manager_contact()
            failure = f"{_REFUSAL} {contact}"
            break

        # ── max_tokens ─────────────────────────────────────────────────────
        # Без цієї гілки обрізана на півслові відповідь виглядала б як повна.
        if completed.stop_reason == "max_tokens":
            log.warning("assistant: відповідь уперлась у max_tokens=%d", conf.MAX_TOKENS)
            failure = _TRUNCATED
            break

        if completed.stop_reason != "tool_use":
            break

        # ── tool_use ───────────────────────────────────────────────────────
        uses = completed.tool_uses
        if not uses:
            log.error("assistant: stop_reason=tool_use, але блоків tool_use немає")
            break

        # Сирі блоки моделі (включно з tool_use) — назад у messages як є.
        messages.append({"role": "assistant", "content": completed.content})

        # 🔴 Подія «tool» — ДО dispatch, а не після. Інакше лисичка почне «думати» рівно тоді,
        #    коли думати вже закінчила: запити в БД — це і є та пауза, яку треба пояснити.
        yield sse("tool", names=[use.name for use in uses])

        outcomes = await asyncio.gather(*(_run_tool(u.name, u.input, lang) for u in uses))

        result_blocks: list[dict[str, Any]] = []
        products: list[dict[str, Any]] = []
        links: list[str] = []

        for use, outcome in zip(uses, outcomes, strict=True):
            result_blocks.append(tools.tool_result_block(use.id, outcome))
            products.extend(outcome["products"])
            if outcome["link"]:
                links.append(outcome["link"])
            tool_calls.append(
                {
                    "name": use.name,
                    "input": use.input if isinstance(use.input, dict) else {},
                    "is_error": outcome["is_error"],
                }
            )

        # Картки й посилання — окремими подіями: фронт малює їх компонентами, а не парсить
        # markdown з тексту відповіді.
        if products:
            yield sse("products", products=products[:MAX_PRODUCTS_PER_EVENT])
        for link in links:
            yield sse("link", url=link)

        # 🔴 ВСІ tool_result — в ОДНОМУ user-повідомленні. Розбивка по кількох мовчки відучує
        #    модель від паралельних викликів: вона «бачить», що ходи розкладаються по одному.
        messages.append({"role": "user", "content": result_blocks})
    else:
        # Цикл вичерпано, а модель усе ще просить інструменти. Далі не пускаємо: 6 ітерацій —
        # це вже дорого, а 7-ма зазвичай означає, що модель ходить по колу.
        log.warning("assistant: вичерпано %d ітерацій tool-loop", conf.MAX_ITERATIONS)
        if not reply_parts:
            failure = _ITERATIONS

    # -- фінал ---------------------------------------------------------------
    reply = "".join(reply_parts).strip()

    # Пишемо навіть на refusal/max_tokens/помилці: токени вже витрачені, і бюджет мусить це
    # побачити. Мовчазна втрата $0.05 на кожній невдалій відповіді — це і є те, як місячний
    # ліміт перестає працювати.
    if reply or tool_calls or spend.cost_usd:
        try:
            await _persist_assistant(session, reply, tool_calls, spend)
        except Exception:
            log.exception("assistant: не вдалося зберегти відповідь (витрати $%s)", spend.cost_usd)

    if failure:
        yield sse("error", code="failed", message=failure)

    yield sse("done")
