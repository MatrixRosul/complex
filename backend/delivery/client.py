"""
Клієнт API Нової Пошти + circuit breaker.

Джерела: INTEGRATIONS.md §1.1–1.3, §1.7.

──────────────────────────────────────────────────────────────────────────────
ЧОМУ ЦЕЙ ФАЙЛ ВИГЛЯДАЄ ПАРАНОЇДАЛЬНО (ADR-020)
──────────────────────────────────────────────────────────────────────────────
`/delivery/quote` — ЄДИНЕ місце, де НП стоїть на критичному шляху покупця. Якщо НП
відповідає 30 с або висить, кожен такий запит ТРИМАЄ web-воркер. Кілька десятків
користувачів на калькуляторі — і зайняті ВСІ воркери: лягає весь сайт, а не калькулятор.

Тому:
  • жорсткі таймаути (веб: connect 2 с / read 3 с, максимум 1 ретрай → найгірший
    бюджет воркера 10 с, а не «скільки схоче НП»);
  • circuit breaker у Redis: 5 помилок за 60 с → 60 с не ходимо в НП взагалі;
  • при відкритому breaker `call()` кидає NovaPoshtaUnavailable ОДРАЗУ, без сокета.
    Сервісний шар перетворює це на HTTP 200 `{"ok": false, "reason": "np_unavailable"}` —
    фронт НІКОЛИ не блокує кнопку «Оформити» через недоступність калькулятора.

Довідники (Celery, `sync_np_refs`) на критичному шляху НЕ стоять → там таймаути м'які
(5/60 с), 5 ретраїв з backoff і breaker вимкнений: нічний синк має право чекати.

──────────────────────────────────────────────────────────────────────────────
ПАСТКИ API НП, ЗАКРИТІ ТУТ (INTEGRATIONS §1.1)
──────────────────────────────────────────────────────────────────────────────
  🔴 HTTP-статус ЗАВЖДИ 200 — навіть на помилках. `raise_for_status()` НЕ рятує.
     Єдина правда — поле `success`.
  🔴 `errors` буває ПОРОЖНІМ масивом навіть при `success: false` → на `errors[0]`
     покладатись не можна. Логуємо `errorCodes` — вони стабільні, тексти ні.
  🔴 Rate-limit заголовків немає (`X-RateLimit-*`, `Retry-After` відсутні). Про ліміт
     дізнаєшся через раптовий `success: false` → ретрай з backoff обов'язковий.
  •  `Page` — 1-based, не 0. `Limit=5000` реально працює (міф про 150 у getSettlements —
     застарілий, не копіювати з PHP-SDK).

──────────────────────────────────────────────────────────────────────────────
ФІКСТУРНИЙ РЕЖИМ
──────────────────────────────────────────────────────────────────────────────
Немає ключа (`NP_API_KEY` / `NOVAPOSHTA_API_KEY` порожній) → клієнт працює на JSON-фікстурах
з `delivery/fixtures/np/` і НЕ відкриває жодного сокета. Це дає:
  • тести, які проходять без мережі й без ключа (CI);
  • робочий локальний dev без реєстрації в НП.

Фіктивний `getDocumentPrice` — детермінований тариф, ВІДКАЛІБРОВАНИЙ на чотирьох реальних
замірах з INTEGRATIONS §1.7 (Ужгород→Київ, Cost=1500):
    WarehouseWarehouse  2 кг без габаритів   →  97.5
    WarehousePostomat   2 кг                 → 107.5
    WarehouseDoors      2 кг                 → 157.5
    WarehouseWarehouse  50×50×60 см (2 кг)   → 539.5   ← об'ємна вага 0.15 м³ × 250 = 37.5 кг
Тобто фікстура відтворює головну пастку проєкту (заниження в 5.5× без габаритів) і тест на
неї — справжній, а не тавтологічний. Це ТАРИФ-ЗАГЛУШКА, а не реальна тарифна сітка НП:
на проді ціну рахує НП, тут — лише те, що потрібно для перевірки нашої логіки.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import httpx
from django.conf import settings
from django.core.cache import cache

# Коефіцієнт об'ємної ваги (250 кг/м³) живе В ОДНОМУ місці — delivery.models.
# Другий літерал «250» у кодовій базі = гарантований дрейф і мовчазне заниження ціни.
from delivery.models import NP_VOLUMETRIC_FACTOR

log = logging.getLogger(__name__)

__all__ = [
    "KYIV_CITY_REF",
    "NP_PAGE_LIMIT",
    "NP_VOLUMETRIC_FACTOR",
    "UZHHOROD_CITY_REF",
    "CircuitBreaker",
    "NovaPoshtaClient",
    "NovaPoshtaError",
    "NovaPoshtaUnavailable",
    "Timeouts",
    "get_api_key",
    "get_sync_client",
    "get_web_client",
    "reset_clients",
]

# ---------------------------------------------------------------------------
# Константи
# ---------------------------------------------------------------------------

#: Ужгород. CityRef (== DeliveryCity ref), а НЕ SettlementRef. INTEGRATIONS §1.2.
UZHHOROD_CITY_REF = "e221d627-391c-11dd-90d9-001a92567626"
#: Київ — потрібен для smoke-тесту синку довідників (§1.5).
KYIV_CITY_REF = "8d5a980d-391c-11dd-90d9-001a92567626"

#: Limit=5000 реально працює (INTEGRATIONS §1.4). ~21 запит на весь довідник.
NP_PAGE_LIMIT = 5000
#: Стеля пагінації — щоб бита відповідь НП не крутила нас вічно.
MAX_PAGES = 100

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "np"


# ---------------------------------------------------------------------------
# Помилки
# ---------------------------------------------------------------------------


class NovaPoshtaError(RuntimeError):
    """НП ВІДПОВІЛА, але `success: false` — логічна помилка (битий ref, невалідний ключ…).

    Це НЕ збій сервісу → breaker НЕ чіпаємо (інакше один покупець з битим ref-ом
    відкриває breaker для всіх). Ретраїти теж немає сенсу: відповідь детермінована.
    """

    def __init__(self, errors: list[str], codes: list[str]) -> None:
        self.errors = errors
        self.codes = codes
        super().__init__("; ".join(errors) or f"NP call failed (errorCodes={codes})")


class NovaPoshtaUnavailable(RuntimeError):
    """НП НЕ ВІДПОВІЛА: таймаут, обрив, 5xx — або breaker уже відкритий.

    Саме це сервісний шар перетворює на `{"ok": false, "reason": "np_unavailable"}`.
    """


# ---------------------------------------------------------------------------
# Circuit breaker (Redis через django-кеш, db1)
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """5 помилок за 60 с → 60 с не ходимо в сервіс взагалі (INTEGRATIONS §1.3).

    Стан — у Redis, а не в пам'яті процесу: воркерів gunicorn кілька, і breaker, який
    «відкрився» лише в одному з них, не рятує від залягання решти.

    Два ключі:
      `<name>:fails` — лічильник з TTL=WINDOW. TTL ставиться при СТВОРЕННІ ключа і
                       `incr` його НЕ подовжує → це саме ковзне вікно 60 с, а не
                       «5 помилок колись за всю історію».
      `<name>:open`  — прапорець з TTL=OPEN_FOR. Є ключ → breaker відкритий.
    """

    FAIL_THRESHOLD = 5
    WINDOW = 60
    OPEN_FOR = 60

    def __init__(self, name: str = "np", *, cache_backend: Any = None) -> None:
        self.name = name
        self._cache = cache_backend or cache
        self._fail_key = f"cb:{name}:fails"
        self._open_key = f"cb:{name}:open"

    def is_open(self) -> bool:
        return self._cache.get(self._open_key) is not None

    def record_failure(self) -> int:
        """Повертає поточне число помилок у вікні. На порозі — відкриває breaker."""
        # `add` = SET NX з TTL: атомарно і НЕ перетирає лічильник, який уже росте.
        self._cache.add(self._fail_key, 0, self.WINDOW)
        try:
            fails = self._cache.incr(self._fail_key)
        except ValueError:
            # Ключ протух рівно між add і incr — вікно почалось наново.
            self._cache.set(self._fail_key, 1, self.WINDOW)
            fails = 1

        if fails >= self.FAIL_THRESHOLD:
            self._cache.set(self._open_key, "1", self.OPEN_FOR)
            log.error(
                "circuit breaker '%s' ВІДКРИТО: %s помилок за %s с → %s с не ходимо в сервіс",
                self.name,
                fails,
                self.WINDOW,
                self.OPEN_FOR,
            )
        return int(fails)

    def record_success(self) -> None:
        """Успіх скидає лічильник. Прапорець `open` НЕ чіпаємо — він дотікає по TTL."""
        self._cache.delete(self._fail_key)

    def reset(self) -> None:
        self._cache.delete(self._fail_key)
        self._cache.delete(self._open_key)

    def fails(self) -> int:
        return int(self._cache.get(self._fail_key) or 0)


# ---------------------------------------------------------------------------
# Профілі таймаутів
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Timeouts:
    connect: float
    read: float
    retries: int
    backoff: float = 0.0

    @property
    def budget(self) -> float:
        """Найгірший час, який запит може з'їсти у воркера."""
        attempts = self.retries + 1
        return attempts * (self.connect + self.read) + self.retries * self.backoff


