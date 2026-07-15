"""services: сума з сервера, правило розстрочки, ЄДИНА ідемпотентна функція проведення."""

from __future__ import annotations

from decimal import Decimal

import pytest

from orders.models import Order, OrderStatusHistory
from payments.models import Payment, PaymentWebhookEvent
from payments.providers.base import PaymentStatus
from payments.services import (
    InstallmentsNotAllowed,
    PaymentError,
    apply_payment_status,
    create_payment,
    order_allows_installments,
    server_side_amount,
)

Result = PaymentWebhookEvent.Result
pytestmark = pytest.mark.django_db


def success(
    payment, *, amount=None, end_date=1_752_400_060_000, raw="success", **kw
) -> PaymentStatus:
    return PaymentStatus(
        state=Payment.Status.PAID if raw in ("success", "sandbox") else Payment.Status.PENDING,
        raw_status=raw,
        invoice_id=str(payment.reference),
        amount=Decimal(str(amount)) if amount is not None else payment.amount,
        currency="UAH",
        paytype="card",
        provider_payment_id="2100000001",
        receiver_commission=Decimal("225.00"),
        end_date=end_date,
        payload={"status": raw},
        **kw,
    )


# ---------------------------------------------------------------------------
# Сума рахується НА СЕРВЕРІ
# ---------------------------------------------------------------------------
class TestServerSideAmount:
    def test_amount_comes_from_order_items(self, order_factory):
        order = order_factory(items=[("A", "12000.00", 2, True), ("B", "3000.50", 1, True)])
        assert server_side_amount(order) == Decimal("27000.50")

    def test_tampered_total_is_refused(self, order_factory):
        """Хтось (баг у checkout або підробка) поклав total=1 грн на кошик з 15 000."""
        order = order_factory()
        Order.objects.filter(pk=order.pk).update(total=Decimal("1.00"))
        order.refresh_from_db()

        with pytest.raises(PaymentError, match="розійшлася"):
            server_side_amount(order)

    def test_create_payment_ignores_client_and_uses_items(self, order_factory):
        order = order_factory(items=[("A", "9999.00", 3, True)])
        payment = create_payment(order, "card")
        assert payment.amount == Decimal("29997.00")


# ---------------------------------------------------------------------------
# 🔴 Оплата частинами — правило з ТЗ
# ---------------------------------------------------------------------------
class TestInstallments:
    def test_allowed_when_all_items_support(self, order_factory):
        order = order_factory(items=[("A", "15000.00", 1, True), ("B", "1000.00", 2, True)])
        assert order_allows_installments(order) is True

    def test_forbidden_when_any_item_does_not_support(self, order_factory):
        order = order_factory(items=[("A", "15000.00", 1, True), ("B", "1000.00", 1, False)])
        assert order_allows_installments(order) is False

    def test_create_payment_rejects_paypart_for_mixed_cart(self, order_factory):
        order = order_factory(items=[("A", "15000.00", 1, True), ("B", "1000.00", 1, False)])
        with pytest.raises(InstallmentsNotAllowed):
            create_payment(order, "paypart")
        assert not Payment.objects.filter(order=order).exists()

    def test_create_payment_rejects_moment_part_for_mixed_cart(self, order_factory):
        order = order_factory(items=[("A", "15000.00", 1, True), ("B", "1000.00", 1, False)])
        with pytest.raises(InstallmentsNotAllowed):
            create_payment(order, "moment_part")

    def test_below_liqpay_minimum_300(self, order_factory):
        order = order_factory(items=[("A", "299.00", 1, True)])
        assert order_allows_installments(order) is False

    def test_above_liqpay_maximum_300000(self, order_factory):
        order = order_factory(items=[("A", "300001.00", 1, True)])
        assert order_allows_installments(order) is False

    def test_allowed_cart_creates_payment(self, order_factory):
        order = order_factory(items=[("A", "15000.00", 1, True)])
        payment = create_payment(order, "paypart")
        assert payment.paytype == "paypart"
        assert payment.status == Payment.Status.CREATED


