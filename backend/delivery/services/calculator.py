"""
Калькулятор вартості доставки НП (INTEGRATIONS §1.7–1.8).

ГОЛОВНИЙ КОНТРАКТ ЦЬОГО МОДУЛЯ: `quote()` НІКОЛИ не кидає виняток назовні.
Будь-яка біда (НП лежить, breaker відкритий, битий ref) повертається як
`QuoteResult(ok=False, reason=...)`, і API віддає це з HTTP 200.

Чому саме так: фронт НЕ МАЄ блокувати кнопку «Оформити» через недоступність
калькулятора. `ok:false` → «Вартість доставки повідомить менеджер», замовлення
створюється з `delivery_cost_estimate = NULL`. Недоступний калькулятор не сміє
зупиняти продажі.

КЕШ — ДВА ШАРИ (INTEGRATIONS §1.8):
  1. Redis, TTL 24 год — гарячий, саме він знімає навантаження з НП.
     🔴 Ключ ОБОВ'ЯЗКОВО містить `cost_declared` (оголошена вартість = ціна товару,
        а вона змінюється 4×/добу) І номер тижня. Ключ без ціни віддавав би застарілу
        вартість доставки НЕСКІНЧЕННО.
  2. Postgres `DeliveryQuote` — ТІЛЬКИ аналітика: пишемо на cache-miss і з семплюванням
     10%. `/delivery/quote` — публічний неавторизований ендпоінт, і патерн
     «публічний запит → INSERT у БД» — вектор забивання диска.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from django.core.cache import cache

from delivery.client import (
    UZHHOROD_CITY_REF,
    NovaPoshtaClient,
    NovaPoshtaError,
    NovaPoshtaUnavailable,
    get_web_client,
)
from delivery.models import DeliveryQuote, DimsSource, NPSettlement, NPWarehouse, ServiceType
from delivery.services.dims import CartDims, cart_dims

log = logging.getLogger(__name__)

__all__ = [
    "QUOTE_TTL",
    "QuoteItem",
    "QuoteResult",
    "Reason",
    "quote",
]

#: TTL гарячого кешу. Тарифи НП не змінюються частіше ніж раз на тиждень, але ціна
#: товару (= оголошена вартість) міняється 4×/добу → вона В КЛЮЧІ, а не в TTL.
QUOTE_TTL = 24 * 60 * 60

#: Частка cache-miss'ів, які осідають у Postgres для аналітики покриття габаритів.
ANALYTICS_SAMPLE_RATE = 0.10


class Reason:
    """Причини `ok: false`. Фронт мапить їх на тексти; сюди нових не додавати мовчки."""

    NP_UNAVAILABLE = "np_unavailable"  # таймаут / обрив / breaker відкритий
    NP_ERROR = "np_error"  # НП відповіла success:false
    UNKNOWN_SETTLEMENT = "unknown_settlement"  # ref немає в НАШОМУ довіднику
    UNKNOWN_WAREHOUSE = "unknown_warehouse"
    EMPTY_CART = "empty_cart"


@dataclass(frozen=True, slots=True)
class QuoteItem:
    product: Any
    qty: int = 1


@dataclass(frozen=True, slots=True)
class QuoteResult:
    ok: bool
    cost: Decimal | None = None
    cost_redelivery: Decimal | None = None
    currency: str = "UAH"
    reason: str | None = None

    service_type: str = ""
    city_ref: str = ""
    seats_amount: int = 0
    weight_kg: Decimal = Decimal("0")
    volume_m3: Decimal = Decimal("0")
    chargeable_weight_kg: Decimal = Decimal("0")
    cost_declared: Decimal = Decimal("0")
    dims_source: str = ""
    cached: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def _week_stamp() -> str:
    """Тиждень у ключі кешу: захист від «тариф змінився, а ми віддаємо старий»."""
    y, w, _ = datetime.now(UTC).isocalendar()
    return f"{y}{w:02d}"


def _cache_key(
    *,
    city_sender: str,
    city_ref: str,
    service_type: str,
    weight_kg: Decimal,
    volume_m3: Decimal,
    cost_declared: Decimal,
    seats: int,
    redelivery: Decimal | None,
) -> str:
    # Числа НОРМАЛІЗОВАНІ до фіксованої точності — інакше 2 і 2.000 дали б різні ключі
    # і кеш ніколи б не влучав.
    # city_sender у ключі — зміна відправника (SiteSettings.np_sender_city_ref) мусить
    # скидати кеш, інакше ціни рахувались би зі старого міста ще добу.
    rd = "0" if redelivery is None else f"{redelivery:.2f}"
    return (
        f"np:quote:{city_sender}:{city_ref}:{service_type}:{weight_kg:.3f}:{volume_m3:.5f}"
        f":{cost_declared:.2f}:{seats}:{rd}:{_week_stamp()}"
    )


def _resolve_recipient(
    *, settlement_ref: str = "", warehouse_ref: str = "", service_type: str = ""
) -> tuple[str, str] | str:
    """→ (city_ref, service_type) або рядок-причина помилки.

    🔴 ServiceType МУСИТЬ відповідати обраній точці (INTEGRATIONS §1.7): обрали поштомат,
       а послали `WarehouseWarehouse` → недорахували ~10 грн на КОЖНОМУ замовленні.
       Тому коли фронт передав `warehouse_ref`, ServiceType беремо З ВІДДІЛЕННЯ, а не
       з того, що надіслав клієнт.
    """
    if warehouse_ref:
        wh = (
            NPWarehouse.objects.filter(ref=warehouse_ref, is_active=True)
            .only("city_ref", "category")
            .first()
        )
        if wh is None:
            return Reason.UNKNOWN_WAREHOUSE
        return wh.city_ref, str(wh.service_type)

    if not settlement_ref:
        return Reason.UNKNOWN_SETTLEMENT

    st = (
        NPSettlement.objects.filter(ref=settlement_ref, is_active=True)
        .only("ref", "delivery_city_ref")
        .first()
    )
    if st is None:
        # Фронт міг прислати CityRef замість SettlementRef — getDocumentPrice толерантний
        # до обох, але наш довідник — ні. Пробуємо як CityRef.
        if NPWarehouse.objects.filter(city_ref=settlement_ref, is_active=True).exists():
            return settlement_ref, service_type or ServiceType.WAREHOUSE_WAREHOUSE
        return Reason.UNKNOWN_SETTLEMENT

    # DeliveryCity — КАНОНІЧНИЙ ref для CityRecipient. Якщо його немає (населений пункт
    # без жодного відділення), getDocumentPrice толерантно приймає і SettlementRef.
    city_ref = st.delivery_city_ref or st.ref
    return city_ref, service_type or ServiceType.WAREHOUSE_WAREHOUSE


def quote(
    items: list[QuoteItem],
    *,
    settlement_ref: str = "",
    warehouse_ref: str = "",
    service_type: str = "",
    redelivery: Decimal | None = None,
    client: NovaPoshtaClient | None = None,
    use_cache: bool = True,
) -> QuoteResult:
    """Порахувати доставку. НІКОЛИ не кидає — завжди повертає QuoteResult."""
    if not items:
        return QuoteResult(ok=False, reason=Reason.EMPTY_CART)

    resolved = _resolve_recipient(
        settlement_ref=settlement_ref, warehouse_ref=warehouse_ref, service_type=service_type
    )
    if isinstance(resolved, str):
        return QuoteResult(ok=False, reason=resolved)
    city_ref, svc = resolved

    dims: CartDims = cart_dims([(i.product, i.qty) for i in items])
    cost_declared = sum((Decimal(i.product.price) * i.qty for i in items), Decimal("0")).quantize(
        Decimal("0.01")
    )

    # Місто відправника — з налаштувань (адмінка), а не з константи. Порожнє поле → Ужгород.
    from core.models import SiteSettings

    city_sender = SiteSettings.get_solo().np_sender_city_ref or UZHHOROD_CITY_REF

    base = QuoteResult(
        ok=False,
        service_type=svc,
        city_ref=city_ref,
        seats_amount=dims.seats_amount,
        weight_kg=dims.weight_kg,
        volume_m3=dims.volume_m3,
        chargeable_weight_kg=dims.chargeable_weight_kg,
        cost_declared=cost_declared,
        dims_source=dims.source,
    )

    key = _cache_key(
        city_sender=city_sender,
        city_ref=city_ref,
        service_type=svc,
        weight_kg=dims.weight_kg,
        volume_m3=dims.volume_m3,
        cost_declared=cost_declared,
        seats=dims.seats_amount,
        redelivery=redelivery,
    )

    if use_cache and (hit := cache.get(key)) is not None:
        return _replace(
            base,
            ok=True,
            cost=Decimal(str(hit["cost"])),
            cost_redelivery=(
                Decimal(str(hit["cost_redelivery"]))
                if hit.get("cost_redelivery") is not None
                else None
            ),
            cached=True,
        )

    np = client or get_web_client()
    try:
        raw = np.get_document_price(
            city_sender=city_sender,
            city_recipient=city_ref,
            weight_kg=dims.weight_kg,
            cost_declared=cost_declared,
            service_type=svc,
            # 🔴 OptionsSeat — ЗАВЖДИ. Без нього НП рахує тільки за фактичною вагою
            #    і магазин недоплачує в 5.5×.
            seats=dims.as_options_seat(),
            redelivery=redelivery,
        )
    except NovaPoshtaUnavailable as exc:
        log.warning("quote: НП недоступна (%s) — віддаємо ok:false, НЕ 500", exc)
        return _replace(base, reason=Reason.NP_UNAVAILABLE)
    except NovaPoshtaError as exc:
        log.warning("quote: НП відмовила: %s (codes=%s)", exc, exc.codes)
        return _replace(base, reason=Reason.NP_ERROR)
    except Exception:
        log.exception("quote: несподівана помилка — віддаємо ok:false")
        return _replace(base, reason=Reason.NP_ERROR)

    cost = Decimal(str(raw.get("Cost") or "0"))
    cost_rd_raw = raw.get("CostRedelivery")
    cost_rd = Decimal(str(cost_rd_raw)) if cost_rd_raw not in (None, "") else None

    if use_cache:
        cache.set(
            key,
            {
                "cost": str(cost),
                "cost_redelivery": None if cost_rd is None else str(cost_rd),
            },
            QUOTE_TTL,
        )
        _sample_to_db(key, base, cost=cost, cost_redelivery=cost_rd, raw=raw)

    return _replace(base, ok=True, cost=cost, cost_redelivery=cost_rd, raw=raw)


def _replace(base: QuoteResult, **kwargs: Any) -> QuoteResult:
    data = {
        f: getattr(base, f)
        for f in (
            "ok",
            "cost",
            "cost_redelivery",
            "currency",
            "reason",
            "service_type",
            "city_ref",
            "seats_amount",
            "weight_kg",
            "volume_m3",
            "chargeable_weight_kg",
            "cost_declared",
            "dims_source",
            "cached",
            "raw",
        )
    }
    data.update(kwargs)
    return QuoteResult(**data)


def _sample_to_db(
    key: str,
    base: QuoteResult,
    *,
    cost: Decimal,
    cost_redelivery: Decimal | None,
    raw: dict[str, Any],
) -> None:
    """10% cache-miss'ів → Postgres. Це АНАЛІТИКА покриття габаритів, не кеш.

    Публічний неавторизований ендпоінт + INSERT на кожен запит = забитий диск, тому
    семплювання, а не «пишемо все».
    """
    if random.random() >= ANALYTICS_SAMPLE_RATE:  # noqa: S311 — семплювання, не крипто
        return
    try:
        DeliveryQuote.objects.create(
            cache_key=key[:140],
            city_ref=base.city_ref,
            service_type=base.service_type,
            weight_kg=base.weight_kg,
            volume_m3=base.volume_m3,
            cost_declared=base.cost_declared,
            cost_delivery=cost,
            cost_redelivery=cost_redelivery,
            dims_source=base.dims_source or DimsSource.DEFAULT,
            raw=raw,
        )
    except Exception:
        log.exception("Не вдалось записати DeliveryQuote (аналітика) — ігноруємо")
