"""
API доставки. Роутер тестуємо ІЗОЛЬОВАНО від config/api.py (його пише інший агент):
піднімаємо власний NinjaAPI і монтуємо в нього наш router.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import path
from ninja import NinjaAPI

from delivery.api import ROUTER_PREFIX
from delivery.api import router as delivery_router
from delivery.client import NovaPoshtaClient
from delivery.models import NPWarehouse
from delivery.tests.conftest import (
    KYIV_SETTLEMENT_REF,
    UZH_POSTOMAT_REF,
    UZHHOROD_SETTLEMENT_REF,
    make_failing_client,
)

pytestmark = pytest.mark.django_db

# Ізольований API тільки для тестів: реальне монтування робить агент «Зшивання».
_api = NinjaAPI(urls_namespace="delivery-tests")
_api.add_router(ROUTER_PREFIX, delivery_router)
urlpatterns = [path("api/", _api.urls)]


@pytest.fixture
def api(settings) -> Client:
    settings.ROOT_URLCONF = __name__
    # debug_toolbar чіпляється до ROOT_URLCONF і падає на reverse('djdt:render_panel'),
    # бо в нашому тестовому urlconf його роутів немає. До доставки він стосунку не має.
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "debug_toolbar" not in m]
    return Client()


def get(api: Client, url: str):
    r = api.get(url)
    assert r.status_code == 200, r.content
    return r.json()


# ---------------------------------------------------------------------------
# Довідники
# ---------------------------------------------------------------------------


def test_areas(api, np_refs):
    data = get(api, "/api/delivery/areas")

    assert {a["name"] for a in data} == {"Закарпатська", "Київська", "Львівська"}


def test_settlements_autocomplete(api, np_refs):
    data = get(api, "/api/delivery/settlements?q=Ужг")

    assert data[0]["name"] == "Ужгород"
    assert data[0]["present"] == "м. Ужгород, Закарпатська обл."
    assert data[0]["area"] == "Закарпатська"
    # 🔴 Фронту віддаємо ОБИДВА ref-и: він шле назад `ref`, ми самі мапимо на CityRef.
    assert data[0]["ref"] != data[0]["delivery_city_ref"]


def test_settlements_tolerates_typos(api, np_refs):
    """Trigram: «Ужгорд» без «о» усе одно знаходить Ужгород."""
    data = get(api, "/api/delivery/settlements?q=Ужгорд")

    assert any(s["name"] == "Ужгород" for s in data)


def test_settlements_needs_two_chars(api, np_refs):
    assert get(api, "/api/delivery/settlements?q=У") == []


def test_warehouses_of_a_settlement(api, np_refs):
    data = get(api, f"/api/delivery/warehouses?settlement={UZHHOROD_SETTLEMENT_REF}")

    refs = {w["ref"] for w in data}
    assert len(data) == 4  # закрите відділення не пропонуємо
    assert UZH_POSTOMAT_REF in refs
    # ServiceType віддається разом з точкою — щоб фронт не міг послати неправильний.
    postomat = next(w for w in data if w["ref"] == UZH_POSTOMAT_REF)
    assert postomat["service_type"] == "WarehousePostomat"
    assert postomat["category"] == "Postomat"


def test_warehouses_filtered_by_category(api, np_refs):
    """В Ужгороді поштоматів у 7 разів більше за відділення → UI мусить групувати."""
    data = get(
        api, f"/api/delivery/warehouses?settlement={UZHHOROD_SETTLEMENT_REF}&category=Postomat"
    )

    assert [w["ref"] for w in data] == [UZH_POSTOMAT_REF]


# ---------------------------------------------------------------------------
# 🔴 Фільтр поштоматів за габаритами — найчастіший баг чекаутів
# ---------------------------------------------------------------------------


def test_fridge_is_not_offered_a_postomat(api, np_refs, fridge):
    """Холодильник 171,5 × 56 × 35 см / 61,5 кг у комірку 40×30×60 не влізе фізично.

    Не влізе і в «Відділення №1 (до 30 кг)» — там ліміт місця 30 кг.
    Лишається тільки вантажне відділення.
    """
    data = get(
        api,
        f"/api/delivery/warehouses?settlement={UZHHOROD_SETTLEMENT_REF}&product_id={fridge.pk}",
    )

    refs = {w["ref"] for w in data}
    assert UZH_POSTOMAT_REF not in refs
    assert refs == {"16006cee-e1c2-11e3-8c4a-0050568002cf"}  # вантажне відділення


def test_small_item_fits_the_postomat(api, np_refs, small_item):
    """Чайник 25×22×30 см / 1,5 кг у комірку 40×30×60 влазить — з поворотом коробки."""
    data = get(
        api,
        f"/api/delivery/warehouses?settlement={UZHHOROD_SETTLEMENT_REF}&product_id={small_item.pk}",
    )

    assert UZH_POSTOMAT_REF in {w["ref"] for w in data}


def test_warehouse_without_np_limits_is_never_filtered_out(api, np_refs, fridge):
    """NULL-обмеження = «НП їх не віддала», а не «нічого не приймає».

    Відсікати такі точки — значить лишити покупця з порожнім дропдавном.
    """
    kyiv_cargo = NPWarehouse.objects.get(ref="1ec09d88-e1c2-11e3-8c4a-0050568002ff")
    assert kyiv_cargo.max_width_cm is None

    data = get(
        api,
        f"/api/delivery/warehouses?settlement={KYIV_SETTLEMENT_REF}&product_id={fridge.pk}",
    )

    assert kyiv_cargo.ref in {w["ref"] for w in data}


# ---------------------------------------------------------------------------
# POST /quote — ЗАВЖДИ 200
# ---------------------------------------------------------------------------


def post_quote(api: Client, payload: dict):
    r = api.post("/api/delivery/quote", data=json.dumps(payload), content_type="application/json")
    # 🔴 Контракт: 200 у будь-якому разі. 5xx тут = заблокована кнопка «Оформити».
    assert r.status_code == 200, r.content
    return r.json()


def test_quote_single_product(api, np_refs, np_box):
    data = post_quote(api, {"product_id": np_box.pk, "settlement_ref": KYIV_SETTLEMENT_REF})

    assert data["ok"] is True
    assert Decimal(data["cost"]) == Decimal("539.5")
    assert Decimal(data["chargeable_weight_kg"]) == Decimal("37.500")
    assert data["dims_source"] == "spec"


def test_quote_cart(api, np_refs, fridge, small_item):
    data = post_quote(
        api,
        {
            "items": [
                {"product_id": fridge.pk, "qty": 2},
                {"product_id": small_item.pk, "qty": 1},
            ],
            "settlement_ref": KYIV_SETTLEMENT_REF,
        },
    )

    assert data["ok"] is True
    assert data["seats_amount"] == 3
    assert Decimal(data["cost_declared"]) == Decimal("48400.00")


def test_quote_returns_200_when_np_is_down(api, np_refs, np_box, monkeypatch, breaker):
    """🔴 НАЙВАЖЛИВІШИЙ ТЕСТ ФАЙЛУ.

    НП лежить → 200 + {"ok": false, "reason": "np_unavailable"}, а НЕ 500 і НЕ таймаут.
    Фронт показує «Вартість доставки повідомить менеджер» і НЕ блокує «Оформити».
    """
    monkeypatch.setattr(
        "delivery.services.calculator.get_web_client", lambda: make_failing_client(breaker)
    )

    data = post_quote(api, {"product_id": np_box.pk, "settlement_ref": KYIV_SETTLEMENT_REF})

    assert data["ok"] is False
    assert data["reason"] == "np_unavailable"
    assert data["cost"] is None


def test_quote_returns_200_on_unknown_settlement(api, np_refs, np_box):
    data = post_quote(api, {"product_id": np_box.pk, "settlement_ref": "00000000-0000-0000"})

    assert data["ok"] is False
    assert data["reason"] == "unknown_settlement"


def test_quote_returns_200_on_unknown_product(api, np_refs):
    data = post_quote(api, {"product_id": 999_999, "settlement_ref": KYIV_SETTLEMENT_REF})

    assert data["ok"] is False
    assert data["reason"] == "empty_cart"


def test_quote_uses_the_warehouses_service_type(api, np_refs, small_item, monkeypatch):
    """Обрали поштомат → ціна рахується як WarehousePostomat (107.5, а не 97.5 за 2 кг)."""
    monkeypatch.setattr(
        "delivery.services.calculator.get_web_client",
        lambda: NovaPoshtaClient(api_key="", force_fixtures=True),
    )

    data = post_quote(api, {"product_id": small_item.pk, "warehouse_ref": UZH_POSTOMAT_REF})

    assert data["ok"] is True
    assert data["service_type"] == "WarehousePostomat"
