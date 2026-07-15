"""POST /payments/liqpay/callback + POST /payments/create.

Роутер тестується напряму через ninja.testing.TestClient — config/api.py чіпає окремий
агент на фазі «Зшивання», тести від нього не залежать.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from ninja.testing import TestClient

from orders.models import Order
from payments.api import router
from payments.models import Payment, PaymentWebhookEvent
from payments.tests.conftest import callback_payload, encode, webhook_form

Result = PaymentWebhookEvent.Result
pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return TestClient(router)


# ---------------------------------------------------------------------------
# ВЕБХУК
# ---------------------------------------------------------------------------
class TestCallbackSignature:
    def test_invalid_signature_is_rejected(self, client, payment_factory):
        """🔴 Перевірка підпису ОБОВ'ЯЗКОВА. Не збігся → 400, платіж не рухається."""
        payment = payment_factory()
        form = webhook_form(callback_payload(payment))
        form["signature"] = "38Px9/7zdpJMFiYkOV3KUnRnMBQ="  # чужий підпис

        resp = client.post("/liqpay/callback", data=form)

        assert resp.status_code == 400
        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.CREATED
        assert payment.order.payment_status != Order.PaymentStatus.PAID

        event = PaymentWebhookEvent.objects.get()
        assert event.result == Result.BAD_SIGNATURE
        assert event.signature_valid is False

    def test_signature_from_wrong_private_key_is_rejected(self, client, payment_factory):
        payment = payment_factory()
        form = webhook_form(callback_payload(payment), private_key="attacker-key")

        resp = client.post("/liqpay/callback", data=form)

        assert resp.status_code == 400
        payment.refresh_from_db()
        assert payment.status == Payment.Status.CREATED

    def test_empty_body_is_rejected(self, client):
        assert client.post("/liqpay/callback", data={}).status_code == 400

    def test_bad_signature_does_not_block_the_real_webhook(self, client, payment_factory):
        """🔴 РЕГРЕС (знайдено e2e-прогоном). Атака «відмова в оплаті» БЕЗ знання ключа:
        хто завгодно POST-ить тіло з гарбидж-підписом → його body_hash займає unique-індекс
        → справжній вебхук LiqPay з тим самим тілом відкидається як «дубль» → гроші зняті,
        замовлення вічно «очікує оплати». Простори ключів мають бути РІЗНІ."""
        payment = payment_factory()
        form = webhook_form(callback_payload(payment))

        poisoned = client.post(
            "/liqpay/callback", data={"data": form["data"], "signature": "garbage"}
        )
        real = client.post("/liqpay/callback", data=form)  # той самий data, ВАЛІДНИЙ підпис

        assert (poisoned.status_code, real.status_code) == (400, 200)
        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.PAID
        assert payment.order.payment_status == Order.PaymentStatus.PAID

    def test_repeated_garbage_is_deduped_within_its_own_namespace(self, client, payment_factory):
        payment = payment_factory()
        form = webhook_form(callback_payload(payment))
        bad = {"data": form["data"], "signature": "garbage"}

        client.post("/liqpay/callback", data=bad)
        client.post("/liqpay/callback", data=bad)

        assert PaymentWebhookEvent.objects.filter(result=Result.BAD_SIGNATURE).count() == 1


class TestCallbackHappyPath:
    def test_success_marks_order_paid(self, client, payment_factory):
        payment = payment_factory()

        resp = client.post("/liqpay/callback", data=webhook_form(callback_payload(payment)))

        assert resp.status_code == 200
        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.PAID
        assert payment.order.payment_status == Order.PaymentStatus.PAID
        assert payment.order.status == Order.Status.CONFIRMED
        assert payment.receiver_commission == Decimal("225.00")
        assert payment.sender_card_mask2 == "424242*42"

        event = PaymentWebhookEvent.objects.get()
        assert event.result == Result.APPLIED
        assert event.processed is True
        assert event.payment_id == payment.pk

    def test_paypart_commission_is_recorded(self, client, payment_factory):
        """receiver_commission — джерело правди по юніт-економіці розстрочки."""
        payment = payment_factory(paytype="paypart")
        payload = callback_payload(
            payment, paytype="paypart", receiver_commission=2400.0, moment_part="false"
        )

        client.post("/liqpay/callback", data=webhook_form(payload))

        payment.refresh_from_db()
        assert payment.paytype == "paypart"
        assert payment.receiver_commission == Decimal("2400.0")
        assert payment.is_moment_part is False


class TestCallbackIdempotency:
    def test_same_webhook_twice_is_applied_once(self, client, payment_factory):
        """🔴 Ключ 1: точний дубль тіла (provider, body_hash)."""
        payment = payment_factory()
        form = webhook_form(callback_payload(payment))

        first = client.post("/liqpay/callback", data=form)
        payment.refresh_from_db()
        paid_at = payment.paid_at

        second = client.post("/liqpay/callback", data=form)

        assert (first.status_code, second.status_code) == (200, 200)
        payment.refresh_from_db()
        assert payment.status == Payment.Status.PAID
        assert payment.paid_at == paid_at  # проведено РІВНО ОДИН раз
        assert PaymentWebhookEvent.objects.count() == 1  # другий не записався взагалі

    def test_retry_with_different_end_date_is_applied_once(self, client, payment_factory):
        """🔴 Ключ 2: (provider, invoice_id, status). У ретраю ІНШЕ тіло → інший body_hash,
        і перший ключ не спрацював би взагалі."""
        payment = payment_factory()

        client.post("/liqpay/callback", data=webhook_form(callback_payload(payment)))
        payment.refresh_from_db()
        paid_at = payment.paid_at

        retry = callback_payload(payment, end_date=1_752_499_999_999)  # інший timestamp
        assert encode(retry) != encode(callback_payload(payment))
        resp = client.post("/liqpay/callback", data=webhook_form(retry))

        assert resp.status_code == 200
        payment.refresh_from_db()
        assert payment.paid_at == paid_at
        assert PaymentWebhookEvent.objects.count() == 1


