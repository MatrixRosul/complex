"""
Калькулятор: об'ємна вага, кеш, деградація при недоступній НП (INTEGRATIONS §1.7–1.8).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.cache import cache

from delivery.models import DeliveryQuote, DimsSource, ServiceType
from delivery.services.calculator import QuoteItem, Reason, quote
from delivery.tests.conftest import (
    KYIV_SETTLEMENT_REF,
    UZH_POSTOMAT_REF,
    make_failing_client,
    make_json_client,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Об'ємна вага — головна причина існування цього калькулятора
# ---------------------------------------------------------------------------


def test_quote_sends_options_seat_and_charges_by_volume(np_refs, np_box, np_client):
    """🔴 Ужгород→Київ, 2 кг, коробка 50×50×60 → 539.5 грн, а не 97.5.

    Це той самий запит, що заміряний живцем в INTEGRATIONS §1.7. Калькулятор без
    OptionsSeat повернув би 97.5 — заниження в 5.5× на КОЖНОМУ замовленні.
    """
    r = quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=np_client)

    assert r.ok is True
    assert r.cost == Decimal("539.5")
    assert r.weight_kg == Decimal("2.000")
    assert r.chargeable_weight_kg == Decimal("37.500")  # об'ємна, а не фактична
    assert r.volume_m3 == Decimal("0.15000")
    assert r.dims_source == DimsSource.SPEC
    assert r.cost_declared == Decimal("1500.00")  # оголошена вартість = ціна товару


def test_quote_resolves_settlement_ref_to_canonical_city_ref(np_refs, np_box, np_client):
    """🔴 SettlementRef ≠ CityRef. У CityRecipient має піти DeliveryCity ref."""
    r = quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=np_client)

    assert r.city_ref == "8d5a980d-391c-11dd-90d9-001a92567626"  # CityRef, не SettlementRef
    assert r.city_ref != KYIV_SETTLEMENT_REF


def test_service_type_follows_the_chosen_point(np_refs, small_item, np_client):
    """Обрали поштомат → ServiceType=WarehousePostomat, а не WarehouseWarehouse.

    Інакше недорахували б ~10 грн на кожному замовленні (97.5 проти 107.5).
    """
    r = quote(
        [QuoteItem(small_item, 1)],
        warehouse_ref=UZH_POSTOMAT_REF,
        service_type=ServiceType.WAREHOUSE_WAREHOUSE,  # ← фронт помилився, сервіс виправляє
        client=np_client,
    )

    assert r.ok is True
    assert r.service_type == ServiceType.WAREHOUSE_POSTOMAT


def test_cart_quote_sums_seats(np_refs, fridge, small_item, np_client):
    r = quote(
        [QuoteItem(fridge, 2), QuoteItem(small_item, 1)],
        settlement_ref=KYIV_SETTLEMENT_REF,
        client=np_client,
    )

    assert r.ok is True
    assert r.seats_amount == 3
    assert r.weight_kg == Decimal("124.500")
    assert r.chargeable_weight_kg == Decimal("172.195")
    assert r.cost_declared == Decimal("48400.00")  # 23600×2 + 1200


# ---------------------------------------------------------------------------
# 🔴 Деградація: НП лежить — магазин працює
# ---------------------------------------------------------------------------


def test_quote_returns_ok_false_when_np_is_down(np_refs, np_box, breaker):
    """НП у таймауті → ok:false, а НЕ виняток і не 500."""
    r = quote(
        [QuoteItem(np_box, 1)],
        settlement_ref=KYIV_SETTLEMENT_REF,
        client=make_failing_client(breaker),
    )

    assert r.ok is False
    assert r.reason == Reason.NP_UNAVAILABLE
    assert r.cost is None
    # Габарити порахувати вдалось і без НП — фронту є що показати.
    assert r.chargeable_weight_kg == Decimal("37.500")


def test_quote_returns_ok_false_when_breaker_is_open(np_refs, np_box, breaker):
    """Після 5 падінь breaker відкритий — далі відповідаємо миттєво, без сокета."""
    client = make_failing_client(breaker)
    for _ in range(5):
        quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=client)

    assert breaker.is_open() is True

    r = quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=client)
    assert r.ok is False
    assert r.reason == Reason.NP_UNAVAILABLE


def test_quote_returns_ok_false_on_np_logical_error(np_refs, np_box, breaker):
    """success:false → np_error (не np_unavailable): НП жива, відмовила логічно."""
    client = make_json_client(
        {"success": False, "data": [], "errors": [], "errorCodes": ["X"]}, breaker
    )
    r = quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=client)

    assert r.ok is False
    assert r.reason == Reason.NP_ERROR


def test_unknown_settlement_is_not_an_exception(np_refs, np_box, np_client):
    r = quote(
        [QuoteItem(np_box, 1)],
        settlement_ref="00000000-dead-beef-0000-000000000000",
        client=np_client,
    )

    assert r.ok is False
    assert r.reason == Reason.UNKNOWN_SETTLEMENT


def test_empty_cart(np_client):
    assert quote([], settlement_ref=KYIV_SETTLEMENT_REF, client=np_client).reason == (
        Reason.EMPTY_CART
    )


# ---------------------------------------------------------------------------
# Кеш
# ---------------------------------------------------------------------------


def test_second_identical_quote_comes_from_cache(np_refs, np_box, np_client):
    first = quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=np_client)
    second = quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=np_client)

    assert first.cached is False
    assert second.cached is True
    assert second.cost == first.cost


def test_cache_key_includes_declared_cost(np_refs, np_box, np_client):
    """🔴 Ціна товару = оголошена вартість, і вона змінюється 4×/добу.

    Ключ без неї віддавав би застарілу вартість доставки НЕСКІНЧЕННО.
    """
    quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=np_client)

    np_box.price = Decimal("1900")
    np_box.save()

    r = quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=np_client)

    assert r.cached is False, "ціна змінилась, а ми віддали кеш зі старою оголошеною вартістю"
    assert r.cost_declared == Decimal("1900.00")


def test_failure_is_never_cached(np_refs, np_box, breaker, np_client):
    """Провал НЕ кешується — інакше після хвилинного збою НП ми б 24 год віддавали
    ok:false усім покупцям, хоча НП давно піднялась."""
    failed = quote(
        [QuoteItem(np_box, 1)],
        settlement_ref=KYIV_SETTLEMENT_REF,
        client=make_failing_client(breaker),
    )
    assert failed.ok is False

    # НП піднялась — наступний же запит іде в НП і дає ціну, а не тягне ok:false з кешу.
    r = quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=np_client)

    assert r.ok is True
    assert r.cached is False
    assert r.cost == Decimal("539.5")


def test_analytics_row_is_sampled_not_written_every_time(np_refs, np_box, np_client, monkeypatch):
    """DeliveryQuote — аналітика з семплюванням 10%, а не кеш.

    Публічний неавторизований ендпоінт + INSERT на кожен запит = вектор забивання диска.
    """
    monkeypatch.setattr("delivery.services.calculator.ANALYTICS_SAMPLE_RATE", 0.0)
    quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=np_client)
    assert DeliveryQuote.objects.count() == 0  # не влучили в семпл

    cache.clear()
    monkeypatch.setattr("delivery.services.calculator.ANALYTICS_SAMPLE_RATE", 1.0)
    quote([QuoteItem(np_box, 1)], settlement_ref=KYIV_SETTLEMENT_REF, client=np_client)

    row = DeliveryQuote.objects.get()
    assert row.cost_delivery == Decimal("539.50")
    assert row.dims_source == DimsSource.SPEC
    assert row.volume_m3 == Decimal("0.15000")