# ---------------------------------------------------------------------------
# create_payment — інші інваріанти
# ---------------------------------------------------------------------------
class TestCreatePayment:
    def test_unknown_paytype_refused(self, order_factory):
        with pytest.raises(PaymentError):
            create_payment(order_factory(), "bitcoin")

    def test_paid_order_cannot_be_paid_twice(self, order_factory):
        order = order_factory(payment_status=Order.PaymentStatus.PAID)
        with pytest.raises(PaymentError, match="вже оплачене"):
            create_payment(order, "card")

    def test_double_click_reuses_created_payment(self, order_factory):
        order = order_factory()
        first = create_payment(order, "card")
        second = create_payment(order, "card")
        assert first.pk == second.pk
        assert Payment.objects.filter(order=order).count() == 1

    def test_payment_carries_checkout_and_reference(self, order_factory):
        payment = create_payment(order_factory(), "card")
        # Повний GET-чекаут (~700 симв.) віддається клієнту; у БД — короткий action_url,
        # бо Payment.payment_url = URLField(500).
        assert payment.checkout.url.startswith("https://www.liqpay.ua/api/3/checkout?data=")
        assert payment.payment_url == "https://www.liqpay.ua/api/3/checkout"
        assert payment.provider_invoice_id == str(payment.reference)
        assert payment.expires_at is not None
        assert "public_key" not in payment.raw_request  # редакція

    def test_reused_payment_rebuilds_identical_checkout(self, order_factory):
        order = order_factory()
        first = create_payment(order, "card")
        second = create_payment(order, "card")
        assert second.checkout.data == first.checkout.data
        assert second.checkout.signature == first.checkout.signature