class TestCallbackAmountGuard:
    def test_underpaid_webhook_is_refused(self, client, payment_factory):
        """🔴 Вебхук із заниженою сумою НЕ проводить замовлення."""
        payment = payment_factory()  # 15 000 грн
        payload = callback_payload(payment, amount=1)

        resp = client.post("/liqpay/callback", data=webhook_form(payload))

        assert resp.status_code == 200  # 200, щоб LiqPay не ретраїв — але гроші НЕ проведено
        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.CREATED
        assert payment.order.payment_status != Order.PaymentStatus.PAID
        assert PaymentWebhookEvent.objects.get().result == Result.AMOUNT_MISMATCH

    def test_overpaid_webhook_is_refused_too(self, client, payment_factory):
        payment = payment_factory()
        client.post("/liqpay/callback", data=webhook_form(callback_payload(payment, amount=99999)))

        payment.refresh_from_db()
        assert payment.status == Payment.Status.CREATED

    def test_amount_mismatch_does_not_block_the_real_webhook(self, client, payment_factory):
        """🔴 РЕГРЕС (знайдено e2e-прогоном): відхилена подія НЕ має спалювати ключ
        ідемпотентності (provider, invoice_id, status). Інакше один вебхук із заниженою
        сумою НАЗАВЖДИ блокував би правильний `success`: гроші зняті, замовлення вічно
        «очікує оплати»."""
        payment = payment_factory()

        bad = client.post(
            "/liqpay/callback", data=webhook_form(callback_payload(payment, amount=1))
        )
        good = client.post("/liqpay/callback", data=webhook_form(callback_payload(payment)))

        assert (bad.status_code, good.status_code) == (200, 200)
        payment.refresh_from_db()
        payment.order.refresh_from_db()
        assert payment.status == Payment.Status.PAID
        assert payment.order.payment_status == Order.PaymentStatus.PAID
        assert PaymentWebhookEvent.objects.get().result == Result.APPLIED

    def test_applied_event_still_blocks_retries(self, client, payment_factory):
        """Зворотний бік: ПРОВЕДЕНА подія ключ спалює — і правильно робить."""
        payment = payment_factory()
        client.post("/liqpay/callback", data=webhook_form(callback_payload(payment)))
        payment.refresh_from_db()
        paid_at = payment.paid_at

        # ретрай success з іншим end_date + запізніла подія з заниженою сумою
        client.post("/liqpay/callback", data=webhook_form(callback_payload(payment, end_date=9)))
        client.post("/liqpay/callback", data=webhook_form(callback_payload(payment, amount=1)))

        payment.refresh_from_db()
        assert payment.paid_at == paid_at
        assert payment.status == Payment.Status.PAID


class TestCallbackUnknownPayment:
    def test_unknown_order_id_returns_200(self, client, payment_factory):
        """Невідомий order_id → 200 (інакше LiqPay довбатиме ретраями) + журнал."""
        payment = payment_factory()
        payload = callback_payload(payment)
        payload["order_id"] = "6f9e26a1-0000-0000-0000-000000000000"

        resp = client.post("/liqpay/callback", data=webhook_form(payload))

        assert resp.status_code == 200
        assert PaymentWebhookEvent.objects.get().result == Result.UNKNOWN_PAYMENT


# ---------------------------------------------------------------------------
# POST /payments/create
# ---------------------------------------------------------------------------
class TestCreateEndpoint:
    def test_returns_checkout_url(self, client, order_factory):
        order = order_factory()

        resp = client.post(
            "/create", json={"order_token": str(order.public_token), "paytype": "card"}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["checkout_url"].startswith("https://www.liqpay.ua/api/3/checkout?data=")
        assert body["action_url"] == "https://www.liqpay.ua/api/3/checkout"
        assert body["data"] and body["signature"]
        assert Decimal(body["amount"]) == order.total
        assert body["installments_available"] is True

        payment = Payment.objects.get(order=order)
        assert body["invoice_id"] == str(payment.reference)
        order.refresh_from_db()
        assert order.payment_status == Order.PaymentStatus.PENDING

    def test_installments_forbidden_for_mixed_cart(self, client, order_factory):
        """🔴 Клієнту не віримо: сервер перевіряє правило сам."""
        order = order_factory(items=[("A", "15000.00", 1, True), ("B", "1000.00", 1, False)])

        resp = client.post(
            "/create", json={"order_token": str(order.public_token), "paytype": "paypart"}
        )

        assert resp.status_code == 400
        assert "частинами" in resp.json()["detail"]
        assert not Payment.objects.filter(order=order).exists()

    def test_unknown_order_returns_404(self, client):
        resp = client.post(
            "/create",
            json={"order_token": "6f9e26a1-0000-0000-0000-000000000000", "paytype": "card"},
        )
        assert resp.status_code == 404

    def test_installments_endpoint_reports_server_decision(self, client, order_factory):
        order = order_factory(items=[("A", "15000.00", 1, True), ("B", "1000.00", 1, False)])

        resp = client.get(f"/installments/{order.public_token}")

        assert resp.status_code == 200
        assert resp.json()["available"] is False
        assert "не всі товари" in resp.json()["reason"]
