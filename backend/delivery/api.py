"""
Ninja-роутер доставки.

⚠️ У `config/api.py` НЕ підключається — це робить окремий агент на фазі «Зшивання»:

    from delivery.api import router as delivery_router
    api.add_router(ROUTER_PREFIX, delivery_router)      # ROUTER_PREFIX == "/delivery"

Шляхи всередині — відносні («/areas»), тож повні URL-и вийдуть:
    GET  /api/delivery/areas
    GET  /api/delivery/settlements?q=Ужг
    GET  /api/delivery/warehouses?settlement=<ref>&product_id=42
    POST /api/delivery/quote

──────────────────────────────────────────────────────────────────────────────
🔴 ГОЛОВНЕ ПРАВИЛО ЦЬОГО ФАЙЛУ: /quote ЗАВЖДИ ВІДДАЄ 200
──────────────────────────────────────────────────────────────────────────────
Навіть коли НП лежить, коли відкритий circuit breaker, коли прилетів битий ref —
відповідь 200 з `{"ok": false, "reason": "..."}`. НІКОЛИ 5xx і НІКОЛИ таймаут.

Фронт НЕ МАЄ блокувати кнопку «Оформити» через недоступність калькулятора: при
`ok: false` показує «Вартість доставки повідомить менеджер», а замовлення оформлюється
з `delivery_cost_estimate = NULL`. Недоступний калькулятор не сміє зупиняти продажі.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.shortcuts import get_object_or_404
from ninja import Field, Router, Schema
from ninja.throttling import AnonRateThrottle

from catalog.models import Product
from delivery.models import ServiceType
from delivery.services.calculator import QuoteItem, Reason, quote
from delivery.services.dims import effective_dims
from delivery.services.refs import list_areas, list_warehouses, search_settlements

log = logging.getLogger(__name__)

#: Префікс, під яким роутер очікує бути змонтованим у config/api.py.
ROUTER_PREFIX = "/delivery"

router = Router(tags=["delivery"])

#: Публічний неавторизований ендпоінт → тротлінг per-IP (INTEGRATIONS §1.8),
#: щоб не спалити невідомий (НП його не публікує) ліміт запитів.
QUOTE_THROTTLE = [AnonRateThrottle("30/m")]


# ---------------------------------------------------------------------------
# Схеми
# ---------------------------------------------------------------------------


class AreaOut(Schema):
    ref: str
    name: str


class SettlementOut(Schema):
    ref: str
    #: 🔴 SettlementRef ≠ CityRef. Фронт передає назад САМЕ `ref`; канонічний CityRef
    #:    сервіс підставляє сам (INTEGRATIONS §1.4).
    delivery_city_ref: str
    name: str
    present: str
    area: str = Field(..., alias="area.name")
    warehouses_count: int


class WarehouseOut(Schema):
    ref: str
    number: str
    description: str
    short_address: str
    category: str
    #: ServiceType, який ПІДЕ в getDocumentPrice саме для цієї точки. Поштомат ≠ відділення:
    #: обрали поштомат, послали WarehouseWarehouse → недорахували ~10 грн на замовленні.
    service_type: str
    latitude: float | None = None
    longitude: float | None = None
    max_width_cm: float | None = None
    max_height_cm: float | None = None
    max_length_cm: float | None = None
    place_max_weight_kg: float | None = None


class QuoteItemIn(Schema):
    product_id: int
    qty: int = Field(1, ge=1, le=99)


class QuoteIn(Schema):
    settlement_ref: str = ""
    warehouse_ref: str = ""
    #: Або один товар…
    product_id: int | None = None
    qty: int = Field(1, ge=1, le=99)
    #: …або кошик.
    items: list[QuoteItemIn] = Field(default_factory=list)
    service_type: str = ""
    #: Накладений платіж: сума, яку кур'єр везе назад. None — не рахуємо.
    redelivery: Decimal | None = None


class QuoteOut(Schema):
    ok: bool
    reason: str | None = None
    cost: Decimal | None = None
    cost_redelivery: Decimal | None = None
    currency: str = "UAH"

    service_type: str = ""
    city_ref: str = ""
    seats_amount: int = 0
    weight_kg: Decimal = Decimal("0")
    volume_m3: Decimal = Decimal("0")
    #: 🔴 max(фактична, об'ємна). Саме від цього числа НП рахує ціну — віддаємо його,
    #:    щоб було видно, коли платимо за об'єм, а не за вагу.
    chargeable_weight_kg: Decimal = Decimal("0")
    cost_declared: Decimal = Decimal("0")
    dims_source: str = ""
    cached: bool = False


# ---------------------------------------------------------------------------
# Довідники — читаємо з НАШОЇ БД, у НП не ходимо
# ---------------------------------------------------------------------------


@router.get("/areas", response=list[AreaOut], summary="Області")
def areas(request) -> list:
    return list(list_areas())


@router.get("/settlements", response=list[SettlementOut], summary="Автокомпліт населеного пункту")
def settlements(request, q: str = "", limit: int = 10) -> list:
    """Trigram-пошук по нашій БД: <5 мс проти 200–400 мс через API НП."""
    return search_settlements(q, limit=min(max(limit, 1), 50))


@router.get("/warehouses", response=list[WarehouseOut], summary="Відділення / поштомати")
def warehouses(
    request,
    settlement: str = "",
    city: str = "",
    category: str = "",
    product_id: int | None = None,
    limit: int = 200,
) -> list:
    """Точки видачі населеного пункту.

    🔴 `product_id` → відсікаємо точки, куди посилка ФІЗИЧНО не влізе. Холодильник
       171,5×56×35 см у комірку поштомата 40×30×60 не заходить, і показувати такий
       поштомат у чекауті — найчастіший баг галузі.
       Фільтр враховує поворот коробки і не відсікає точки, для яких НП обмежень не дала.

    ⚠️ Групувати за `category` (Відділення / Поштомат / Пункт видачі) — робота UI: в
       Ужгороді поштоматів 148 проти 20 відділень, і плоский список ховає відділення.
    """
    fits = None
    if product_id is not None:
        product = get_object_or_404(Product.objects.select_related("category"), pk=product_id)
        dims = effective_dims(product)
        # Вага одного МІСЦЯ (не всього замовлення): обмеження НП — на місце.
        fits = (dims.width_cm, dims.height_cm, dims.depth_cm, dims.weight_kg)

    return list_warehouses(
        settlement_ref=settlement,
        city_ref=city,
        category=category,
        fits=fits,
        limit=min(max(limit, 1), 500),
    )


# ---------------------------------------------------------------------------
# Калькулятор
# ---------------------------------------------------------------------------


@router.post("/quote", response=QuoteOut, throttle=QUOTE_THROTTLE, summary="Вартість доставки")
def quote_delivery(request, payload: QuoteIn) -> QuoteOut:
    """🔴 ЗАВЖДИ 200 — навіть коли НП лежить. Див. шапку модуля."""
    wanted: list[tuple[int, int]] = [(i.product_id, i.qty) for i in payload.items]
    if payload.product_id is not None:
        wanted.append((payload.product_id, payload.qty))

    if not wanted:
        return QuoteOut(ok=False, reason=Reason.EMPTY_CART)

    products = {
        p.pk: p
        for p in Product.objects.select_related("category").filter(
            pk__in=[pid for pid, _ in wanted]
        )
    }
    items = [QuoteItem(product=products[pid], qty=q) for pid, q in wanted if pid in products]
    if not items:
        # Неіснуючий товар — теж 200: 404 тут заблокував би чекаут так само, як і 500.
        return QuoteOut(ok=False, reason=Reason.EMPTY_CART)

    try:
        result = quote(
            items,
            settlement_ref=payload.settlement_ref,
            warehouse_ref=payload.warehouse_ref,
            service_type=payload.service_type or ServiceType.WAREHOUSE_WAREHOUSE,
            redelivery=payload.redelivery,
        )
    except Exception:
        log.exception("/delivery/quote: несподівана помилка — віддаємо ok:false, НЕ 500")
        return QuoteOut(ok=False, reason=Reason.NP_ERROR)

    return QuoteOut(
        ok=result.ok,
        reason=result.reason,
        cost=result.cost,
        cost_redelivery=result.cost_redelivery,
        currency=result.currency,
        service_type=result.service_type,
        city_ref=result.city_ref,
        seats_amount=result.seats_amount,
        weight_kg=result.weight_kg,
        volume_m3=result.volume_m3,
        chargeable_weight_kg=result.chargeable_weight_kg,
        cost_declared=result.cost_declared,
        dims_source=result.dims_source,
        cached=result.cached,
    )


__all__ = ["ROUTER_PREFIX", "router"]
