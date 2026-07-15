"""LiqPay: підпис, мапа статусів, paytypes, чекаут.

Підпис звіряється з ЕТАЛОННИМ вектором (LIQPAY.md §3.1), порахованим незалежною
реалізацією, а не нашим клієнтом.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal

import pytest

from payments.models import Payment
from payments.providers.base import redact_request, redact_response
from payments.providers.liqpay import (
    CHECKOUT_URL,
    LIQPAY_STATUS_MAP,
    LiqPayClient,
    LiqPayProvider,
)
from payments.tests.conftest import TEST_PRIVATE_KEY, TEST_PUBLIC_KEY

# --- Еталон з офіційного прикладу LiqPay ------------------------------------
GOLDEN_PRIVATE_KEY = "a4825234f4bae72a0be04eafe9e8e2bada209255"
GOLDEN_PARAMS = {
    "version": 3,
    "public_key": "i00000000",
    "action": "pay",
    "amount": 1,
    "currency": "USD",
    "description": "description text",
    "order_id": "order_id_1",
}
GOLDEN_DATA = (
    "eyJ2ZXJzaW9uIjozLCJwdWJsaWNfa2V5IjoiaTAwMDAwMDAwIiwiYWN0aW9uIjoicGF5IiwiYW1vdW50IjoxLCJj"
    "dXJyZW5jeSI6IlVTRCIsImRlc2NyaXB0aW9uIjoiZGVzY3JpcHRpb24gdGV4dCIsIm9yZGVyX2lkIjoib3JkZXJf"
    "aWRfMSJ9"
)
GOLDEN_SIGNATURE = "38Px9/7zdpJMFiYkOV3KUnRnMBQ="


class TestSignature:
    def test_encode_data_matches_golden(self):
        client = LiqPayClient("i00000000", GOLDEN_PRIVATE_KEY)
        assert client.encode_data(GOLDEN_PARAMS) == GOLDEN_DATA

    def test_signature_matches_golden_vector(self):
        """base64(SHA1(private + data + private)) — БІНАРНИЙ digest, ключ з обох боків."""
        client = LiqPayClient("i00000000", GOLDEN_PRIVATE_KEY)
        assert client.make_signature(GOLDEN_DATA) == GOLDEN_SIGNATURE

    def test_signature_is_not_hexdigest(self):
        """Найпоширеніша помилка: sha1().hexdigest() замість .digest()."""
        import hashlib

        client = LiqPayClient("i00000000", GOLDEN_PRIVATE_KEY)
        raw = GOLDEN_PRIVATE_KEY + GOLDEN_DATA + GOLDEN_PRIVATE_KEY
        wrong = base64.b64encode(hashlib.sha1(raw.encode()).hexdigest().encode()).decode()  # noqa: S324
        assert client.make_signature(GOLDEN_DATA) != wrong

    def test_verify_signature_roundtrip(self):
        client = LiqPayClient(TEST_PUBLIC_KEY, TEST_PRIVATE_KEY)
        data = client.encode_data({"action": "status", "order_id": "x"})
        assert client.verify_signature(data, client.make_signature(data)) is True

    @pytest.mark.parametrize("bad", ["", "deadbeef", "38Px9/7zdpJMFiYkOV3KUnRnMBQ="])
    def test_verify_signature_rejects_wrong(self, bad):
        client = LiqPayClient(TEST_PUBLIC_KEY, TEST_PRIVATE_KEY)
        data = client.encode_data({"action": "status", "order_id": "x"})
        assert client.verify_signature(data, bad) is False

    def test_decode_data_roundtrip_cyrillic(self):
        client = LiqPayClient(TEST_PUBLIC_KEY, TEST_PRIVATE_KEY)
        params = {"description": "Замовлення №CMPX-260713-0001 — Complex", "amount": 15000.0}
        assert client.decode_data(client.encode_data(params)) == params


class TestStatusMap:
    def test_map_values_are_real_payment_statuses(self):
        assert set(LIQPAY_STATUS_MAP.values()) <= set(Payment.Status.values)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("success", Payment.Status.PAID),
            ("wait_compensation", Payment.Status.PAID),
            ("sandbox", Payment.Status.PAID),
            ("failure", Payment.Status.FAILED),
            ("error", Payment.Status.FAILED),
            ("reversed", Payment.Status.REFUNDED),
            ("hold_wait", Payment.Status.HELD),
            ("processing", Payment.Status.PENDING),
            ("wait_accept", Payment.Status.PENDING),
            ("3ds_verify", Payment.Status.PENDING),
        ],
    )
    def test_known_statuses(self, raw, expected):
        provider = LiqPayProvider.from_settings()
        status = provider._to_status({"status": raw, "order_id": "x"})
        assert status.state == expected

    def test_subscribed_is_ignored(self):
        provider = LiqPayProvider.from_settings()
        assert provider._to_status({"status": "subscribed"}).state is None
        assert provider._to_status({"status": "unsubscribed"}).state is None

    def test_unknown_status_becomes_pending_not_paid(self, caplog):
        """Новий статус НЕ має мовчки ставати «оплачено» або «провалено»."""
        provider = LiqPayProvider.from_settings()
        status = provider._to_status({"status": "some_new_liqpay_status", "order_id": "x"})
        assert status.state == Payment.Status.PENDING

    def test_wait_accept_sets_bank_review(self):
        provider = LiqPayProvider.from_settings()
        assert provider._to_status({"status": "wait_accept"}).needs_bank_review is True
        assert provider._to_status({"status": "success"}).needs_bank_review is False

    def test_sandbox_flag(self):
        provider = LiqPayProvider.from_settings()
        assert provider._to_status({"status": "sandbox"}).is_sandbox is True
        assert provider._to_status({"status": "success"}).is_sandbox is False

    def test_commission_and_paytype_parsed(self):
        provider = LiqPayProvider.from_settings()
        status = provider._to_status(
            {
                "status": "success",
                "order_id": "abc",
                "amount": 30000,
                "currency": "UAH",
                "paytype": "paypart",
                "receiver_commission": 2400.5,
                "moment_part": "true",
                "payment_id": 12345,
                "end_date": 1752400060000,
            }
        )
        assert status.amount == Decimal("30000")
        assert status.paytype == "paypart"
        assert status.receiver_commission == Decimal("2400.5")
        assert status.is_moment_part is True
        assert status.provider_payment_id == "12345"
        assert status.end_date == 1752400060000


class TestPaytypes:
    def test_installments_absent_when_server_says_no(self):
        assert LiqPayProvider.paytypes_for("paypart", allow_installments=False) == [
            "card",
            "privat24",
            "apay",
            "gpay",
        ]

    def test_installments_present_when_allowed(self):
        assert "paypart" in LiqPayProvider.paytypes_for("paypart", allow_installments=True)
        assert "moment_part" in LiqPayProvider.paytypes_for("moment_part", allow_installments=True)

    def test_card_never_gets_installments(self):
        assert "paypart" not in LiqPayProvider.paytypes_for("card", allow_installments=True)

    def test_cash_is_standalone(self):
        assert LiqPayProvider.paytypes_for("cash", allow_installments=True) == ["cash"]


@pytest.mark.django_db
class TestCheckout:
    def test_create_invoice_signs_and_carries_sandbox(self, payment_factory):
        payment = payment_factory()
        provider = LiqPayProvider.from_settings()

        invoice = provider.create_invoice(payment)

        assert invoice.action_url == CHECKOUT_URL
        assert invoice.url.startswith(f"{CHECKOUT_URL}?data=")
        assert invoice.invoice_id == str(payment.reference)

        body = json.loads(base64.b64decode(invoice.data))
        assert body["sandbox"] == 1  # LIQPAY_SANDBOX=True → без реальних грошей
        assert body["order_id"] == str(payment.reference)  # 🔴 reference, а не Order.pk
        assert body["amount"] == float(payment.amount)
        assert body["version"] == 3
        assert body["server_url"] == "https://api.complex.ua/api/v1/payments/liqpay/callback"
        assert body["result_url"] == f"https://complex.ua/order/{payment.order.public_token}"
        # paytypes передається ЗАВЖДИ явно, інакше тумблер розстрочки не працює
        assert body["paytypes"] == "card,privat24,apay,gpay"

        client = LiqPayClient(TEST_PUBLIC_KEY, TEST_PRIVATE_KEY)
        assert client.verify_signature(invoice.data, invoice.signature)

    def test_create_invoice_adds_paypart_only_for_eligible_cart(
        self, payment_factory, order_factory
    ):
        order = order_factory(items=[("A", "15000.00", 1, True), ("B", "5000.00", 1, True)])
        payment = payment_factory(order=order, paytype="paypart")
        invoice = LiqPayProvider.from_settings().create_invoice(payment)
        assert "paypart" in json.loads(base64.b64decode(invoice.data))["paytypes"]

    def test_create_invoice_drops_paypart_for_mixed_cart(self, payment_factory, order_factory):
        order = order_factory(items=[("A", "15000.00", 1, True), ("B", "5000.00", 1, False)])
        payment = payment_factory(order=order, paytype="paypart")
        invoice = LiqPayProvider.from_settings().create_invoice(payment)
        assert "paypart" not in json.loads(base64.b64decode(invoice.data))["paytypes"]


class TestRedaction:
    def test_secrets_and_pii_never_reach_db(self):
        payload = {
            "status": "success",
            "amount": 100,
            "signature": "secret-sig",
            "data": "base64",
            "card": "4242424242424242",
            "sender_phone": "+380501234567",
            "sender_card_mask2": "424242*42",
        }
        clean = redact_response(payload)
        assert clean["sender_card_mask2"] == "424242*42"
        for forbidden in ("signature", "data", "card", "sender_phone"):
            assert forbidden not in clean

    def test_request_redaction_drops_public_key_and_signature(self):
        clean = redact_request({"public_key": "i1", "signature": "x", "amount": 1, "action": "pay"})
        assert clean == {"amount": 1, "action": "pay"}
