"""Платежі — HTTP-шар (Django Ninja).

Роутер підключається у `config/api.py` окремим агентом на фазі «Зшивання»:

    api.add_router("/payments/", "payments.api.router")

Ендпоінти:
    POST /api/v1/payments/create            {order_token, paytype} → {checkout_url, …}
    POST /api/v1/payments/liqpay/callback   вебхук LiqPay (data + signature, form-urlencoded)
    GET  /api/v1/payments/installments/{order_token}

🔴 ВЕБХУК. Чотири запобіжники (INTEGRATIONS §3.4):
   1. ПІДПИС — обов'язковий, constant-time. Не збігся → 400 (єдиний випадок НЕ-200).
   2. ІДЕМПОТЕНТНІСТЬ — по `PaymentWebhookEvent`, ДВА unique-ключі:
        (provider, body_hash)             — точний дубль тіла;
        (provider, invoice_id, status)    — 🔴 і ретрай з іншим `end_date` у тілі:
                                            body_hash у нього ІНШИЙ, перший ключ не спрацює.
   3. СУМА — звіряється в `apply_payment_status` (заниження не проводиться).
   4. OUT-OF-ORDER / фінальність — там само.
   Усе, крім поганого підпису, повертає 200: інакше LiqPay довбатиме ретраями.
"""

from __future__ import annotations

import hashlib
import logging
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.http import HttpResponse
from ninja import Form, Router, Schema
from ninja.responses import Status
from pydantic import Field

from orders.models import Order
from payments.models import Payment, PaymentWebhookEvent
from payments.providers import get_provider
from payments.services import (
    InstallmentsNotAllowed,
    PaymentError,
    alert,
    apply_payment_status,
    installments_badge,
    order_allows_installments,
)

log = logging.getLogger(__name__)

router = Router(tags=["payments"])

Result = PaymentWebhookEvent.Result


# ---------------------------------------------------------------------------
# Схеми
# ---------------------------------------------------------------------------
class CreatePaymentIn(Schema):
    """🔴 Жодних сум і жодних цін з клієнта. Тільки замовлення і бажаний спосіб оплати."""

    order_token: UUID = Field(..., description="Order.public_token (НЕ номер замовлення)")
    paytype: str = Field("card", description="card | paypart | moment_part | cash")


class CheckoutOut(Schema):
    checkout_url: str = Field(..., description="GET-редірект покупця на сторінку оплати")
    action_url: str = Field(..., description="URL для POST-форми (redirect-інтеграція)")
    data: str = Field(..., description="base64(json) — поле 'data' форми LiqPay")
    signature: str = Field(..., description="підпис — поле 'signature' форми LiqPay")
    invoice_id: str
    amount: Decimal
    currency: str
    paytype: str
    installments_available: bool
    installments_max_payments: int | None


class ErrorOut(Schema):
    detail: str


class InstallmentsOut(Schema):
    available: bool
    max_payments: int | None
    reason: str = ""


# ---------------------------------------------------------------------------
# POST /payments/create
# ---------------------------------------------------------------------------
@router.post(
    "/create",
    response={200: CheckoutOut, 400: ErrorOut, 404: ErrorOut},
    auth=None,
    url_name="payments_create",
    summary="Створити платіж і отримати посилання на чекаут",
)
def create_payment_endpoint(request, payload: CreatePaymentIn):
    from payments.services import create_payment

    order = Order.objects.filter(public_token=payload.order_token).first()
    if order is None:
        return Status(404, {"detail": "Замовлення не знайдено"})

    try:
        payment = create_payment(order, payload.paytype)
    except InstallmentsNotAllowed as exc:
        return Status(400, {"detail": str(exc)})
    except PaymentError as exc:
        return Status(400, {"detail": str(exc)})

    invoice = payment.checkout  # створений або детерміновано перебудований у create_payment

    return Status(
        200,
        {
            "checkout_url": invoice.url,
            "action_url": invoice.action_url,
            "data": invoice.data,
            "signature": invoice.signature,
            "invoice_id": invoice.invoice_id,
            "amount": payment.amount,
            "currency": payment.currency,
            "paytype": payment.paytype,
            "installments_available": order_allows_installments(order),
            "installments_max_payments": installments_badge(order),
        },
    )


