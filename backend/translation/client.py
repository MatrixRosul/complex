"""
Тонкий клієнт Anthropic для перекладу (TRANSLATION.md §5.2, §7.3, §8.5).

ЩО ТУТ ВАЖЛИВО І ЛЕГКО ЗЛАМАТИ:

1. thinking={"type": "disabled"} — ОБОВ'ЯЗКОВО.
   На Sonnet 5 adaptive thinking увімкнений ЗА ЗАМОВЧУВАННЯМ, коли параметр `thinking` не
   переданий. Для перекладу це чистий спалений бюджет на output-токени (ми платимо за
   роздуми моделі про те, як перекласти «Чорний»). Пропустиш параметр — рахунок подвоїться,
   і жодної помилки при цьому не буде.
   (На Opus 4.8 навпаки: без параметра thinking не вмикається. Ставимо явно на обох.)

2. output_config.format (structured outputs) — ЄДИНИЙ спосіб отримати JSON.
   Prefill (останнє assistant-повідомлення) на всіх поточних моделях повертає 400.

3. Batch API — головна економія: −50% на input і output, знижка СТАКАЄТЬСЯ з кешуванням.
   Результати приходять У ДОВІЛЬНОМУ ПОРЯДКУ → ключуємо ТІЛЬКИ за custom_id, ніколи за
   індексом у списку.

4. DRY-RUN / фікстурний режим: якщо ANTHROPIC_API_KEY не заданий — НЕ падаємо. Повертаємо
   заглушку (ru = uk) і пишемо в лог. Тести проходять без ключа; продакшн без ключа не
   зіпсує каталог, бо заглушка ru=uk пройде валідатори, але піде в чергу як MACHINE і
   чекатиме людини.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from django.conf import settings

from translation import conf
from translation.prompts import (
    TRANSLATION_SCHEMA,
    build_system,
    render_user_message,
)

logger = logging.getLogger(__name__)


class TranslationError(RuntimeError):
    pass


class BudgetExceeded(TranslationError):
    pass


@dataclass
class Usage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    batch: bool = False
    cost_usd: Decimal = Decimal("0")

    @classmethod
    def from_api(cls, model: str, raw: Any, *, batch: bool = False) -> Usage:
        u = cls(
            model=model,
            input_tokens=getattr(raw, "input_tokens", 0) or 0,
            output_tokens=getattr(raw, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
            batch=batch,
        )
        u.cost_usd = conf.compute_cost(
            model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_tokens,
            cache_write_tokens=u.cache_write_tokens,
            batch=batch,
        )
        return u


@dataclass
class TranslationRequest:
    """Один запит = один виклик API = N сегментів."""

    custom_id: str
    kind: str
    segments: list[dict[str, str]]  # [{"id": "...", "uk": "..."}]
    model: str
    context: str | None = None
    previous_rejected: dict[str, str] | None = None


@dataclass
class TranslationResponse:
    custom_id: str
    translations: dict[str, str] = field(default_factory=dict)  # id → ru
    notes: dict[str, str] = field(default_factory=dict)  # id → note
    usage: Usage = field(default_factory=Usage)
    error: str = ""


class BaseClient(Protocol):
    def translate(
        self, request: TranslationRequest, glossary_block: str
    ) -> TranslationResponse: ...

    def translate_many(
        self, requests: list[TranslationRequest], glossary_block: str
    ) -> list[TranslationResponse]: ...


# ---------------------------------------------------------------------------
# DRY-RUN
# ---------------------------------------------------------------------------


class DryRunClient:
    """Заглушка: ru = uk. Жодного мережевого виклику, жодних витрат.

    Навмисно НЕ вигадує переклад: якби вона його вигадувала, тест «переклад коректний»
    перевіряв би заглушку, а не пайплайн.
    """

    is_dry_run = True

    def translate(self, request: TranslationRequest, glossary_block: str) -> TranslationResponse:
        logger.info(
            "TRANSLATION dry-run: %s (%s), %d сегментів — API не викликається",
            request.custom_id,
            request.kind,
            len(request.segments),
        )
        return TranslationResponse(
            custom_id=request.custom_id,
            translations={s["id"]: s["uk"] for s in request.segments},
            notes={},
            usage=Usage(model=request.model),
        )

    def translate_many(
        self, requests: list[TranslationRequest], glossary_block: str
    ) -> list[TranslationResponse]:
        return [self.translate(r, glossary_block) for r in requests]


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------


class AnthropicClient:
    """Реальний клієнт. Sync — для кнопки «Перекласти зараз» в адмінці.
    Batch — для масових прогонів (−50%)."""

    is_dry_run = False

    def __init__(self, api_key: str, *, max_retries: int = 5):
        import anthropic

        self._anthropic = anthropic
        # SDK сам ретраїть 429/408/409/5xx з експоненційним backoff.
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=max_retries)
        self._max_retries = max_retries

    # -- спільні параметри запиту -------------------------------------------

    def _params(self, request: TranslationRequest, glossary_block: str) -> dict[str, Any]:
        from translation.prompts import build_payload

        payload = build_payload(
            request.kind,
            request.segments,
            context=request.context,
            previous_rejected=request.previous_rejected,
        )
        return {
            "model": request.model,
            "max_tokens": conf.MAX_TOKENS,
            # ⚠️ без цього на Sonnet 5 вмикається adaptive thinking і ми платимо за роздуми
            "thinking": {"type": "disabled"},
            "output_config": {
                "effort": conf.EFFORT,
                "format": {"type": "json_schema", "schema": TRANSLATION_SCHEMA},
            },
            "system": build_system(glossary_block, request.model),
            "messages": [{"role": "user", "content": render_user_message(payload)}],
        }

    @staticmethod
    def _parse(custom_id: str, message: Any, model: str, *, batch: bool) -> TranslationResponse:
        import json

        text = next((b.text for b in message.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except (ValueError, TypeError) as exc:
            return TranslationResponse(
                custom_id=custom_id,
                usage=Usage.from_api(model, message.usage, batch=batch),
                error=f"bad_json: {exc}",
            )

        translations: dict[str, str] = {}
        notes: dict[str, str] = {}
        for item in data.get("translations", []):
            sid = item.get("id")
            if not sid:
                continue
            translations[sid] = item.get("ru", "")
            if item.get("note"):
                notes[sid] = item["note"]

        return TranslationResponse(
            custom_id=custom_id,
            translations=translations,
            notes=notes,
            usage=Usage.from_api(model, message.usage, batch=batch),
        )

    # -- sync ---------------------------------------------------------------

    def translate(self, request: TranslationRequest, glossary_block: str) -> TranslationResponse:
        params = self._params(request, glossary_block)
        last_exc: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                message = self._client.messages.create(**params)
            except self._anthropic.RateLimitError as exc:
                last_exc = exc
                delay = min(60.0, 2**attempt) + random.uniform(0, 1)  # noqa: S311
                logger.warning("TRANSLATION rate-limit, спроба %d, пауза %.1fs", attempt + 1, delay)
                time.sleep(delay)
                continue
            except self._anthropic.APIStatusError as exc:
                if exc.status_code >= 500:
                    last_exc = exc
                    delay = min(60.0, 2**attempt) + random.uniform(0, 1)  # noqa: S311
                    logger.warning("TRANSLATION %s, спроба %d", exc.status_code, attempt + 1)
                    time.sleep(delay)
                    continue
                # 4xx (крім 429) ретраїти безглуздо — це наш баг у запиті
                return TranslationResponse(
                    custom_id=request.custom_id, error=f"api_{exc.status_code}: {exc}"
                )

            resp = self._parse(request.custom_id, message, request.model, batch=False)
            # Контроль кешу: на повторних запитах має бути > 0. Нуль — або «тихий
            # інвалідатор» у system, або префікс коротший за мінімум (4096 tok на Opus 4.8).
            if resp.usage.cache_read_tokens == 0 and resp.usage.cache_write_tokens == 0:
                logger.debug(
                    "TRANSLATION: кеш не спрацював (%s). Перевір довжину system-блоку.",
                    request.model,
                )
            return resp

        return TranslationResponse(
            custom_id=request.custom_id, error=f"retries_exhausted: {last_exc}"
        )

    # -- batch --------------------------------------------------------------

    def submit_batch(self, requests: list[TranslationRequest], glossary_block: str) -> str:
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        batch = self._client.messages.batches.create(
            requests=[
                Request(
                    custom_id=r.custom_id,
                    params=MessageCreateParamsNonStreaming(**self._params(r, glossary_block)),
                )
                for r in requests
            ]
        )
        logger.info("TRANSLATION: батч %s надіслано, %d запитів", batch.id, len(requests))
        return batch.id

    def poll_batch(self, batch_id: str) -> str:
        return self._client.messages.batches.retrieve(batch_id).processing_status

    def fetch_batch(self, batch_id: str, models: dict[str, str]) -> list[TranslationResponse]:
        """⚠️ Результати приходять У ДОВІЛЬНОМУ ПОРЯДКУ — ключуємо тільки за custom_id."""
        out: list[TranslationResponse] = []
        for result in self._client.messages.batches.results(batch_id):
            cid = result.custom_id
            model = models.get(cid, conf.DEFAULT_BULK_MODEL)
            if result.result.type == "succeeded":
                out.append(self._parse(cid, result.result.message, model, batch=True))
            else:
                out.append(TranslationResponse(custom_id=cid, error=f"batch_{result.result.type}"))
        return out

    def translate_many(
        self,
        requests: list[TranslationRequest],
        glossary_block: str,
        *,
        poll_interval: float = 30.0,
        timeout: float = 24 * 3600,
    ) -> list[TranslationResponse]:
        if not requests:
            return []
        batch_id = self.submit_batch(requests, glossary_block)
        models = {r.custom_id: r.model for r in requests}

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.poll_batch(batch_id)
            if status == "ended":
                return self.fetch_batch(batch_id, models)
            time.sleep(poll_interval)
        raise TranslationError(f"batch {batch_id} не завершився за {timeout}s")

    # -- утиліти ------------------------------------------------------------

    def count_tokens(self, request: TranslationRequest, glossary_block: str) -> int:
        """Точний підрахунок (не евристика 2,5 симв/токен).

        TRANSLATION.md §7.4: перед першим повним прогоном ОБОВ'ЯЗКОВО заміряти на 50
        реальних описах і перерахувати оцінку — описи це 83% рахунку.
        """
        p = self._params(request, glossary_block)
        return self._client.messages.count_tokens(
            model=p["model"], system=p["system"], messages=p["messages"]
        ).input_tokens


# ---------------------------------------------------------------------------
# Фабрика
# ---------------------------------------------------------------------------


def get_client(*, dry_run: bool = False) -> BaseClient:
    """Без ключа — не падаємо, а працюємо в dry-run. Тести МАЮТЬ проходити без ключа."""
    api_key = (getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip()
    if dry_run or not api_key:
        if not dry_run:
            logger.warning(
                "TRANSLATION: ANTHROPIC_API_KEY не заданий — працюю в DRY-RUN "
                "(переклад = оригінал, витрати $0). Це не помилка, але й не переклад."
            )
        return DryRunClient()
    return AnthropicClient(api_key)
