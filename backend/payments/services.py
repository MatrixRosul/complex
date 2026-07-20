"""Платежі — сервісний шар.

🔴 ГОЛОВНЕ ПРАВИЛО МОДУЛЯ (INTEGRATIONS §3.6):
   `apply_payment_status()` — ЄДИНА функція переходу станів. І вебхук (`payments.api`),
   і звірка (`payments.tasks.reconcile_pending_payments`) йдуть ЧЕРЕЗ НЕЇ. Ніякого
   дубльованого мапінгу: це та помилка, після якої стани розходяться і магазин або
   віддає товар без грошей, або тримає гроші без товару.

🔴 СУМА РАХУЄТЬСЯ НА СЕРВЕРІ. З клієнта в цей модуль не приходить ЖОДНОЇ суми: у
   `POST /payments/create` є тільки `order_token` і `paytype`. Сума береться з позицій
   замовлення (`server_side_amount`), і якщо вона розійшлася з `Order.total` — ми не
   «підправляємо тихо», а падаємо: розсинхрон означає баг у checkout, і платити за нього
   покупець не має.

🔴 ОПЛАТА ЧАСТИНАМИ доступна, ЛИШЕ якщо ВСІ позиції її підтримують (+ ліміти LiqPay
   300…300 000 грн, тільки UAH). Перевірка — на СЕРВЕРІ, по снапшоту
   `OrderItem.installment_available` (стан НА МОМЕНТ замовлення, а не сьогоднішній).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from orders.models import Order, OrderStatusHistory
from payments.models import Payment, PaymentWebhookEvent
from payments.providers import PaymentStatus, get_provider

if TYPE_CHECKING:  # pragma: no cover
    from payments.providers.base import PaymentProvider

log = logging.getLogger(__name__)

#: Ліміти сервісу «Оплата частинами» / «Миттєва розстрочка» ПриватБанку (LIQPAY.md §6).
INSTALLMENTS_MIN = Decimal("300")
INSTALLMENTS_MAX = Decimal("300000")

#: Що клієнт має право попросити.
ALLOWED_PAYTYPES: frozenset[str] = frozenset({"card", "paypart", "moment_part", "cash"})
INSTALLMENT_PAYTYPES: frozenset[str] = frozenset({"paypart", "moment_part"})

#: З яких станів дозволений перехід у даний. Реалізується умовним UPDATE (не read-modify-write).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    Payment.Status.PENDING: frozenset({Payment.Status.CREATED, Payment.Status.PENDING}),
    Payment.Status.HELD: frozenset(
        {Payment.Status.CREATED, Payment.Status.PENDING, Payment.Status.HELD}
    ),
    Payment.Status.PAID: frozenset(
        {Payment.Status.CREATED, Payment.Status.PENDING, Payment.Status.HELD}
    ),
    Payment.Status.FAILED: frozenset(
        {Payment.Status.CREATED, Payment.Status.PENDING, Payment.Status.HELD}
    ),
    # Єдиний дозволений перехід із фінального стану: paid → reversed/refunded.
    Payment.Status.REFUNDED: frozenset({Payment.Status.PAID, Payment.Status.HELD}),
    Payment.Status.REVERSED: frozenset({Payment.Status.PAID, Payment.Status.HELD}),
    Payment.Status.EXPIRED: frozenset({Payment.Status.CREATED, Payment.Status.PENDING}),
}

Result = PaymentWebhookEvent.Result


class PaymentError(RuntimeError):
    """Платіж створити не можна (бізнес-правило)."""


class InstallmentsNotAllowed(PaymentError):
    """Розстрочка недоступна для цього кошика/замовлення."""


# ---------------------------------------------------------------------------
# Алерти
# ---------------------------------------------------------------------------
def alert(message: str, **context: object) -> None:
    """Критична подія, яку МАЄ побачити людина.

    Два канали, і жоден не скасовує інший: `log.critical` (його підхоплює Sentry, це
    слід для розбору постфактум) + Telegram (ADR-019 — менеджер і так у телефоні).

    ⚠️ Telegram шлемо ЗАДАЧЕЮ, а не тут-таки. `alert()` викликається з обробки вебхука
       LiqPay і з транзакцій проведення платежу; синхронний HTTP до Telegram тримав би
       відкриту транзакцію й міг би завалити вебхук таймаутом — а вебхук, який не
       відповів 200, LiqPay ретраїть.

    ⚠️ І постановка в чергу теж не має права зламати виклик: якщо брокер лежить,
       алерт лишається в лозі, а платіж проводиться далі.
    """
    log.critical("PAYMENTS ALERT: %s | %s", message, context)

    try:
        from core.alerts import escape
        from core.tasks import send_telegram_alert

        text = f"🔴 <b>Платежі</b>\n\n{escape(message)}"
        if context:
            details = "\n".join(f"{escape(k)}: <code>{escape(v)}</code>" for k, v in context.items())
            text = f"{text}\n\n{details}"

        send_telegram_alert.delay(text)
    except Exception:
        log.exception("Не вдалось поставити Telegram-алерт у чергу")


# ---------------------------------------------------------------------------
# Суми і правила — все рахується на сервері
# ---------------------------------------------------------------------------
def server_side_amount(order: Order) -> Decimal:
    """Сума до сплати — з ПОЗИЦІЙ замовлення, а не з того, що прислав клієнт.

    Позиції — це снапшот цін на момент оформлення (`OrderItem.price`), тому сума не
    «пливе» від зміни каталогу, але й не береться з запиту.
    """
    agg = order.items.aggregate(total=models.Sum("line_total"))
    subtotal: Decimal = agg["total"] or Decimal("0")
    amount = subtotal - (order.discount or Decimal("0"))

    if amount != order.total:
        raise PaymentError(
            f"Сума замовлення {order.number} розійшлася: позиції дають {amount}, "
            f"а Order.total = {order.total}. Платіж не створюємо."
        )
    if amount <= 0:
        raise PaymentError(f"Сума замовлення {order.number} не додатна: {amount}")
    return amount


def order_allows_installments(order: Order) -> bool:
    """Розстрочка доступна, ЛИШЕ якщо КОЖНА позиція її підтримує + сума в межах ліміту.

    🔴 Клієнту не віримо: `paypart`/`moment_part` потрапляють у `paytypes` чекауту тільки
    після цієї перевірки (LIQPAY.md §6, INTEGRATIONS §3.7).
    """
    items = list(order.items.all())
    if not items:
        return False
    total = order.total
    if not (INSTALLMENTS_MIN <= total <= INSTALLMENTS_MAX):
        return False
    return all(i.installment_available for i in items)


def installments_badge(order: Order) -> int | None:
    """Скільки платежів показувати: МІНІМУМ по всіх товарах (найконсервативніше).

    ⚠️ МАРКЕТИНГОВА ОБІЦЯНКА, а не технічне обмеження: у Checkout API немає параметра
    «максимум N платежів», кількість (2–25) обирає ПОКУПЕЦЬ на сторінці LiqPay.
    """
    if not order_allows_installments(order):
        return None
    from core.models import SiteSettings

    default = SiteSettings.get_solo().installment_max_period
    values = [
        (i.product.installment_max_payments or default)
        for i in order.items.select_related("product")
        if i.product is not None and i.product.installment_max_payments
    ]
    return min(values) if values else default


# ---------------------------------------------------------------------------
# Створення платежу
# ---------------------------------------------------------------------------
def create_payment(
    order: Order,
    paytype: str = "card",
    *,
    provider_name: str | None = None,
) -> Payment:
    """Створити спробу оплати і чекаут-посилання.

    З клієнта приходять ТІЛЬКИ `order_token` і `paytype`. Сума, дозволеність розстрочки і
    набір `paytypes` рахуються тут, із БД.
    """
    if paytype not in ALLOWED_PAYTYPES:
        raise PaymentError(f"Невідомий спосіб оплати: {paytype!r}")

    if order.payment_status == Order.PaymentStatus.PAID:
        raise PaymentError(f"Замовлення {order.number} вже оплачене")
    if order.status == Order.Status.CANCELLED:
        raise PaymentError(f"Замовлення {order.number} скасоване")
    if order.payments.filter(status=Payment.Status.PAID).exists():
        raise PaymentError(f"Замовлення {order.number} вже має проведений платіж")

    amount = server_side_amount(order)

    if paytype in INSTALLMENT_PAYTYPES and not order_allows_installments(order):
        raise InstallmentsNotAllowed(
            "Оплата частинами доступна, лише якщо ВСІ товари в кошику її підтримують "
            f"і сума в межах {INSTALLMENTS_MIN}–{INSTALLMENTS_MAX} грн"
        )

    provider: PaymentProvider = get_provider(provider_name)

    with transaction.atomic():
        # Подвійний клік по «Оплатити» не має плодити спроби: живий CREATED-платіж з тією ж
        # сумою і тим самим paytype перевикористовуємо (той самий reference → той самий
        # рахунок у LiqPay).
        existing = (
            order.payments.select_for_update()
            .filter(
                provider=provider.name,
                status=Payment.Status.CREATED,
                paytype=paytype,
                amount=amount,
                expires_at__gt=timezone.now(),
            )
            .exclude(payment_url="")
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            log.info("payments: перевикористовую CREATED-платіж %s", existing.reference)
            # Чекаут детермінований (ті самі reference/amount/expires_at → ті самі data/signature),
            # тому просто перебудовуємо його без мережі.
            existing.checkout = provider.create_invoice(existing)  # type: ignore[attr-defined]
            return existing

        payment = Payment.objects.create(
            order=order,
            provider=provider.name,
            amount=amount,
            currency="UAH",
            paytype=paytype,
            status=Payment.Status.CREATED,
        )

        invoice = provider.create_invoice(payment)

        # ⚠️ `Payment.payment_url` — URLField(max_length=500), а GET-URL чекауту LiqPay
        #    (data+signature у query) — ~700 символів. Тому в БД лягає короткий action_url
        #    (POST-форма — рекомендований спосіб інтеграції, INTEGRATIONS §3.3), а повний
        #    чекаут повертається клієнту і при потребі ДЕТЕРМІНОВАНО перебудовується з Payment.
        #    Якщо власник моделі захоче бачити пряме посилання в адмінці — треба max_length=1000.
        payment.payment_url = invoice.url if len(invoice.url) <= 500 else invoice.action_url
        payment.provider_invoice_id = invoice.invoice_id or ""
        payment.expires_at = invoice.expires_at
        payment.raw_request = invoice.request
        payment.save(
            update_fields=[
                "payment_url",
                "provider_invoice_id",
                "expires_at",
                "raw_request",
                "updated_at",
            ]
        )

        if order.payment_status != Order.PaymentStatus.PENDING:
            Order.objects.filter(pk=order.pk).exclude(
                payment_status=Order.PaymentStatus.PAID
            ).update(payment_status=Order.PaymentStatus.PENDING, updated_at=timezone.now())

    # Чекаут-форма (data/signature) потрібна фронту — вішаємо на об'єкт, у БД не пишемо.
    payment.checkout = invoice  # type: ignore[attr-defined]
    return payment


# ---------------------------------------------------------------------------
# 🔴 ЄДИНА ФУНКЦІЯ ПРОВЕДЕННЯ СТАТУСУ
# ---------------------------------------------------------------------------
def _sandbox_allowed() -> bool:
    return bool(getattr(settings, "LIQPAY_SANDBOX", False) or getattr(settings, "DEBUG", False))


@transaction.atomic
def apply_payment_status(payment: Payment, status: PaymentStatus, source: str) -> str:
    """Провести подію провайдера. ІДЕМПОТЕНТНА. Викликається З ВЕБХУКА І ЗІ ЗВІРКИ.

    `source` — "webhook" | "reconcile" | "manual" (тільки для логів).
    Повертає `PaymentWebhookEvent.Result` — що саме сталося.

    Запобіжники (INTEGRATIONS §3.4) — усі чотири обов'язкові:
      1. ідемпотентність  — умовний UPDATE + фінальність стану (нижче);
      2. звірка суми      — заниження суми НЕ проводиться;
      3. out-of-order     — старіша подія не перетирає новішу (`last_end_date`);
      4. фінальність      — PAID/REFUNDED незворотні (єдиний виняток: paid → reversed).
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    # 0. Подія, яка нас не стосується (підписок немає).
    if status.state is None:
        log.info(
            "payments: ігнорую подію %r для %s (%s)", status.raw_status, payment.reference, source
        )
        return Result.APPLIED

    # 2. ЗВІРКА СУМИ — класична діра: без неї будь-хто, підібравши order_id, провів би
    #    замовлення на 1 грн.
    if status.amount is not None and (
        status.amount != payment.amount or (status.currency and status.currency != payment.currency)
    ):
        alert(
            "LIQPAY AMOUNT MISMATCH — платіж НЕ проведено",
            reference=str(payment.reference),
            expected=f"{payment.amount} {payment.currency}",
            got=f"{status.amount} {status.currency}",
            source=source,
        )
        return Result.AMOUNT_MISMATCH

    # 🔴 sandbox у проді = витік ключа або sandbox:1 у бойовому конфізі. Товар НЕ віддаємо.
    if status.is_sandbox and not _sandbox_allowed():
        alert(
            "SANDBOX-ПЛАТІЖ У ПРОДІ — товар НЕ віддаємо",
            reference=str(payment.reference),
            source=source,
        )
        return Result.SANDBOX_IN_PROD

    # 3. OUT-OF-ORDER: провайдер НЕ гарантує порядок; `pending` після `success` — типовий ретрай.
    if status.end_date and payment.last_end_date and status.end_date < payment.last_end_date:
        log.info("payments: застаріла подія для %s (%s)", payment.reference, source)
        return Result.STALE

    new_state = status.state

    # 4. ФІНАЛЬНІСТЬ: із PAID виходимо тільки в REFUNDED/REVERSED; з REFUNDED — нікуди.
    if payment.status in Payment.FINAL_STATUSES and new_state not in (
        Payment.Status.REFUNDED,
        Payment.Status.REVERSED,
    ):
        log.info(
            "payments: %s уже у фінальному стані %s, подія %r проігнорована (%s)",
            payment.reference,
            payment.status,
            status.raw_status,
            source,
        )
        return Result.FINAL_STATE

    fields: dict[str, object] = {
        "status": new_state,
        "last_end_date": status.end_date or payment.last_end_date,
        "needs_bank_review": status.needs_bank_review,
        "raw_response": status.payload,
        "updated_at": timezone.now(),
    }
    if status.paytype:
        fields["paytype"] = status.paytype
    if status.provider_payment_id:
        fields["provider_invoice_id"] = status.provider_payment_id
    if status.receiver_commission is not None:
        fields["receiver_commission"] = status.receiver_commission
    if status.is_moment_part is not None:
        fields["is_moment_part"] = status.is_moment_part
    if status.installment_count is not None:
        fields["installment_count"] = status.installment_count
    if status.card_mask:
        fields["sender_card_mask2"] = status.card_mask
    if status.card_bank:
        fields["sender_card_bank"] = status.card_bank
    if status.err_code:
        fields["err_code"] = status.err_code
    if status.err_description:
        fields["error_message"] = status.err_description
    if new_state == Payment.Status.PAID and payment.paid_at is None:
        fields["paid_at"] = timezone.now()

    # 1. ІДЕМПОТЕНТНІСТЬ: умовний UPDATE, а не read-modify-write. 0 рядків = подія прийшла
    #    не по порядку або стан уже змінили паралельно → мовчки виходимо.
    allowed_from = ALLOWED_TRANSITIONS.get(new_state, frozenset())
    updated = Payment.objects.filter(pk=payment.pk, status__in=allowed_from).update(**fields)
    if not updated:
        log.info(
            "payments: перехід %s → %s для %s не дозволений (%s)",
            payment.status,
            new_state,
            payment.reference,
            source,
        )
        return Result.STALE

    payment.refresh_from_db()

    if status.raw_status == "error":
        # ⚡ `error` = некоректні дані запиту = НАШ баг у параметрах, а не проблема покупця.
        alert(
            "LIQPAY ERROR — некоректні параметри платежу",
            reference=str(payment.reference),
            err_code=status.err_code,
            err=status.err_description,
        )

    _apply_to_order(payment, new_state, source=source)

    log.info(
        "payments: %s → %s (raw=%s, source=%s, paytype=%s, commission=%s)",
        payment.reference,
        new_state,
        status.raw_status,
        source,
        status.paytype or "—",
        status.receiver_commission,
    )
    return Result.APPLIED


