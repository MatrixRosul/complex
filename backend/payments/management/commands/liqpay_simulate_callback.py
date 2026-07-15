"""Симулятор вебхука LiqPay — валідно підписаний callback БЕЗ інтернету.

Навіщо: `server_url` має бути публічним HTTPS, а локально його немає. Це єдиний спосіб
перевірити ідемпотентність, out-of-order і amount-mismatch на живій БД (LIQPAY.md §7).

    uv run python manage.py liqpay_simulate_callback --reference <uuid> --status success
    uv run python manage.py liqpay_simulate_callback --reference <uuid> --status success --amount 1
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.test import Client

from payments.models import Payment
from payments.providers.liqpay import LiqPayProvider


class Command(BaseCommand):
    help = "Сформувати валідно підписаний callback LiqPay і надіслати його у наш вебхук"

    def add_arguments(self, parser):
        parser.add_argument("--reference", required=True, help="Payment.reference (UUID)")
        parser.add_argument("--status", default="success", help="success|failure|reversed|sandbox…")
        parser.add_argument("--amount", default=None, help="Сума (за замовчуванням — з платежу)")
        parser.add_argument("--paytype", default="card")
        parser.add_argument(
            "--url",
            default="/api/v1/payments/liqpay/callback",
            help="Шлях вебхука (після зшивання роутера в config/api.py)",
        )

    def handle(self, *args, **opts):
        payment = Payment.objects.filter(reference=opts["reference"]).first()
        if payment is None:
            raise CommandError(f"Платіж {opts['reference']} не знайдено")

        amount = Decimal(str(opts["amount"])) if opts["amount"] else payment.amount
        provider = LiqPayProvider.from_settings()

        payload = {
            "action": "pay",
            "payment_id": 1234567890,
            "status": opts["status"],
            "version": 3,
            "type": "buy",
            "paytype": opts["paytype"],
            "public_key": provider.client.public_key,
            "acq_id": 414963,
            "order_id": str(payment.reference),
            "liqpay_order_id": f"SIM{payment.pk}",
            "description": f"Замовлення №{payment.order.number} — Complex",
            "sender_card_mask2": "424242*42",
            "sender_card_bank": "pb",
            "amount": float(amount),
            "currency": "UAH",
            "receiver_commission": float(round(amount * Decimal("0.015"), 2)),
            "create_date": 1752400000000,
            "end_date": 1752400060000,
        }
        data = provider.client.encode_data(payload)
        signature = provider.client.make_signature(data)

        self.stdout.write(f"data      = {data[:60]}…")
        self.stdout.write(f"signature = {signature}")
        self.stdout.write(f"payload   = {json.dumps(payload, ensure_ascii=False)[:200]}…")

        form = {"data": data, "signature": signature}
        resp = Client().post(opts["url"], form)

        if resp.status_code == 404:
            # Роутер ще не підключений у config/api.py (фаза «Зшивання») — б'ємо напряму в нього.
            self.stdout.write(
                self.style.WARNING(
                    f"{opts['url']} → 404: роутер ще не змонтований у config/api.py. "
                    "Викликаю payments.api.router напряму."
                )
            )
            from ninja.testing import TestClient

            from payments.api import router

            resp = TestClient(router).post("/liqpay/callback", data=form)

        self.stdout.write(self.style.SUCCESS(f"HTTP {resp.status_code}: {resp.content!r}"))

        payment.refresh_from_db()
        payment.order.refresh_from_db()
        self.stdout.write(
            f"Payment {payment.reference}: {payment.status} | "
            f"Order {payment.order.number}: {payment.order.payment_status}"
        )
