"""Фікстури платіжного модуля. Реальних платежів немає: sandbox-ключі + моки провайдера."""

from __future__ import annotations

import base64
import hashlib
import json
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from orders.models import Order, OrderItem
from payments.models import Payment

#: Тестова пара ключів. Бойових ключів у тестах немає і бути не може.
TEST_PUBLIC_KEY = "sandbox_i00000000"
TEST_PRIVATE_KEY = "sandbox_private_key_test"  # тестовий ключ пісочниці


@pytest.fixture(autouse=True)
def liqpay_settings(settings):
    settings.LIQPAY_PUBLIC_KEY = TEST_PUBLIC_KEY
    settings.LIQPAY_PRIVATE_KEY = TEST_PRIVATE_KEY
    settings.LIQPAY_SANDBOX = True
    settings.SITE_URL = "https://complex.ua"
    settings.PAYMENTS_PUBLIC_URL = "https://api.complex.ua"
    settings.DEBUG = False
    # Кеш — локальний: SiteSettings.get_solo() не має тягти дані з робочого Redis.
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "payments-tests",
        }
    }
    from django.core.cache import cache

    cache.clear()
    return settings


@pytest.fixture
def order_factory(db):
    """Замовлення з позиціями. Товари (catalog.Product) не потрібні: OrderItem — снапшот."""

    def _make(
        *,
        items: list[tuple[str, str, int, bool]] | None = None,
        status: str = Order.Status.NEW,
        payment_status: str = Order.PaymentStatus.PENDING,
    ) -> Order:
        # (sku, price, qty, installment_available)
        items = items if items is not None else [("SKU-1", "15000.00", 1, True)]
        lines = [(sku, Decimal(price), qty, inst) for sku, price, qty, inst in items]
        subtotal = sum((p * q for _, p, q, _ in lines), Decimal("0"))

        order = Order.objects.create(
            number=Order.generate_number(),
            idempotency_key=uuid4(),
            status=status,
            payment_status=payment_status,
            last_name="Іваненко",
            first_name="Іван",
            phone="+380501234567",
            email="ivan@example.com",
            delivery_method=Order.DeliveryMethod.PICKUP,
            payment_method=Order.PaymentMethod.ONLINE,
            subtotal=subtotal,
            total=subtotal,
        )
        for sku, price, qty, inst in lines:
            OrderItem.objects.create(
                order=order,
                sku=sku,
                name=f"Холодильник {sku}",
                price=price,
                quantity=qty,
                line_total=price * qty,
                installment_available=inst,
            )
        return order

    return _make


@pytest.fixture
def payment_factory(db, order_factory):
    def _make(order: Order | None = None, **kwargs: Any) -> Payment:
        order = order or order_factory()
        defaults: dict[str, Any] = {
            "order": order,
            "provider": Payment.Provider.LIQPAY,
            "amount": order.total,
            "currency": "UAH",
            "paytype": "card",
            "status": Payment.Status.CREATED,
        }
        defaults.update(kwargs)
        return Payment.objects.create(**defaults)

    return _make


# ---------------------------------------------------------------------------
# Тіло вебхука — будується НЕЗАЛЕЖНОЮ реалізацією підпису (не нашим клієнтом),
# інакше тест перевіряв би сам себе.
# ---------------------------------------------------------------------------
def encode(payload: dict[str, Any]) -> str:
    return base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def sign(data: str, private_key: str = TEST_PRIVATE_KEY) -> str:
    raw = (private_key + data + private_key).encode("utf-8")
    return base64.b64encode(hashlib.sha1(raw).digest()).decode("ascii")  # noqa: S324


def callback_payload(
    payment: Payment,
    *,
    status: str = "success",
    amount: Decimal | float | None = None,
    end_date: int = 1_752_400_060_000,
    paytype: str = "card",
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "pay",
        "payment_id": 2_100_000_001,
        "status": status,
        "version": 3,
        "type": "buy",
        "paytype": paytype,
        "public_key": TEST_PUBLIC_KEY,
        "order_id": str(payment.reference),
        "liqpay_order_id": "LP-1",
        "description": f"Замовлення №{payment.order.number} — Complex",
        "sender_card_mask2": "424242*42",
        "sender_card_bank": "pb",
        "amount": float(amount if amount is not None else payment.amount),
        "currency": "UAH",
        "receiver_commission": 225.0,
        "create_date": 1_752_400_000_000,
        "end_date": end_date,
    }
    payload.update(extra)
    return payload


def webhook_form(payload: dict[str, Any], *, private_key: str = TEST_PRIVATE_KEY) -> dict[str, str]:
    data = encode(payload)
    return {"data": data, "signature": sign(data, private_key)}
