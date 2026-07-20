"""HTTP-шар замовлень — контракт із фронтом.

⚠️ Ці тести стережуть саме КОНТРАКТ (`frontend/src/lib/api/types.ts`), а не внутрішню
   логіку: форма чекауту вже написана й розрахована на конкретні імена полів і коди
   відповідей. Перейменування поля тут = мовчазна поломка оформлення в проді.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from orders.models import Order

pytestmark = pytest.mark.django_db

CREATE_URL = "/api/v1/orders"


def _body(product_id: int, **overrides) -> dict:
    data = {
        "items": [{"id": product_id, "qty": 1}],
        "expected_total": "15000.00",
        "idempotency_key": str(uuid4()),
        "last_name": "Шевченко",
        "first_name": "Тарас",
        "phone": "0671234567",
        "email": "taras@example.com",
        "delivery_method": "np_warehouse",
        "np_city_ref": "city-ref",
        "np_city_name": "Ужгород",
        "np_warehouse_ref": "wh-ref",
        "np_warehouse_name": "Відділення №1",
        "payment_method": "cod",
    }
    data.update(overrides)
    return data


def _post(client, body: dict):
    return client.post(CREATE_URL, data=json.dumps(body), content_type="application/json")


# ---------------------------------------------------------------------------
# POST /orders
# ---------------------------------------------------------------------------
def test_create_order_returns_contract_shape(client, product_factory) -> None:
    product = product_factory(price="15000.00")

    response = _post(client, _body(product.pk))

    assert response.status_code == 200
    data = response.json()

    # Рівно ті поля, які читає frontend/src/lib/api/types.ts::OrderOut.
    for field in (
        "number",
        "public_token",
        "status",
        "created_at",
        "payment_status",
        "payment_url",
        "items",
        "items_total",
        "delivery_price",
        "total",
    ):
        assert field in data, f"фронт очікує поле {field}"

    assert data["total"] == "15000.00"
    assert data["items"][0]["qty"] == 1
    # Накладений платіж → редіректу на оплату немає.
    assert data["payment_url"] is None


def test_price_change_returns_409_with_actual_total(client, product_factory) -> None:
    """Фронт ловить саме 409 і показує «ціни оновились» (checkout-form.tsx)."""
    product = product_factory(price="15000.00")

    response = _post(client, _body(product.pk, expected_total="9999.00"))

    assert response.status_code == 409
    data = response.json()
    assert data["detail"] == "price_changed"
    assert data["actual_total"] == "15000.00"
    assert data["changed_items"] == [product.pk]
    assert not Order.objects.exists()


def test_unavailable_item_returns_409(client, product_factory) -> None:
    product = product_factory(is_active=False)

    response = _post(client, _body(product.pk))

    assert response.status_code == 409
    assert response.json()["detail"] == "items_unavailable"


def test_bad_phone_returns_400(client, product_factory) -> None:
    product = product_factory()
    response = _post(client, _body(product.pk, phone="123"))
    assert response.status_code == 400


def test_double_submit_returns_same_order(client, product_factory) -> None:
    """Подвійний клік по «Оформити» — одне замовлення, не два."""
    product = product_factory()
    body = _body(product.pk)

    first = _post(client, body)
    second = _post(client, body)

    assert first.status_code == second.status_code == 200
    assert first.json()["number"] == second.json()["number"]
    assert Order.objects.count() == 1


# ---------------------------------------------------------------------------
# GET /orders/{public_token}
# ---------------------------------------------------------------------------
def test_read_order_by_public_token(client, product_factory) -> None:
    product = product_factory()
    token = _post(client, _body(product.pk)).json()["public_token"]

    response = client.get(f"{CREATE_URL}/{token}")

    assert response.status_code == 200
    assert response.json()["public_token"] == token


def test_unknown_token_returns_404(client) -> None:
    response = client.get(f"{CREATE_URL}/{uuid4()}")
    assert response.status_code == 404


def test_order_is_not_readable_by_number(client, product_factory) -> None:
    """🔴 ADR-014 (IDOR). Номер передбачуваний: `CMPX-260711-0042`.

    Якщо колись з'явиться роут по номеру, перебір за сьогоднішню дату віддасть ПІБ,
    телефон і адресу будь-якого покупця. Цей тест існує, щоб такий роут не з'явився
    непомітно.
    """
    product = product_factory()
    number = _post(client, _body(product.pk)).json()["number"]

    response = client.get(f"{CREATE_URL}/{number}")

    assert response.status_code == 404
