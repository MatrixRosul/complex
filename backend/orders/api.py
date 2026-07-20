"""Замовлення — HTTP-шар (Django Ninja).

    POST /api/v1/orders          оформлення (checkout)
    GET  /api/v1/orders/{token}  сторінка «дякуємо за замовлення»

⚠️ ЧОМУ РОУТ ЧИТАННЯ — ПО `public_token`, А НЕ ПО НОМЕРУ (ADR-014). Номер
   `CMPX-260711-0042` передбачуваний: дата + лічильник. Перебір `0001..0100` за
   сьогоднішню дату віддавав би ПІБ, телефон, email і адресу БУДЬ-ЯКОГО покупця —
   класичний IDOR, який тут коштує персональних даних. Токен — 122 біти ентропії.
   Ніколи не додавайте сюди пошук по `number`.

⚠️ CSRF немає СВІДОМО (див. config/api.py). Захист POST — ідемпотентний ключ
   (подвійний сабміт не створює друге замовлення) + throttling, а не cookie-токен,
   який недоступний fetch'у з Next.js SSR.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from django.http import HttpRequest
from ninja import Router
from ninja.responses import Status
from ninja.throttling import AnonRateThrottle, BaseThrottle

from orders.models import Order
from orders.schemas import (
    CreateOrderIn,
    OrderConflictOut,
    OrderOut,
    OrderUnavailableOut,
)
from orders.services.checkout import (
    CheckoutError,
    PriceChanged,
    UnavailableItems,
    create_order,
)

log = logging.getLogger(__name__)

router = Router(tags=["orders"])

#: Оформлення — дорога операція (перерахунок кошика + габарити + запис). 10/хв на IP
#: достатньо живій людині й відсікає скрипт, що молотить створення замовлень.
#: Анотація list[BaseThrottle] — щоб mypy не лаявся на інваріантність списку.
CHECKOUT_THROTTLE: list[BaseThrottle] = [AnonRateThrottle("10/m")]

#: Читання сторінки замовлення — дешеве, але токен теоретично можна перебирати.
ORDER_READ_THROTTLE: list[BaseThrottle] = [AnonRateThrottle("60/m")]

#: Спосіб оплати замовлення → `paytype` провайдера (payments.services.ALLOWED_PAYTYPES).
#: Ключі — рядки, а не TextChoices: `Order.payment_method` читається з БД як str.
_PAYTYPE: dict[str, str] = {
    str(Order.PaymentMethod.ONLINE): "card",
    str(Order.PaymentMethod.INSTALLMENT): "paypart",
}


# ---------------------------------------------------------------------------
# POST /orders
# ---------------------------------------------------------------------------
@router.post(
    "",
    response={
        200: OrderOut,
        400: dict,
        409: OrderConflictOut | OrderUnavailableOut,
    },
    auth=None,
    throttle=CHECKOUT_THROTTLE,
    url_name="orders_create",
    summary="Оформити замовлення",
)
def create_order_endpoint(request: HttpRequest, payload: CreateOrderIn, lang: str = "uk"):
    """Створити замовлення.

    409 — ціни змінились або товар зник; фронт перезавантажує кошик і показує нову суму.
    """
    try:
        order = create_order(payload, lang=lang, meta=_request_meta(request))
    except PriceChanged as exc:
        return Status(
            409,
            {
                "detail": "price_changed",
                "changed_items": exc.changed_items,
                "actual_total": exc.actual_total,
            },
        )
    except UnavailableItems as exc:
        return Status(
            409,
            {"detail": "items_unavailable", "unavailable_items": exc.unavailable_items},
        )
    except CheckoutError as exc:
        return Status(400, {"detail": str(exc)})

    return Status(200, _order_payload(order, create_payment=True))


# ---------------------------------------------------------------------------
# GET /orders/{public_token}
# ---------------------------------------------------------------------------
@router.get(
    "/{uuid:public_token}",
    response={200: OrderOut, 404: dict},
    auth=None,
    throttle=ORDER_READ_THROTTLE,
    url_name="orders_detail",
    summary="Замовлення за публічним токеном",
)
def order_detail_endpoint(request: HttpRequest, public_token: UUID):
    """Сторінка «дякуємо». ⚠️ Тільки по токену — див. ADR-014 у шапці модуля."""
    order = Order.objects.filter(public_token=public_token).prefetch_related("items").first()
    if order is None:
        return Status(404, {"detail": "Замовлення не знайдено"})

    # create_payment=False: перезавантаження сторінки не має плодити спроби оплати.
    return Status(200, _order_payload(order, create_payment=False))


# ---------------------------------------------------------------------------
# Складання відповіді
# ---------------------------------------------------------------------------
def _order_payload(order: Order, *, create_payment: bool) -> dict[str, Any]:
    return {
        "number": order.number,
        "public_token": order.public_token,
        "status": order.status,
        "created_at": order.created_at,
        "last_name": order.last_name,
        "first_name": order.first_name,
        "phone": order.phone,
        "email": order.email,
        "comment": order.comment,
        "delivery_method": order.delivery_method,
        "np_city_name": order.np_city_name,
        "np_warehouse_name": order.np_warehouse_name,
        "delivery_address": order.delivery_address,
        "ttn": order.ttn or None,
        "payment_method": order.payment_method,
        "payment_status": order.payment_status,
        "payment_url": _payment_url(order, create=create_payment),
        "items": [
            {
                "product_id": item.product_id,
                "name": item.name,
                "sku": item.sku,
                "qty": item.quantity,
                "price": item.price,
                "line_total": item.line_total,
                "main_image_url": item.image_url or None,
            }
            for item in order.items.all()
        ],
        "items_total": order.subtotal,
        "delivery_price": order.delivery_cost_estimate,
        "total": order.total,
    }


def _payment_url(order: Order, *, create: bool) -> str | None:
    """Посилання на оплату для онлайн-замовлень.

    ⚠️ ЗБІЙ ПЛАТІЖКИ НЕ СКАСОВУЄ ЗАМОВЛЕННЯ. Воно вже закомічене й менеджер його бачить;
       якщо LiqPay недоступний, покупець просто потрапляє на сторінку замовлення, а
       менеджер передзвонює. Кинути тут виняток означало б 500 на успішно оформленому
       замовленні — той самий клас помилки, від якого захищає circuit breaker у НП.
    """
    if order.payment_method not in _PAYTYPE:
        return None

    existing = order.payments.exclude(payment_url="").order_by("-created_at").first()
    if existing is not None:
        return existing.payment_url
    if not create:
        return None

    try:
        from payments.services import create_payment as make_payment

        payment = make_payment(order, _PAYTYPE[order.payment_method])
    except Exception:
        log.exception("Не вдалось створити платіж для замовлення %s", order.number)
        return None

    return payment.payment_url or None


def _request_meta(request: HttpRequest) -> dict[str, Any]:
    """IP / User-Agent / UTM — для аналітики й розбору спірних замовлень."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")

    return {
        "ip": ip or None,
        "user_agent": request.META.get("HTTP_USER_AGENT", ""),
        "utm": {key: value for key, value in request.GET.items() if key.startswith("utm_")},
    }
