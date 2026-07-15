"""🔴 Звірка: вебхук не дійшов — гроші зняті, замовлення «очікує оплати» назавжди.

Мокається ТІЛЬКИ мережа (`LiqPayClient.api`) — мапінг статусів, guards і проведення
працюють справжні, ті самі, що й у вебхуку.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from django.utils import timezone

from orders.models import Order
from payments.models import Payment
from payments.providers.liqpay import LiqPayClient
from payments.tasks import RECONCILE_MAX_AGE, reconcile_pending_payments

pytestmark = pytest.mark.django_db


def _age(payment: Payment, delta: timedelta) -> None:
    """Змінити created_at (auto_now_add не дає це зробити через save)."""
    Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - delta)


def _liqpay_status(payment: Payment, status: str, **extra) -> dict:
    payload = {
        "status": status,
        "order_id": str(payment.reference),
        "payment_id": 2_100_000_777,
        "amount": float(payment.amount),
        "currency": "UAH",
        "paytype": "card",
        "receiver_commission": 225.0,
        "end_date": 1_752_400_060_000,
    }
    payload.update(extra)
    return payload


class TestReconcileRecovery:
    def test_lost_webhook_is_recovered(self, monkeypatch, payment_factory):
        """Вебхук не прийшов, а гроші зняті. get_status каже success → замовлення оплачене."""
        payment = payment_factory()
        _age(payment, timedelta(minutes=30))

        monkeypatch.setattr(
            LiqPayClient, "api", lambda self, params: _liqpay_status(payment, "success")
        )

        stats = reconcile_pending_payments()

        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.PAID
        assert payment.paid_at is not None
        assert payment.receiver_commission == Decimal("225.00")
        assert payment.last_polled_at is not None
        assert payment.order.payment_status == Order.PaymentStatus.PAID
        assert payment.order.status == Order.Status.CONFIRMED
        assert stats["applied"] == 1

    def test_reconcile_and_webhook_do_not_double_apply(self, monkeypatch, payment_factory):
        """Звірка після вебхука нічого не ламає: та сама функція, та сама ідемпотентність."""
        from payments.providers.base import PaymentStatus
        from payments.services import apply_payment_status

        payment = payment_factory()
        apply_payment_status(
            payment,
            PaymentStatus(
                state=Payment.Status.PAID,
                raw_status="success",
                invoice_id=str(payment.reference),
                amount=payment.amount,
                currency="UAH",
                end_date=1_752_400_060_000,
            ),
            source="webhook",
        )
        payment.refresh_from_db()
        paid_at = payment.paid_at
        _age(payment, timedelta(minutes=30))
        Payment.objects.filter(pk=payment.pk).update(status=Payment.Status.PAID)

        monkeypatch.setattr(
            LiqPayClient, "api", lambda self, params: _liqpay_status(payment, "success")
        )
        reconcile_pending_payments()  # PAID не потрапляє у вибірку взагалі

        payment.refresh_from_db()
        assert payment.paid_at == paid_at

    def test_failure_from_reconcile_marks_failed(self, monkeypatch, payment_factory):
        payment = payment_factory()
        _age(payment, timedelta(hours=2))
        monkeypatch.setattr(
            LiqPayClient,
            "api",
            lambda self, params: _liqpay_status(
                payment, "failure", err_code="limit", err_description="Ліміт"
            ),
        )

        reconcile_pending_payments()

        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.FAILED
        assert payment.order.payment_status == Order.PaymentStatus.FAILED


class TestReconcileWindow:
    def test_young_payment_is_not_polled(self, monkeypatch, payment_factory):
        """Фора вебхуку: платіж молодший 5 хв не смикаємо."""
        payment = payment_factory()
        _age(payment, timedelta(minutes=1))
        called = []
        monkeypatch.setattr(
            LiqPayClient,
            "api",
            lambda self, params: called.append(params) or _liqpay_status(payment, "success"),
        )

        stats = reconcile_pending_payments()

        assert called == []
        assert stats["polled"] == 0
        payment.refresh_from_db()
        assert payment.status == Payment.Status.CREATED

    def test_older_than_48h_expires_and_fails_order(self, monkeypatch, payment_factory):
        payment = payment_factory()
        _age(payment, RECONCILE_MAX_AGE + timedelta(hours=1))
        monkeypatch.setattr(
            LiqPayClient, "api", lambda self, params: _liqpay_status(payment, "success")
        )

        stats = reconcile_pending_payments()

        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert stats["polled"] == 0  # старіший за вікно — не опитуємо
        assert stats["expired"] == 1
        assert payment.status == Payment.Status.EXPIRED
        assert payment.order.payment_status == Order.PaymentStatus.FAILED

    def test_paid_payment_is_never_expired(self, payment_factory):
        payment = payment_factory(status=Payment.Status.PAID)
        _age(payment, RECONCILE_MAX_AGE + timedelta(days=5))

        reconcile_pending_payments()

        payment.refresh_from_db()
        assert payment.status == Payment.Status.PAID


class TestReconcileResilience:
    def test_network_error_does_not_kill_the_run(self, monkeypatch, payment_factory):
        """Один платіж не опитався — інші мають опитатись."""
        broken = payment_factory()
        _age(broken, timedelta(minutes=30))
        ok = payment_factory()
        _age(ok, timedelta(minutes=30))

        def fake_api(self, params):
            order_id = params["order_id"]
            if order_id == str(broken.reference):
                raise httpx.ConnectError("boom")
            return _liqpay_status(ok, "success")

        monkeypatch.setattr(LiqPayClient, "api", fake_api)

        stats = reconcile_pending_payments()

        broken.refresh_from_db()
        ok.refresh_from_db()
        assert broken.status == Payment.Status.CREATED
        assert ok.status == Payment.Status.PAID
        assert stats["errors"] == 1
        assert stats["applied"] == 1

    def test_payment_not_found_is_not_a_failure(self, monkeypatch, payment_factory):
        """Покупець не відкрив чекаут → у LiqPay рахунку немає. Це НЕ «платіж провалився»."""
        payment = payment_factory()
        _age(payment, timedelta(minutes=30))
        monkeypatch.setattr(
            LiqPayClient,
            "api",
            lambda self, params: {"result": "error", "err_code": "payment_not_found"},
        )

        stats = reconcile_pending_payments()

        payment.refresh_from_db()
        assert payment.status == Payment.Status.CREATED  # чекаємо далі, до 48 год
        assert stats["not_found"] == 1
        assert stats["applied"] == 0

    def test_amount_mismatch_from_reconcile_is_refused(self, monkeypatch, payment_factory):
        """Той самий guard, що й у вебхуку — бо функція проведення одна."""
        payment = payment_factory()
        _age(payment, timedelta(minutes=30))
        monkeypatch.setattr(
            LiqPayClient, "api", lambda self, params: _liqpay_status(payment, "success", amount=1.0)
        )

        reconcile_pending_payments()

        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.CREATED
        assert payment.order.payment_status != Order.PaymentStatus.PAID