# ---------------------------------------------------------------------------
# GET /payments/installments/{order_token} — що показувати на чекауті
# ---------------------------------------------------------------------------
@router.get(
    "/installments/{order_token}",
    response={200: InstallmentsOut, 404: ErrorOut},
    auth=None,
    summary="Чи доступна оплата частинами для цього замовлення (рахує СЕРВЕР)",
)
def installments_endpoint(request, order_token: UUID):
    order = Order.objects.filter(public_token=order_token).first()
    if order is None:
        return Status(404, {"detail": "Замовлення не знайдено"})

    available = order_allows_installments(order)
    reason = ""
    if not available:
        if order.items.filter(installment_available=False).exists():
            reason = "не всі товари в замовленні підтримують оплату частинами"
        else:
            reason = "сума замовлення поза межами 300–300 000 грн"

    return Status(
        200,
        {
            "available": available,
            "max_payments": installments_badge(order),
            "reason": reason,
        },
    )


# ---------------------------------------------------------------------------
# POST /payments/liqpay/callback — вебхук
# ---------------------------------------------------------------------------
@router.post(
    "/liqpay/callback",
    auth=None,
    url_name="liqpay_callback",
    summary="Вебхук LiqPay (server_url)",
    include_in_schema=False,
)
def liqpay_callback(request, data: str = Form(""), signature: str = Form("")):
    provider = get_provider("liqpay")

    # (1) ПІДПИС — до всього іншого. Не збігся → 400 і в лог (можлива атака).
    if not data or not provider.verify_webhook(data, signature):
        log.warning(
            "liqpay: НЕВАЛІДНИЙ ПІДПИС вебхука (ip=%s)",
            request.META.get("REMOTE_ADDR", "?"),
        )
        _record_event(
            body=data,
            signature=signature,
            payload={},
            invoice_id="",
            raw_status="",
            signature_valid=False,
            payment=None,
            result=Result.BAD_SIGNATURE,
        )
        return HttpResponse("bad signature", status=400)

    # Дані беремо ТІЛЬКИ з розшифрованого `data`, ніколи з query/GET.
    status = provider.parse_webhook(data)
    payment = (
        Payment.objects.filter(reference=status.invoice_id).first() if status.invoice_id else None
    )

    # (2) ІДЕМПОТЕНТНІСТЬ — два unique-ключі в БД.
    event, created = _record_event(
        body=data,
        signature=signature,
        payload=status.payload,
        invoice_id=status.invoice_id,
        raw_status=status.raw_status,
        signature_valid=True,
        payment=payment,
        result="",
    )
    if not created:
        log.info("liqpay: дубль вебхука %s/%s — пропускаю", status.invoice_id, status.raw_status)
        return HttpResponse("ok")

    if payment is None:
        log.error("liqpay: невідомий order_id %s", status.invoice_id)
        _close_event(event, Result.UNKNOWN_PAYMENT)
        return HttpResponse("ok")  # 200 — щоб LiqPay не довбав ретраями

    try:
        # (3)(4) звірка суми, out-of-order і фінальність — усередині ЄДИНОЇ функції.
        result = apply_payment_status(payment, status, source="webhook")
    except Exception as exc:  # вебхук не має падати 500 у провайдера
        log.exception("liqpay: помилка обробки вебхука %s", status.invoice_id)
        alert("Помилка обробки вебхука LiqPay", reference=status.invoice_id, error=str(exc))
        _close_event(event, Result.ERROR, error=str(exc))
        return HttpResponse("ok")

    _close_event(event, result)
    return HttpResponse("ok")


# ---------------------------------------------------------------------------
# Журнал вебхуків
# ---------------------------------------------------------------------------
#: 🔴 Результати, які НЕ «спалюють» ключ ідемпотентності (provider, invoice_id, status).
#   Подія з таким результатом НЕ була проведена, отже наступний вебхук з тим самим статусом
#   (LiqPay кладе в тіло `end_date` → тіло інше, body_hash інший) МАЄ бути оброблений.
#   Без цього одна-єдина подія з заниженою сумою НАЗАВЖДИ блокувала б правильний `success`:
#   гроші зняті, замовлення вічно «очікує оплати». (Знайдено e2e-прогоном, не юніт-тестами.)
RETRIABLE_RESULTS: frozenset[str] = frozenset(
    {Result.AMOUNT_MISMATCH, Result.ERROR, Result.UNKNOWN_PAYMENT}
)