# ---------------------------------------------------------------------------
# 🔴 apply_payment_status — ЄДИНА функція проведення, ідемпотентна
# ---------------------------------------------------------------------------
class TestApplyPaymentStatus:
    def test_success_marks_paid_and_confirms_order(self, payment_factory):
        payment = payment_factory()

        assert apply_payment_status(payment, success(payment), source="webhook") == Result.APPLIED

        payment.refresh_from_db()
        order = payment.order
        order.refresh_from_db()
        assert payment.status == Payment.Status.PAID
        assert payment.paid_at is not None
        assert payment.receiver_commission == Decimal("225.00")
        assert order.payment_status == Order.PaymentStatus.PAID
        assert order.status == Order.Status.CONFIRMED
        assert OrderStatusHistory.objects.filter(order=order).count() == 1

    def test_same_event_twice_applies_once(self, payment_factory):
        """🔴 Ідемпотентність на рівні сервісу (у API поверх неї ще два unique-ключі)."""
        payment = payment_factory()
        apply_payment_status(payment, success(payment), source="webhook")
        payment.refresh_from_db()
        paid_at = payment.paid_at

        result = apply_payment_status(payment, success(payment), source="webhook")

        assert result == Result.FINAL_STATE
        payment.refresh_from_db()
        assert payment.paid_at == paid_at
        assert OrderStatusHistory.objects.filter(order=payment.order).count() == 1

    def test_underpaid_amount_is_refused(self, payment_factory):
        """🔴 Без цієї перевірки будь-хто, підібравши order_id, провів би замовлення на 1 грн."""
        payment = payment_factory()

        result = apply_payment_status(payment, success(payment, amount="1.00"), source="webhook")

        assert result == Result.AMOUNT_MISMATCH
        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.CREATED
        assert payment.order.payment_status != Order.PaymentStatus.PAID

    def test_currency_mismatch_is_refused(self, payment_factory):
        payment = payment_factory()
        status = PaymentStatus(
            state=Payment.Status.PAID,
            raw_status="success",
            invoice_id=str(payment.reference),
            amount=payment.amount,
            currency="USD",
        )
        assert apply_payment_status(payment, status, source="webhook") == Result.AMOUNT_MISMATCH

    def test_out_of_order_pending_does_not_downgrade_paid(self, payment_factory):
        """LiqPay не гарантує порядок: `pending` після `success` — типовий ретрай."""
        payment = payment_factory()
        apply_payment_status(payment, success(payment, end_date=200), source="webhook")

        stale = PaymentStatus(
            state=Payment.Status.PENDING,
            raw_status="processing",
            invoice_id=str(payment.reference),
            amount=payment.amount,
            currency="UAH",
            end_date=100,  # старіша подія
        )
        assert apply_payment_status(payment, stale, source="webhook") == Result.STALE

        payment.refresh_from_db()
        assert payment.status == Payment.Status.PAID

    def test_reversed_after_paid_is_the_only_allowed_exit(self, payment_factory):
        payment = payment_factory()
        apply_payment_status(payment, success(payment, end_date=100), source="webhook")

        reversal = PaymentStatus(
            state=Payment.Status.REFUNDED,
            raw_status="reversed",
            invoice_id=str(payment.reference),
            amount=payment.amount,
            currency="UAH",
            end_date=300,
        )
        assert apply_payment_status(payment, reversal, source="webhook") == Result.APPLIED

        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.REFUNDED
        assert payment.order.payment_status == Order.PaymentStatus.REFUNDED

    def test_failure_keeps_order_unpaid(self, payment_factory):
        payment = payment_factory()
        status = PaymentStatus(
            state=Payment.Status.FAILED,
            raw_status="failure",
            invoice_id=str(payment.reference),
            amount=payment.amount,
            currency="UAH",
            err_code="limit",
            err_description="Перевищено ліміт",
        )
        assert apply_payment_status(payment, status, source="webhook") == Result.APPLIED

        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.FAILED
        assert payment.err_code == "limit"
        assert payment.order.payment_status == Order.PaymentStatus.FAILED

    def test_wait_accept_keeps_pending_and_flags_bank_review(self, payment_factory):
        payment = payment_factory()
        status = PaymentStatus(
            state=Payment.Status.PENDING,
            raw_status="wait_accept",
            invoice_id=str(payment.reference),
            amount=payment.amount,
            currency="UAH",
            needs_bank_review=True,
        )
        apply_payment_status(payment, status, source="webhook")

        payment.refresh_from_db()
        assert payment.status == Payment.Status.PENDING
        assert payment.needs_bank_review is True

    def test_subscribed_event_is_ignored(self, payment_factory):
        payment = payment_factory()
        status = PaymentStatus(
            state=None, raw_status="subscribed", invoice_id=str(payment.reference)
        )

        assert apply_payment_status(payment, status, source="webhook") == Result.APPLIED
        payment.refresh_from_db()
        assert payment.status == Payment.Status.CREATED

    def test_sandbox_in_prod_does_not_release_goods(self, payment_factory, settings):
        """🔴 sandbox у проді = витік ключа. Товар НЕ віддаємо."""
        settings.LIQPAY_SANDBOX = False
        settings.DEBUG = False
        payment = payment_factory()
        status = PaymentStatus(
            state=Payment.Status.PAID,
            raw_status="sandbox",
            invoice_id=str(payment.reference),
            amount=payment.amount,
            currency="UAH",
            is_sandbox=True,
        )

        assert apply_payment_status(payment, status, source="webhook") == Result.SANDBOX_IN_PROD

        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.CREATED
        assert payment.order.payment_status != Order.PaymentStatus.PAID

    def test_sandbox_is_paid_in_sandbox_mode(self, payment_factory):
        payment = payment_factory()
        status = PaymentStatus(
            state=Payment.Status.PAID,
            raw_status="sandbox",
            invoice_id=str(payment.reference),
            amount=payment.amount,
            currency="UAH",
            is_sandbox=True,
        )
        assert apply_payment_status(payment, status, source="webhook") == Result.APPLIED
        payment.refresh_from_db()
        assert payment.status == Payment.Status.PAID
