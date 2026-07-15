"""LiqPay (ПриватБанк) — єдиний платіжний провайдер Complex (ADR-025).

Джерела: docs/research/LIQPAY.md, INTEGRATIONS.md §3.

⚠️ Офіційний SDK `liqpay/sdk-python` використовувати ЗАБОРОНЕНО: це Python 2
   (`from urlparse import urljoin`, `iteritems()`), а всередині — `requests.post(..., verify=False)`,
   тобто ВИМКНЕНА перевірка TLS-сертифіката. Свій клієнт — 80 рядків.

ПІДПИС — три місця, де це ламається у всіх (LIQPAY.md §3.1):
    data      = base64( json_utf8(params) )
    signature = base64( sha1_binary( private_key + data + private_key ) )
  1. sha1 береться від БІНАРНОГО digest, а не від hex-рядка;
  2. private_key конкатенується з ОБОХ боків саме `data` (base64-рядка), а не JSON;
  3. порівняння підпису — ТІЛЬКИ hmac.compare_digest (constant-time), інакше timing-oracle.

PAYTYPES — передаємо ЗАВЖДИ явно. Якщо не передати, LiqPay покаже те, що ввімкнено в
кабінеті магазину, і по-товарний тумблер розстрочки перестане працювати взагалі.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import httpx
from django.conf import settings
from django.utils import timezone

from payments.providers.base import (
    Invoice,
    PaymentStatus,
    ProviderError,
    ProviderNotFound,
    redact_request,
    redact_response,
)

if TYPE_CHECKING:  # pragma: no cover
    from payments.models import Payment

log = logging.getLogger(__name__)

CHECKOUT_URL = "https://www.liqpay.ua/api/3/checkout"
API_URL = "https://www.liqpay.ua/api/request"
API_VERSION = 3
WEBHOOK_PATH = "/api/v1/payments/liqpay/callback"
# ⚠️ Звірено з config/urls.py: NinjaAPI змонтований у `path("api/v1/", api.urls)`, а роутер
#    payments — під префіксом "/payments/". Змінить хтось префікс — server_url поїде, і LiqPay
#    почне бити вебхуками в нікуди (гроші зняті, замовлення «очікує оплати»).

#: Способи оплати, доступні завжди.
BASE_PAYTYPES: tuple[str, ...] = ("card", "privat24", "apay", "gpay")
#: 🔴 Розстрочка. `paypart` — комісію 2,3–27,3% платить МАГАЗИН; `moment_part` — покупець.
INSTALLMENT_PAYTYPES: frozenset[str] = frozenset({"paypart", "moment_part"})
#: Що клієнт має право попросити у POST /payments/create.
REQUESTABLE_PAYTYPES: frozenset[str] = frozenset({"card", "paypart", "moment_part", "cash"})


# ---------------------------------------------------------------------------
# Мапа статусів — ОДИН словник, одна точка правди (INTEGRATIONS §3.5)
# ---------------------------------------------------------------------------
# Імпорт Payment.Status тут створив би цикл (models → providers), тому рядки-літерали;
# їх узгодженість із Payment.Status перевіряє тест `test_status_map_matches_model`.
PAID = "paid"
PENDING = "pending"
HELD = "held"
FAILED = "failed"
REFUNDED = "refunded"

LIQPAY_STATUS_MAP: dict[str, str] = {
    # --- фінальні ---
    "success": PAID,
    "wait_compensation": PAID,  # успішний, зарахується в добовій виплаті — товар віддаємо
    "sandbox": PAID,  # 🔴 але тільки якщо sandbox дозволений; у проді → ALERT (services)
    "failure": FAILED,
    "error": FAILED,  # ⚡ некоректні дані = НАШ баг у параметрах → Sentry
    "reversed": REFUNDED,  # кошти повернуто → товар назад на склад
    # --- hold ---
    "hold_wait": HELD,
    # --- гроші вже списані, магазин на перевірці банку ---
    "wait_accept": PENDING,
    "wait_secure": PENDING,
    # --- проміжні ---
    "processing": PENDING,
    "prepared": PENDING,
    "wait_card": PENDING,
    "wait_lc": PENDING,
    "wait_reserve": PENDING,
    "cash_wait": PENDING,
    "invoice_wait": PENDING,
    "wait_qr": PENDING,
    "wait_sender": PENDING,
    # --- покупець ще щось підтверджує ---
    "3ds_verify": PENDING,
    "otp_verify": PENDING,
    "cvv_verify": PENDING,
    "captcha_verify": PENDING,
    "ivr_verify": PENDING,
    "password_verify": PENDING,
    "phone_verify": PENDING,
    "pin_verify": PENDING,
    "sender_verify": PENDING,
    "receiver_verify": PENDING,
    "senderapp_verify": PENDING,
}

#: Підписок у нас немає — події ІГНОРУЄМО (state=None), а не «мапимо кудись».
IGNORED_STATUSES: frozenset[str] = frozenset({"subscribed", "unsubscribed"})
#: Гроші з покупця вже списані, але магазин на верифікації банку.
BANK_REVIEW_STATUSES: frozenset[str] = frozenset({"wait_accept", "wait_secure"})
#: Рахунку у провайдера немає (покупець так і не відкрив чекаут) — це НЕ «платіж провалився».
NOT_FOUND_ERR_CODES: frozenset[str] = frozenset(
    {"payment_not_found", "order_id_empty", "err_payment_not_found"}
)


# ---------------------------------------------------------------------------
# Клієнт
# ---------------------------------------------------------------------------
class LiqPayClient:
    """Тонкий клієнт: підпис + два транспорти (checkout / server-to-server)."""

    def __init__(
        self,
        public_key: str,
        private_key: str,
        *,
        sandbox: bool = False,
        version: int = API_VERSION,
        timeout: float = 15.0,
    ) -> None:
        self.public_key = public_key
        self._private_key = private_key.encode("utf-8")
        self.sandbox = sandbox
        self.version = version
        self._timeout = timeout

    # -- підпис ---------------------------------------------------------------
    @staticmethod
    def encode_data(params: dict[str, Any]) -> str:
        payload = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        return base64.b64encode(payload.encode("utf-8")).decode("ascii")

    @staticmethod
    def decode_data(data: str) -> dict[str, Any]:
        return json.loads(base64.b64decode(data).decode("utf-8"))

    def make_signature(self, data: str) -> str:
        raw = self._private_key + data.encode("utf-8") + self._private_key
        # SHA-1 тут не «слабкий хеш за вибором», а ВИМОГА ПРОТОКОЛУ LiqPay (див. noqa нижче).
        return base64.b64encode(hashlib.sha1(raw).digest()).decode("ascii")  # noqa: S324

    def verify_signature(self, data: str, signature: str) -> bool:
        if not data or not signature:
            return False
        return hmac.compare_digest(self.make_signature(data), signature)

    # -- чекаут ---------------------------------------------------------------
    def build_checkout(self, params: dict[str, Any]) -> dict[str, str]:
        body: dict[str, Any] = {
            "version": self.version,
            "public_key": self.public_key,
            **params,
        }
        if self.sandbox:
            body["sandbox"] = 1
        data = self.encode_data(body)
        signature = self.make_signature(data)
        return {"url": CHECKOUT_URL, "data": data, "signature": signature}

    # -- server-to-server -----------------------------------------------------
    def api(self, params: dict[str, Any]) -> dict[str, Any]:
        body = {"version": self.version, "public_key": self.public_key, **params}
        data = self.encode_data(body)
        resp = httpx.post(
            API_URL,
            data={"data": data, "signature": self.make_signature(data)},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def status(self, order_id: str) -> dict[str, Any]:
        return self.api({"action": "status", "order_id": order_id})

    def refund(self, order_id: str, amount: Decimal) -> dict[str, Any]:
        return self.api({"action": "refund", "order_id": order_id, "amount": float(amount)})


# ---------------------------------------------------------------------------
# Провайдер
# ---------------------------------------------------------------------------
def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass(slots=True)
class LiqPayProvider:
    """Реалізація `PaymentProvider` для LiqPay."""

    client: LiqPayClient
    name: str = "liqpay"
    sandbox: bool = False
    invoice_ttl_hours: int = 24

    # -- конструктор із налаштувань ------------------------------------------
    @classmethod
    def from_settings(cls) -> LiqPayProvider:
        public_key = getattr(settings, "LIQPAY_PUBLIC_KEY", "") or ""
        private_key = getattr(settings, "LIQPAY_PRIVATE_KEY", "") or ""
        sandbox = bool(getattr(settings, "LIQPAY_SANDBOX", True))
        version = int(getattr(settings, "LIQPAY_API_VERSION", API_VERSION))
        ttl = int(getattr(settings, "LIQPAY_INVOICE_TTL_HOURS", 24))
        if not public_key or not private_key:
            log.warning("liqpay: LIQPAY_PUBLIC_KEY/LIQPAY_PRIVATE_KEY порожні")
        return cls(
            client=LiqPayClient(public_key, private_key, sandbox=sandbox, version=version),
            sandbox=sandbox,
            invoice_ttl_hours=ttl,
        )

    # -- paytypes -------------------------------------------------------------
    @staticmethod
    def paytypes_for(requested: str, *, allow_installments: bool) -> list[str]:
        """`paytypes` для чекауту. РОЗСТРОЧКА ДОДАЄТЬСЯ ЛИШЕ ЯКЩО СЕРВЕР ЇЇ ДОЗВОЛИВ.

        `allow_installments` рахує `payments.services.order_allows_installments()` — з
        БД, а не з того, що прислав клієнт.
        """
        if requested == "cash":
            return ["cash"]
        if requested in INSTALLMENT_PAYTYPES:
            if not allow_installments:
                # Захист від «клієнт попросив paypart, а сервер сказав ні»: мовчки
                # деградуємо до звичайної картки. services.create_payment до цього не
                # доходить (кидає InstallmentsNotAllowed), але провайдер не має права
                # покластися на виклик згори.
                log.warning("liqpay: запит paytype=%s без дозволу сервера — деградую", requested)
                return list(BASE_PAYTYPES)
            return [*BASE_PAYTYPES, requested]
        return list(BASE_PAYTYPES)

    # -- створення рахунку ----------------------------------------------------
    def create_invoice(self, payment: Payment) -> Invoice:
        from payments.services import order_allows_installments  # цикл: services → providers

        order = payment.order
        allow = order_allows_installments(order)
        paytypes = self.paytypes_for(payment.paytype or "card", allow_installments=allow)

        expires_at = payment.expires_at or timezone.now() + timedelta(hours=self.invoice_ttl_hours)

        params: dict[str, Any] = {
            "action": "pay",
            "amount": float(payment.amount),  # LiqPay хоче число, не рядок
            "currency": payment.currency,
            "description": f"Замовлення №{order.number} — Complex",
            "order_id": str(payment.reference),  # 🔴 reference спроби, НЕ Order.pk
            "paytypes": ",".join(paytypes),
            "language": "uk",
            "server_url": self.webhook_url(),
            "result_url": self.result_url(order),
            "expired_date": expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        }
        first = order.items.first()
        if first is not None:
            params["product_name"] = first.name[:100]

        checkout = self.client.build_checkout(params)
        url = f"{checkout['url']}?data={checkout['data']}&signature={checkout['signature']}"

        return Invoice(
            invoice_id=str(payment.reference),
            url=url,
            action_url=checkout["url"],
            data=checkout["data"],
            signature=checkout["signature"],
            expires_at=expires_at,
            request=redact_request(params),
        )

    # -- вебхук ---------------------------------------------------------------
    def verify_webhook(self, body: str, signature: str) -> bool:
        return self.client.verify_signature(body, signature)

    def parse_webhook(self, body: str) -> PaymentStatus:
        """`body` — це поле `data` (base64 JSON). Дані беремо ТІЛЬКИ звідси, ніколи з GET."""
        return self._to_status(self.client.decode_data(body))

    # -- звірка ---------------------------------------------------------------
    def get_status(self, invoice_id: str) -> PaymentStatus:
        """🔴 Використовується reconcile'ом, коли вебхук не дійшов."""
        try:
            payload = self.client.status(invoice_id)
        except httpx.HTTPError as exc:
            raise ProviderError(f"liqpay status failed for {invoice_id}: {exc}") from exc

        err_code = str(payload.get("err_code") or payload.get("code") or "")
        if err_code in NOT_FOUND_ERR_CODES:
            # Покупець ніколи не відкривав чекаут → у LiqPay рахунку немає. Це НЕ «failure»:
            # він ще може заплатити протягом 24 год. Такий платіж дочекається EXPIRED (48 год).
            raise ProviderNotFound(f"liqpay: рахунок {invoice_id} не знайдено ({err_code})")
        if not payload.get("status"):
            raise ProviderError(f"liqpay: відповідь без status для {invoice_id}: {payload!r}")

        return self._to_status(payload)

    def refund(self, payment: Payment, amount: Decimal) -> PaymentStatus:
        try:
            payload = self.client.refund(str(payment.reference), amount)
        except httpx.HTTPError as exc:
            raise ProviderError(f"liqpay refund failed for {payment.reference}: {exc}") from exc
        return self._to_status(payload)

    # -- мапінг ---------------------------------------------------------------
    def _to_status(self, payload: dict[str, Any]) -> PaymentStatus:
        raw = str(payload.get("status") or "")

        if raw in IGNORED_STATUSES:
            state: str | None = None
        elif raw in LIQPAY_STATUS_MAP:
            state = LIQPAY_STATUS_MAP[raw]
        else:
            # 🔴 Невідомий статус НЕ має мовчки ставати «оплачено» або «провалено».
            log.warning("liqpay: НЕВІДОМИЙ статус %r → трактую як pending", raw)
            state = PENDING

        end_date = payload.get("end_date") or payload.get("create_date")
        try:
            end_date_int = int(end_date) if end_date is not None else None
        except (TypeError, ValueError):
            end_date_int = None

        return PaymentStatus(
            state=state,
            raw_status=raw,
            invoice_id=str(payload.get("order_id") or ""),
            amount=_to_decimal(payload.get("amount")),
            currency=str(payload.get("currency") or ""),
            paytype=str(payload.get("paytype") or ""),
            provider_payment_id=str(payload.get("payment_id") or ""),
            receiver_commission=_to_decimal(payload.get("receiver_commission")),
            is_moment_part=_parse_bool(payload.get("moment_part")),
            installment_count=_parse_int(payload.get("paypart_count")),
            card_mask=str(payload.get("sender_card_mask2") or "")[:32],
            card_bank=str(payload.get("sender_card_bank") or "")[:64],
            err_code=str(payload.get("err_code") or "")[:64],
            err_description=str(payload.get("err_description") or "")[:500],
            end_date=end_date_int,
            needs_bank_review=raw in BANK_REVIEW_STATUSES,
            is_sandbox=raw == "sandbox",
            payload=redact_response(payload),
        )

    # -- URL-и ----------------------------------------------------------------
    @staticmethod
    def webhook_url() -> str:
        """🔴 server_url МАЄ бути публічним HTTPS (локально — cloudflared/ngrok).

        Береться з `PAYMENTS_PUBLIC_URL` (публічний URL БЕКЕНДА), фолбек — SITE_URL.
        SITE_URL — це фронт (Next.js), тому в проді змінна ОБОВ'ЯЗКОВА (див. звіт).
        """
        base = getattr(settings, "PAYMENTS_PUBLIC_URL", "") or getattr(settings, "SITE_URL", "")
        return f"{base.rstrip('/')}{WEBHOOK_PATH}"

    @staticmethod
    def result_url(order: Any) -> str:
        """⚠️ Це НЕ підтвердження оплати, а лише UX-редірект покупця."""
        base = getattr(settings, "SITE_URL", "").rstrip("/")
        return f"{base}/order/{order.public_token}"


def _parse_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _parse_int(value: Any) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