def _apply_to_order(payment: Payment, new_state: str, *, source: str) -> None:
    """Проєкція стану платежу на замовлення. Ідемпотентна (paid_at ставиться один раз)."""
    order = Order.objects.select_for_update().get(pk=payment.order_id)

    if new_state == Payment.Status.PAID:
        if order.payment_status == Order.PaymentStatus.PAID:
            return  # уже проведено — другий раз нічого не робимо

        from_status = order.status
        order.payment_status = Order.PaymentStatus.PAID
        order.paid_at = payment.paid_at or timezone.now()
        if order.status == Order.Status.NEW:
            order.status = Order.Status.CONFIRMED
        order.save(update_fields=["payment_status", "paid_at", "status", "updated_at"])

        if from_status != order.status:
            OrderStatusHistory.objects.create(
                order=order,
                from_status=from_status,
                to_status=order.status,
                comment=f"Оплата підтверджена ({source}, {payment.get_paytype_display()})",
            )
        # Побічні ефекти (лист покупцю, резерв складу, фіскальний чек) — окремі ідемпотентні
        # Celery-таски через transaction.on_commit. Їх власник — orders/fiscal (не цей модуль).
        return

    if new_state in (Payment.Status.REFUNDED, Payment.Status.REVERSED):
        order.payment_status = Order.PaymentStatus.REFUNDED
        order.save(update_fields=["payment_status", "updated_at"])
        alert(
            "Кошти повернуто покупцю — товар треба повернути на склад",
            order=order.number,
            reference=str(payment.reference),
        )
        return

    if new_state in (Payment.Status.FAILED, Payment.Status.EXPIRED):
        if order.payment_status != Order.PaymentStatus.PAID:
            order.payment_status = Order.PaymentStatus.FAILED
            order.save(update_fields=["payment_status", "updated_at"])
        return

    # created / pending / held — замовлення просто чекає.
    if order.payment_status not in (Order.PaymentStatus.PAID, Order.PaymentStatus.REFUNDED):
        order.payment_status = Order.PaymentStatus.PENDING
        order.save(update_fields=["payment_status", "updated_at"])
