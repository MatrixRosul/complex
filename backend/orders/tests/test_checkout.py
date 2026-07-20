"""Checkout — тести інваріантів, на яких тримаються гроші."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from orders.models import Order, OrderItem
from orders.services.checkout import (
    CheckoutError,
    PriceChanged,
    UnavailableItems,
    create_order,
    normalize_phone,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Телефон
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    ["0671234567", "+380671234567", "380671234567", "+38 (067) 123-45-67", "671234567"],
)
def test_normalize_phone_forms(raw: str) -> None:
    """Усі побутові записи зводяться до одного канонічного вигляду."""
    assert normalize_phone(raw) == "+380671234567"


@pytest.mark.parametrize("raw", ["", "123", "не телефон", "+1 555 010 9999"])
def test_normalize_phone_rejects_garbage(raw: str) -> None:
    with pytest.raises(CheckoutError):
        normalize_phone(raw)


# ---------------------------------------------------------------------------
# Щасливий шлях
# ---------------------------------------------------------------------------
def test_creates_order_with_snapshot(product_factory, payload_factory) -> None:
    product = product_factory(price="15000.00")
    payload = payload_factory(
        items=[{"id": product.pk, "qty": 2}],
        expected_total="30000.00",
    )

    order = create_order(payload)

    assert order.number.startswith("CMPX-")
    assert order.total == Decimal("30000.00")
    assert order.phone == "+380671234567"
    assert order.status == Order.Status.NEW
    # Накладений платіж — оплата не потрібна.
    assert order.payment_status == Order.PaymentStatus.NOT_REQUIRED

    item = order.items.get()
    assert item.quantity == 2
    assert item.line_total == Decimal("30000.00")
    # Снапшот, а не FK: назва й ціна не «попливуть» від зміни каталогу.
    assert item.name == product.name
    assert item.sku == product.sku


def test_writes_status_history(product_factory, payload_factory) -> None:
    product = product_factory()
    order = create_order(
        payload_factory(items=[{"id": product.pk, "qty": 1}], expected_total="15000.00")
    )

    entry = order.history.get()
    assert entry.from_status == ""
    assert entry.to_status == Order.Status.NEW


def test_stores_effective_dims(product_factory, payload_factory) -> None:
    """ADR-021: у позицію лягає те, що РЕАЛЬНО піде в Нову Пошту.

    Порожні габарити = недоплата за доставку в рази, тому «немає розмірів» —
    недопустимий результат навіть для товару без характеристик.
    """
    product = product_factory()
    order = create_order(
        payload_factory(items=[{"id": product.pk, "qty": 1}], expected_total="15000.00")
    )

    item = order.items.get()
    assert item.weight_kg is not None
    assert item.volume_m3 is not None
    assert item.dims_source  # spec | category | default — але не порожньо


def test_np_service_type_matches_delivery_method(product_factory, payload_factory) -> None:
    """Обрали поштомат — у НП має піти WarehousePostomat, інакше недорахунок."""
    product = product_factory()
    order = create_order(
        payload_factory(
            items=[{"id": product.pk, "qty": 1}],
            expected_total="15000.00",
            delivery_method="np_postomat",
        )
    )
    assert order.np_service_type == "WarehousePostomat"


# ---------------------------------------------------------------------------
# Ціни рахує сервер
# ---------------------------------------------------------------------------
def test_price_mismatch_raises_409(product_factory, payload_factory) -> None:
    """Клієнт прислав стару суму → 409, а не тихий продаж за старою ціною."""
    product = product_factory(price="15000.00")
    payload = payload_factory(
        items=[{"id": product.pk, "qty": 1}],
        expected_total="9999.00",  # ціна, яку покупець бачив до синку
    )

    with pytest.raises(PriceChanged) as exc:
        create_order(payload)

    assert exc.value.actual_total == Decimal("15000.00")
    assert exc.value.changed_items == [product.pk]
    assert not Order.objects.exists()


def test_client_total_is_never_trusted(product_factory, payload_factory) -> None:
    """Занижена сума не проходить навіть при збігу кількості позицій."""
    product = product_factory(price="15000.00")

    with pytest.raises(PriceChanged):
        create_order(
            payload_factory(items=[{"id": product.pk, "qty": 2}], expected_total="15000.00")
        )


def test_inactive_product_rejected(product_factory, payload_factory) -> None:
    product = product_factory(is_active=False)

    with pytest.raises(UnavailableItems) as exc:
        create_order(
            payload_factory(items=[{"id": product.pk, "qty": 1}], expected_total="15000.00")
        )

    assert exc.value.unavailable_items == [product.pk]


def test_empty_cart_rejected(payload_factory) -> None:
    with pytest.raises(CheckoutError):
        create_order(payload_factory(items=[], expected_total="0"))


# ---------------------------------------------------------------------------
# Ідемпотентність
# ---------------------------------------------------------------------------
def test_same_idempotency_key_returns_same_order(product_factory, payload_factory) -> None:
    """Подвійний сабміт форми не створює друге замовлення і другий платіж."""
    product = product_factory()
    key = str(uuid4())

    first = create_order(
        payload_factory(
            items=[{"id": product.pk, "qty": 1}],
            expected_total="15000.00",
            idempotency_key=key,
        )
    )
    second = create_order(
        payload_factory(
            items=[{"id": product.pk, "qty": 1}],
            expected_total="15000.00",
            idempotency_key=key,
        )
    )

    assert first.pk == second.pk
    assert Order.objects.count() == 1
    assert OrderItem.objects.count() == 1


def test_distinct_keys_create_distinct_orders(product_factory, payload_factory) -> None:
    product = product_factory()
    for _ in range(2):
        create_order(
            payload_factory(items=[{"id": product.pk, "qty": 1}], expected_total="15000.00")
        )

    numbers = set(Order.objects.values_list("number", flat=True))
    assert len(numbers) == 2  # номери з sequence не повторюються


# ---------------------------------------------------------------------------
# Оплата
# ---------------------------------------------------------------------------
def test_online_payment_requires_email(product_factory, payload_factory) -> None:
    product = product_factory()

    with pytest.raises(CheckoutError, match="Email"):
        create_order(
            payload_factory(
                items=[{"id": product.pk, "qty": 1}],
                expected_total="15000.00",
                payment_method="online",
                email="",
            )
        )


def test_online_payment_sets_pending(product_factory, payload_factory) -> None:
    product = product_factory()
    order = create_order(
        payload_factory(
            items=[{"id": product.pk, "qty": 1}],
            expected_total="15000.00",
            payment_method="online",
        )
    )
    assert order.payment_status == Order.PaymentStatus.PENDING


def test_installment_requires_all_items_to_support_it(product_factory, payload_factory) -> None:
    """Інваріант ADR-017: розстрочка — AND по ВСІХ позиціях, рахує сервер."""
    good = product_factory(sku="SKU-A", price="10000.00", installment=True)
    bad = product_factory(sku="SKU-B", price="5000.00", installment=False)

    with pytest.raises(CheckoutError, match="частинами"):
        create_order(
            payload_factory(
                items=[{"id": good.pk, "qty": 1}, {"id": bad.pk, "qty": 1}],
                expected_total="15000.00",
                payment_method="installment",
            )
        )
