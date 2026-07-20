"""Схеми API замовлень.

⚠️ КОНТРАКТ ЗАДАНИЙ ФРОНТОМ, а не навпаки. Форма чекауту й типи вже існували, поки
   бекенд-роут ще ні, тому джерело істини тут — `frontend/src/lib/api/types.ts`
   (`CreateOrderRequest`, `OrderOut`, `OrderItemOut`, `OrderConflict`). Будь-яке
   перейменування поля тут ламає готовий фронт — звіряйтесь із types.ts.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from ninja import Schema

__all__ = [
    "CreateOrderIn",
    "OrderConflictOut",
    "OrderItemOut",
    "OrderOut",
    "OrderUnavailableOut",
]


class CartItemIn(Schema):
    """Позиція кошика. Рівно `{id, qty}` — цін клієнт не присилає (ADR-017).

    Саме тому сервер не може сказати, ЯКІ позиції подорожчали: він не знає, що було
    на екрані. Ціни живуть лише в БД.
    """

    id: int
    qty: int = 1


class CreateOrderIn(Schema):
    """Тіло `POST /orders` — дзеркало types.ts::CreateOrderRequest."""

    items: list[CartItemIn]
    expected_total: str
    """Сума, яку бачив покупець. Не використовується для розрахунку — тільки для звірки."""

    idempotency_key: UUID
    """Клієнт генерує РАЗ на checkout. Подвійний сабміт не створює друге замовлення."""

    last_name: str
    first_name: str
    phone: str
    email: str | None = None
    comment: str | None = None

    delivery_method: str
    np_city_ref: str | None = None
    np_city_name: str | None = None
    np_settlement_ref: str | None = None
    np_warehouse_ref: str | None = None
    np_warehouse_name: str | None = None
    delivery_address: str | None = None

    payment_method: str


class OrderItemOut(Schema):
    product_id: int | None
    name: str
    sku: str
    qty: int
    price: Decimal
    line_total: Decimal
    main_image_url: str | None


class OrderOut(Schema):
    """Дзеркало types.ts::OrderOut."""

    number: str
    public_token: UUID
    status: str
    created_at: datetime

    last_name: str
    first_name: str
    phone: str
    email: str
    comment: str

    delivery_method: str
    np_city_name: str
    np_warehouse_name: str
    delivery_address: str
    ttn: str | None

    payment_method: str
    payment_status: str
    payment_url: str | None
    """Куди редіректити на оплату. ⚠️ Для онлайн-оплати це ЄДИНА точка входу в платіж:
    кнопки «оплатити» на сторінці замовлення фронт не має."""

    items: list[OrderItemOut]
    items_total: Decimal
    delivery_price: Decimal | None
    total: Decimal


class OrderConflictOut(Schema):
    """409: ціни змінились між переглядом кошика і сабмітом."""

    detail: str = "price_changed"
    changed_items: list[int]
    actual_total: Decimal


class OrderUnavailableOut(Schema):
    """409: товару вже немає в продажу."""

    detail: str = "items_unavailable"
    unavailable_items: list[int]
