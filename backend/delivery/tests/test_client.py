"""
Клієнт НП: жорсткі таймаути, circuit breaker, пастки API (ADR-020, INTEGRATIONS §1.1–1.3).

Мережі немає: усе через httpx.MockTransport або фікстурний режим.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from delivery.client import (
    WEB_TIMEOUTS,
    NovaPoshtaClient,
    NovaPoshtaError,
    NovaPoshtaUnavailable,
    Timeouts,
)
from delivery.tests.conftest import make_failing_client, make_json_client

# ---------------------------------------------------------------------------
# Таймаути (ADR-020)
# ---------------------------------------------------------------------------


def test_web_timeouts_are_hard():
    """connect 2 с / read 3 с / максимум 1 ретрай.

    🔴 Не косметика: без цього кожен запит /delivery/quote тримає web-воркер стільки,
    скільки схоче НП. Кілька десятків користувачів на калькуляторі — і зайняті ВСІ
    воркери, лягає весь сайт.
    """
    assert WEB_TIMEOUTS.connect == 2.0
    assert WEB_TIMEOUTS.read == 3.0
    assert WEB_TIMEOUTS.retries == 1
    # Найгірший бюджет воркера — 10 с, не 30 і не «скільки завгодно».
    assert WEB_TIMEOUTS.budget == 10.0


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_breaker_opens_after_five_failures(breaker):
    """5 помилок за 60 с → breaker відкритий."""
    assert breaker.is_open() is False

    for i in range(1, 5):
        breaker.record_failure()
        assert breaker.is_open() is False, f"передчасно відкрився на {i}-й помилці"

    breaker.record_failure()  # п'ята
    assert breaker.is_open() is True


def test_success_resets_the_counter(breaker):
    """Чотири помилки + успіх + чотири помилки — це НЕ 8 у вікні, breaker закритий."""
    for _ in range(4):
        breaker.record_failure()
    breaker.record_success()
    assert breaker.fails() == 0

    for _ in range(4):
        breaker.record_failure()
    assert breaker.is_open() is False


def test_client_opens_breaker_on_repeated_np_timeouts(breaker):
    """Клієнт сам веде лічильник: 5 таймаутів поспіль → breaker відкрито."""
    client = make_failing_client(breaker)

    for _ in range(5):
        with pytest.raises(NovaPoshtaUnavailable):
            client.call("Address", "getAreas")

    assert breaker.is_open() is True


def test_open_breaker_short_circuits_without_a_socket(breaker):
    """🔴 ГОЛОВНЕ: при відкритому breaker у НП НЕ ЙДЕМО ВЗАГАЛІ.

    Транспорт кине AssertionError, якщо його все ж смикнуть, — так ми доводимо, що
    жодного сокета не відкрито, а не просто «швидко впало».
    """
    breaker._cache.set(breaker._open_key, "1", 60)

    def must_not_be_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError("breaker відкритий, а клієнт усе одно поліз у мережу")

    client = NovaPoshtaClient(
        api_key="test-key",
        timeouts=Timeouts(connect=1, read=1, retries=1),
        breaker=breaker,
        transport=httpx.MockTransport(must_not_be_called),
    )

    with pytest.raises(NovaPoshtaUnavailable, match="open"):
        client.call("Address", "getAreas")


def test_retries_exactly_once_on_web_profile(breaker):
    """Веб-профіль: 1 ретрай, тобто рівно 2 спроби. Ні більше, ні менше."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    client = NovaPoshtaClient(
        api_key="test-key",
        timeouts=Timeouts(connect=0.1, read=0.1, retries=1),
        breaker=breaker,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(NovaPoshtaUnavailable):
        client.call("Address", "getAreas")

    assert len(calls) == 2  # 1 спроба + 1 ретрай
    assert breaker.fails() == 1  # весь виклик — ОДНА помилка, а не дві


# ---------------------------------------------------------------------------
# Пастки API НП
# ---------------------------------------------------------------------------


def test_http_200_with_success_false_is_an_error(breaker):
    """🔴 HTTP-статус НП ЗАВЖДИ 200 — навіть на помилках. raise_for_status() не рятує."""
    client = make_json_client(
        {"success": False, "data": [], "errors": ["City not found"], "errorCodes": ["20000401550"]},
        breaker,
    )

    with pytest.raises(NovaPoshtaError) as exc:
        client.call("Address", "getWarehouses", CityRef="bad")

    assert exc.value.codes == ["20000401550"]


def test_empty_errors_array_still_raises(breaker):
    """`errors` буває ПОРОЖНІМ навіть при success:false → на errors[0] покладатись не можна."""
    client = make_json_client(
        {"success": False, "data": [], "errors": [], "errorCodes": ["20000000000"]}, breaker
    )

    with pytest.raises(NovaPoshtaError, match="errorCodes"):
        client.call("Address", "nonExistentMethod")


def test_logical_error_does_not_open_the_breaker(breaker):
    """success:false — це НЕ збій сервісу.

    Інакше один покупець з битим ref-ом відкрив би breaker усьому магазину.
    """
    client = make_json_client(
        {"success": False, "data": [], "errors": ["bad ref"], "errorCodes": ["X"]}, breaker
    )

    for _ in range(10):
        with pytest.raises(NovaPoshtaError):
            client.call("Address", "getWarehouses", CityRef="bad")

    assert breaker.is_open() is False
    assert breaker.fails() == 0


# ---------------------------------------------------------------------------
# Фікстурний режим
# ---------------------------------------------------------------------------


def test_fixture_mode_without_key(np_client):
    assert np_client.use_fixtures is True

    areas = np_client.fetch_all("Address", "getAreas")
    warehouses = np_client.fetch_all("Address", "getWarehouses")

    assert {a["Description"] for a in areas} >= {"Закарпатська", "Київська"}
    assert any(w["CategoryOfWarehouse"] == "Postomat" for w in warehouses)


@pytest.mark.parametrize(
    ("service_type", "expected"),
    [
        ("WarehouseWarehouse", 97.5),
        ("WarehousePostomat", 107.5),
        ("WarehouseDoors", 157.5),
    ],
)
def test_fixture_price_reproduces_live_measurements(np_client, service_type, expected):
    """Фікстурний тариф відтворює живі заміри НП (Ужгород→Київ, 2 кг, Cost=1500).

    Тобто тест на об'ємну вагу нижче — справжній, а не тавтологія на вигаданих числах.
    """
    row = np_client.get_document_price(
        city_recipient="8d5a980d-391c-11dd-90d9-001a92567626",
        weight_kg=Decimal("2"),
        cost_declared=Decimal("1500"),
        service_type=service_type,
        seats=[],  # БЕЗ габаритів — саме так і занижується ціна
    )

    assert row["Cost"] == expected


def test_fixture_price_with_options_seat_is_5x_higher(np_client):
    """🔴 Та сама посилка з габаритами 50×50×60 → 539.5 замість 97.5. Різниця 5.5×."""
    row = np_client.get_document_price(
        city_recipient="8d5a980d-391c-11dd-90d9-001a92567626",
        weight_kg=Decimal("2"),
        cost_declared=Decimal("1500"),
        service_type="WarehouseWarehouse",
        seats=[
            {
                "weight": "2",
                "volumetricWidth": "50",
                "volumetricHeight": "50",
                "volumetricLength": "60",
            }
        ],
    )

    assert row["Cost"] == 539.5