#: Веб-запит (`/delivery/quote`). Бюджет ≈ 10 с у найгіршому разі — і жодного ретраю більше.
WEB_TIMEOUTS = Timeouts(connect=2.0, read=3.0, retries=1, backoff=0.0)
#: Celery (`sync_np_refs`). Нічний синк має право чекати; 22 МБ warehouses читаються довго.
TASK_TIMEOUTS = Timeouts(connect=5.0, read=60.0, retries=5, backoff=2.0)


def get_api_key() -> str:
    """Ключ НП.

    ⚠️ У `config/settings/base.py` змінна називається `NP_API_KEY` (і саме вона в
    `.env.example`). Псевдонім `NOVAPOSHTA_API_KEY` підтримуємо як env-фолбек, щоб не
    правити settings (їх пише інший агент).
    """
    return (
        getattr(settings, "NP_API_KEY", "")
        or os.environ.get("NOVAPOSHTA_API_KEY", "")
        or os.environ.get("NP_API_KEY", "")
    ).strip()


# ---------------------------------------------------------------------------
# Клієнт
# ---------------------------------------------------------------------------


class NovaPoshtaClient:
    """Тонка обгортка над єдиним endpoint-ом НП.

    Усе API НП — це один POST на один URL; різниця лише в `modelName`/`calledMethod`.
    Тому власний клієнт на ~150 рядків, а не бібліотека (INTEGRATIONS §1.2).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeouts: Timeouts = WEB_TIMEOUTS,
        breaker: CircuitBreaker | None = None,
        url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        force_fixtures: bool = False,
    ) -> None:
        self.api_key = get_api_key() if api_key is None else api_key
        self.timeouts = timeouts
        self.breaker = breaker
        self.url = url or getattr(settings, "NP_API_URL", "https://api.novaposhta.ua/v2.0/json/")
        self.use_fixtures = force_fixtures or not self.api_key
        self._transport = transport
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    # --- транспорт ---------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = httpx.Client(
                        timeout=httpx.Timeout(
                            connect=self.timeouts.connect,
                            read=self.timeouts.read,
                            write=2.0,
                            pool=2.0,
                        ),
                        # gzip → 8× економії на синку довідників (INTEGRATIONS §1.1)
                        headers={"Accept-Encoding": "gzip", "Content-Type": "application/json"},
                        transport=self._transport,
                        follow_redirects=False,
                    )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # --- головний виклик ---------------------------------------------------

    def call(self, model: str, method: str, **props: Any) -> list[dict[str, Any]]:
        """Один виклик НП. Повертає `data`.

        Кидає:
          NovaPoshtaUnavailable — breaker відкритий / таймаут / обрив / 5xx;
          NovaPoshtaError       — НП відповіла `success: false`.
        """
        if self.use_fixtures:
            return fixture_call(model, method, props)

        if self.breaker is not None and self.breaker.is_open():
            # 🔴 Головна цінність файлу: жодного сокета, миттєва відмова.
            raise NovaPoshtaUnavailable(f"circuit breaker '{self.breaker.name}' is open")

        payload = {
            "apiKey": self.api_key,
            "modelName": model,
            "calledMethod": method,
            "methodProperties": props,
        }

        last: Exception | None = None
        for attempt in range(self.timeouts.retries + 1):
            try:
                resp = self._http().post(self.url, json=payload)
                resp.raise_for_status()  # ловить лише 5xx/4xx-транспорт, НЕ логіку НП
                data = resp.json()
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                last = exc
                log.warning(
                    "НП %s/%s — спроба %s/%s не вдалась: %s",
                    model,
                    method,
                    attempt + 1,
                    self.timeouts.retries + 1,
                    exc,
                )
                if attempt < self.timeouts.retries:
                    if self.timeouts.backoff:
                        time.sleep(self.timeouts.backoff * (2**attempt))
                    continue
                break

            # 🔴 HTTP 200 ≠ успіх. Єдина правда — поле `success`.
            if not data.get("success"):
                errors = [str(e) for e in (data.get("errors") or [])]
                codes = [str(c) for c in (data.get("errorCodes") or [])]
                # errors може бути ПОРОЖНІМ навіть при success:false → логуємо коди.
                log.warning("НП %s/%s → success=false, errorCodes=%s", model, method, codes)
                if self.breaker is not None:
                    self.breaker.record_success()  # сервіс живий, просто відмовив логічно
                raise NovaPoshtaError(errors, codes)

            if self.breaker is not None:
                self.breaker.record_success()
            return list(data.get("data") or [])

        # Усі спроби вичерпані — це збій СЕРВІСУ, а не логіки.
        if self.breaker is not None:
            self.breaker.record_failure()
        raise NovaPoshtaUnavailable(f"НП недоступна: {last}") from last

    # --- пагінація ---------------------------------------------------------

    def fetch_all(self, model: str, method: str, **props: Any) -> list[dict[str, Any]]:
        """Тягне ВСІ сторінки. `Page` — 1-based (не 0!), `Limit=5000`."""
        out: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            chunk = self.call(model, method, Limit=str(NP_PAGE_LIMIT), Page=str(page), **props)
            out.extend(chunk)
            if len(chunk) < NP_PAGE_LIMIT:
                return out
        log.error("НП %s/%s: досягнуто стелі в %s сторінок — обриваємо", model, method, MAX_PAGES)
        return out

    # --- калькулятор -------------------------------------------------------

    def get_document_price(
        self,
        *,
        city_recipient: str,
        weight_kg: Decimal,
        cost_declared: Decimal,
        service_type: str,
        seats: list[dict[str, str]],
        city_sender: str = UZHHOROD_CITY_REF,
        redelivery: Decimal | None = None,
    ) -> dict[str, Any]:
        """`InternetDocument/getDocumentPrice`.

        🔴 `OptionsSeat` шлеться ЗАВЖДИ, навіть коли габарити дефолтні: без них НП рахує
        тільки за фактичною вагою і магазин недоплачує в 5.5× (INTEGRATIONS §1.7).
        🔴 `CargoType` — тільки `Parcel`: на `Cargo` НП МОВЧКИ міняє тип і додає warning.
        """
        props: dict[str, Any] = {
            "CitySender": city_sender,
            "CityRecipient": city_recipient,
            "Weight": _num(weight_kg),
            "ServiceType": service_type,
            "Cost": _num(cost_declared),
            "CargoType": "Parcel",
            "SeatsAmount": str(len(seats)),
            "OptionsSeat": seats,
        }
        if redelivery is not None:
            props["RedeliveryCalculate"] = {"CargoType": "Money", "Amount": _num(redelivery)}

        rows = self.call("InternetDocument", "getDocumentPrice", **props)
        if not rows:
            raise NovaPoshtaError(["НП повернула порожній data на getDocumentPrice"], [])
        return rows[0]


def _num(value: Decimal | float | int) -> str:
    """НП приймає числа рядками. Нормалізуємо без експоненти й без хвостових нулів."""
    d = Decimal(str(value)).normalize()
    if d == d.to_integral_value():
        d = d.quantize(Decimal("1"))
    return format(d, "f")


# ---------------------------------------------------------------------------
# Синглтони клієнтів
# ---------------------------------------------------------------------------

_web_client: NovaPoshtaClient | None = None
_sync_client: NovaPoshtaClient | None = None
_clients_lock = threading.Lock()


def get_web_client() -> NovaPoshtaClient:
    """Клієнт для КРИТИЧНОГО ШЛЯХУ: жорсткі таймаути + breaker."""
    global _web_client
    if _web_client is None:
        with _clients_lock:
            if _web_client is None:
                _web_client = NovaPoshtaClient(timeouts=WEB_TIMEOUTS, breaker=CircuitBreaker("np"))
    return _web_client


def get_sync_client() -> NovaPoshtaClient:
    """Клієнт для Celery: м'які таймаути, 5 ретраїв, БЕЗ breaker.

    Breaker тут шкідливий: нічний синк — єдиний споживач, і відкритий ним breaker
    поклав би калькулятор покупцям.
    """
    global _sync_client
    if _sync_client is None:
        with _clients_lock:
            if _sync_client is None:
                _sync_client = NovaPoshtaClient(timeouts=TASK_TIMEOUTS, breaker=None)
    return _sync_client


def reset_clients() -> None:
    """Для тестів: скинути синглтони (щоб підмінити ключ/транспорт)."""
    global _web_client, _sync_client
    with _clients_lock:
        for c in (_web_client, _sync_client):
            if c is not None:
                c.close()
        _web_client = None
        _sync_client = None


# ---------------------------------------------------------------------------
# Фікстурний бекенд
# ---------------------------------------------------------------------------

_FIXTURE_FILES = {
    ("Address", "getAreas"): "areas.json",
    ("Address", "getCities"): "cities.json",
    ("Address", "getSettlements"): "settlements.json",
    ("Address", "getWarehouses"): "warehouses.json",
}

#: Тариф-заглушка, відкалібрований на 4 реальних замірах INTEGRATIONS §1.7 (див. шапку).
_FIXTURE_BASE = {
    "WarehouseWarehouse": Decimal("72.60"),
    "WarehousePostomat": Decimal("82.60"),
    "WarehouseDoors": Decimal("132.60"),
}
_FIXTURE_PER_KG = Decimal("12.45")
_FIXTURE_REDELIVERY = Decimal("50.00")

_fixture_cache: dict[str, list[dict[str, Any]]] = {}


def load_fixture(name: str) -> list[dict[str, Any]]:
    if name not in _fixture_cache:
        _fixture_cache[name] = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    # Копія: викликач не має права зіпсувати кеш.
    return [dict(row) for row in _fixture_cache[name]]


def fixture_call(model: str, method: str, props: dict[str, Any]) -> list[dict[str, Any]]:
    """Емуляція НП без мережі: довідники з JSON, ціна — детермінований тариф."""
    key = (model, method)

    if key in _FIXTURE_FILES:
        rows = load_fixture(_FIXTURE_FILES[key])
        if city_ref := props.get("CityRef"):
            rows = [r for r in rows if r.get("CityRef") == city_ref]
        # Пагінація — точно як у НП: Page 1-based, зріз по Limit.
        limit = int(props.get("Limit") or NP_PAGE_LIMIT)
        page = int(props.get("Page") or 1)
        return rows[(page - 1) * limit : page * limit]

    if key == ("InternetDocument", "getDocumentPrice"):
        return [_fixture_price(props)]

    raise NovaPoshtaError([f"Фікстури для {model}/{method} немає"], ["FIXTURE_MISS"])


def _fixture_price(props: dict[str, Any]) -> dict[str, Any]:
    service_type = str(props.get("ServiceType") or "WarehouseWarehouse")
    if service_type not in _FIXTURE_BASE:
        raise NovaPoshtaError([f"Невідомий ServiceType {service_type}"], ["FIXTURE_BAD_SERVICE"])

    actual = Decimal(str(props.get("Weight") or "0"))
    seats = props.get("OptionsSeat") or []

    # НП рахує ціну від max(фактична вага, об'ємна вага) — ПО КОЖНОМУ МІСЦЮ окремо.
    chargeable = Decimal("0")
    if seats:
        for seat in seats:
            w = Decimal(str(seat.get("weight") or "0"))
            vol_m3 = (
                Decimal(str(seat.get("volumetricWidth") or "0"))
                * Decimal(str(seat.get("volumetricHeight") or "0"))
                * Decimal(str(seat.get("volumetricLength") or "0"))
                / Decimal("1000000")  # см³ → м³
            )
            chargeable += max(w, vol_m3 * NP_VOLUMETRIC_FACTOR)
    else:
        # 🔴 Саме цей режим і занижує ціну в 5.5×. Фікстура його чесно відтворює.
        chargeable = actual
    chargeable = max(chargeable, actual)

    cost = _FIXTURE_BASE[service_type] + _FIXTURE_PER_KG * chargeable
    # Округлення до 0.5 грн — так лягають усі 4 живі заміри (97.5 / 107.5 / 157.5 / 539.5).
    cost = (cost / Decimal("0.5")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal("0.5")

    out: dict[str, Any] = {
        "AssessedCost": float(Decimal(str(props.get("Cost") or "0"))),
        "Cost": float(cost),
        "CostPack": 0,
    }
    if props.get("RedeliveryCalculate"):
        out["CostRedelivery"] = float(_FIXTURE_REDELIVERY)
    return out