#: Скільки байтів непідписаного тіла лишаємо для форензики (решта — сміття з інтернету).
UNVERIFIED_BODY_LIMIT = 4096


def _body_hash(body: str, *, signature_valid: bool) -> str:
    """🔴 Ключі ВАЛІДНИХ і НЕВАЛІДНИХ вебхуків живуть у РІЗНИХ просторах імен.

    Інакше — безкоштовна атака «відмова в оплаті», яка НЕ потребує знання ключа:
    хто завгодно POST-ить тіло з ГАРБИДЖ-підписом → ми пишемо його body_hash у той самий
    unique-індекс → справжній вебхук LiqPay з ТИМ САМИМ тілом відкидається як «дубль»
    → гроші з покупця зняті, замовлення ВІЧНО «очікує оплати».
    (Знайдено e2e-прогоном; юніт-тести цього не бачили — регрес у test_webhook.py.)
    """
    prefix = "" if signature_valid else "invalid:"
    return hashlib.sha256((prefix + body).encode("utf-8")).hexdigest()


def _record_event(
    *,
    body: str,
    signature: str,
    payload: dict,
    invoice_id: str,
    raw_status: str,
    signature_valid: bool,
    payment: Payment | None,
    result: str,
) -> tuple[PaymentWebhookEvent | None, bool]:
    """Записати вебхук. `created=False` = такий уже оброблено (ідемпотентність).

    Обидва unique-ключі ловляться тут, у ВЛАСНІЙ транзакції: IntegrityError інакше
    зламав би зовнішню.
    """
    body_hash = _body_hash(body, signature_valid=signature_valid)
    if not signature_valid:
        body = body[:UNVERIFIED_BODY_LIMIT]
    try:
        with transaction.atomic():
            event = PaymentWebhookEvent.objects.create(
                provider=Payment.Provider.LIQPAY,
                payment=payment,
                invoice_id=invoice_id[:120],
                status=raw_status[:20],
                body_hash=body_hash,
                raw_body=body,
                signature=signature[:128],
                payload=payload,
                signature_valid=signature_valid,
                result=result,
                processed=bool(result),
            )
        return event, True
    except IntegrityError:
        pass

    events = PaymentWebhookEvent.objects.filter(provider=Payment.Provider.LIQPAY)

    # КЛЮЧ 1: точний дубль тіла — обробляти нічого.
    if events.filter(body_hash=body_hash).exists():
        return None, False

    # КЛЮЧ 2: (provider, invoice_id, status). Тіло інше (інший `end_date`) — це або ретрай
    # уже ПРОВЕДЕНОЇ події, або нова спроба після НЕпроведеної.
    existing = events.filter(invoice_id=invoice_id[:120], status=raw_status[:20]).first()
    if existing is None:
        return None, False  # гонка: паралельний воркер щойно вставив рядок
    if existing.result not in RETRIABLE_RESULTS:
        return None, False  # подію вже проведено → справжній дубль

    # Попередня подія з цим ключем НЕ була проведена (напр. amount_mismatch) — вона не має
    # назавжди блокувати правильний вебхук. Перезаписуємо рядок і обробляємо заново.
    log.warning(
        "liqpay: %s/%s — попередня подія була %s, обробляю новий вебхук",
        invoice_id,
        raw_status,
        existing.result,
    )
    existing.body_hash = body_hash
    existing.raw_body = body
    existing.signature = signature[:128]
    existing.payload = payload
    existing.payment = payment or existing.payment
    existing.signature_valid = signature_valid
    existing.result = result
    existing.error = ""
    existing.processed = bool(result)
    existing.processed_at = None
    existing.save(
        update_fields=[
            "body_hash",
            "raw_body",
            "signature",
            "payload",
            "payment",
            "signature_valid",
            "result",
            "error",
            "processed",
            "processed_at",
        ]
    )
    return existing, True


def _close_event(event: PaymentWebhookEvent | None, result: str, error: str = "") -> None:
    if event is None:
        return
    from django.utils import timezone

    event.result = result
    event.error = error
    event.processed = True
    event.processed_at = timezone.now()
    event.save(update_fields=["result", "error", "processed", "processed_at"])
