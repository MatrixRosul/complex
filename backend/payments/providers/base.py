"""Абстракція платіжного провайдера (INTEGRATIONS §3.1, ADR-015).

Реалізація на старті рівно одна — LiqPay. Абстракція існує не «на всяк випадок»,
а щоб замінити провайдера (або додати другий контур — накладений платіж, Checkbox)
БЕЗ переписування `orders` і `payments.services`.

Контракт (4 методи, які використовує решта системи):

    create_invoice(payment)          -> Invoice        # чекаут: {url, invoice_id, ...}
    verify_webhook(body, signature)  -> bool           # підпис, constant-time
    parse_webhook(body)              -> PaymentStatus  # сире тіло → наш нормалізований стан
    get_status(invoice_id)           -> PaymentStatus  # 🔴 ЗВІРКА (reconcile) — критично

🔴 Чому `create_invoice` приймає **Payment**, а не Order (свідомий відступ від ТЗ):
   LiqPay прив'язує статус і рефанд до `order_id`, а `order_id` у нас — це
   `Payment.reference` (UUID КОЖНОЇ спроби), бо одне Order → багато Payment
   (INTEGRATIONS §3.3). З самого Order провайдер не може знати, ДЛЯ ЯКОЇ спроби
   робиться рахунок, — це була б гонка «повторна оплата скасованого замовлення».
   Order завжди доступний як `payment.order`.

🔴 `get_status()` — не «додатковий комфорт», а умова існування магазину: вебхук може
   не дійти (бекенд лежав під час деплою, Caddy віддав 502, провайдер вичерпав ретраї).
   Без нього гроші зняті, а замовлення ВІЧНО «очікує оплати» (payments.tasks).

🔴 РЕДАКЦІЯ сирих payload'ів (`redact_*`) — обов'язкова перед записом у БД: ключі,
   підписи і PII осідають у бекапах назавжди.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from payments.models import Payment

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Помилки
# ---------------------------------------------------------------------------
class ProviderError(RuntimeError):
    """Провайдер не відповів / відповів помилкою. Не фейлить платіж сам по собі."""


class ProviderNotFound(ProviderError):
    """Провайдер не знає такого рахунку (напр. покупець так і не відкрив чекаут)."""


class SignatureError(ProviderError):
    """Підпис не збігся. Можлива атака — у лог і 400."""


# ---------------------------------------------------------------------------
# Редакція сирих payload'ів (ALLOWLIST, а не blocklist)
# ---------------------------------------------------------------------------
#: Поля відповіді/вебхука, які МОЖНА зберігати. Усе інше не потрапляє в БД.
RESPONSE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "action",
        "agent_commission",
        "amount",
        "amount_credit",
        "amount_debit",
        "commission_credit",
        "commission_debit",
        "completion_date",
        "create_date",
        "currency",
        "currency_credit",
        "currency_debit",
        "description",
        "end_date",
        "err_code",
        "err_description",
        "err_erc",
        "info",
        "is_3ds",
        "liqpay_order_id",
        "moment_part",
        "mpi_eci",
        "order_id",
        "payment_id",
        "paytype",
        "public_key",
        "receiver_commission",
        "rrn_credit",
        "rrn_debit",
        "sender_bonus",
        "sender_card_bank",
        "sender_card_country",
        "sender_card_mask2",
        "sender_card_type",
        "sender_commission",
        "status",
        "transaction_id",
        "type",
        "version",
        "result",
    }
)
#: Явні винятки з allowlist — PII і секрети. Перемагають allowlist.
RESPONSE_DENYLIST: frozenset[str] = frozenset(
    {
        "card",
        "card_cvv",
        "card_exp_month",
        "card_exp_year",
        "customer",
        "data",
        "ip",
        "private_key",
        "sender_first_name",
        "sender_last_name",
        "sender_phone",
        "signature",
    }
)
#: Поля запиту, які МОЖНА зберігати (public_key і підпис — ні).
REQUEST_ALLOWLIST: frozenset[str] = frozenset(
    {
        "action",
        "amount",
        "currency",
        "description",
        "expired_date",
        "language",
        "order_id",
        "paytypes",
        "product_category",
        "product_name",
        "result_url",
        "sandbox",
        "server_url",
        "version",
    }
)


def _redact(payload: Mapping[str, Any], allowlist: frozenset[str]) -> dict[str, Any]:
    return {
        k: v
        for k, v in payload.items()
        if k in allowlist and k not in RESPONSE_DENYLIST and not isinstance(v, (bytes, bytearray))
    }


def redact_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Відповідь/вебхук провайдера → те, що дозволено класти в `Payment.raw_response`."""
    return _redact(payload, RESPONSE_ALLOWLIST)


def redact_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Запит до провайдера → те, що дозволено класти в `Payment.raw_request`."""
    return _redact(payload, REQUEST_ALLOWLIST)


# ---------------------------------------------------------------------------
# Значення, якими провайдер спілкується з рештою системи
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Invoice:
    """Результат `create_invoice()` — усе, що треба фронту, щоб відправити покупця платити."""

    invoice_id: str
    """`order_id` у провайдера. Для LiqPay = str(Payment.reference)."""

    url: str
    """Готове посилання для редіректу (GET). Фронт може просто зробити location.href."""

    action_url: str = ""
    """URL для POST-форми (LiqPay: https://www.liqpay.ua/api/3/checkout)."""

    data: str = ""
    """base64(json) — поле `data` форми."""

    signature: str = ""
    """base64(sha1(private + data + private)) — поле `signature` форми."""

    expires_at: Any = None
    """datetime, до якого рахунок дійсний (None — провайдер не обмежує)."""

    request: dict[str, Any] = field(default_factory=dict)
    """Редагований (allowlist) знімок запиту — для `Payment.raw_request`."""


@dataclass(frozen=True, slots=True)
class PaymentStatus:
    """Нормалізована подія провайдера — ЄДИНИЙ тип, який бачить `services.apply_payment_status`.

    Однаковий для вебхука (`parse_webhook`) і для звірки (`get_status`) — саме тому
    мапінг статусів існує рівно в одному місці і стани не можуть розійтися.
    """

    state: str | None
    """Наш `Payment.Status`. None = подію треба ІГНОРУВАТИ (subscribed/unsubscribed)."""

    raw_status: str = ""
    """Сирий статус провайдера ("success", "wait_accept", "sandbox"…) — як прийшов."""

    invoice_id: str = ""
    amount: Decimal | None = None
    currency: str = ""
    paytype: str = ""
    provider_payment_id: str = ""
    receiver_commission: Decimal | None = None
    is_moment_part: bool | None = None
    installment_count: int | None = None
    card_mask: str = ""
    card_bank: str = ""
    err_code: str = ""
    err_description: str = ""
    end_date: int | None = None
    """unix-ms з тіла (end_date | create_date) — захист від out-of-order подій."""

    needs_bank_review: bool = False
    """`wait_accept` / `wait_secure`: гроші з покупця вже списані, магазин на перевірці."""

    is_sandbox: bool = False
    """🔴 У проді sandbox-подія = ALERT і товар НЕ віддаємо (витік ключа / sandbox:1)."""

    payload: dict[str, Any] = field(default_factory=dict)
    """Редагований (allowlist) payload — для `Payment.raw_response`."""


# ---------------------------------------------------------------------------
# Протокол
# ---------------------------------------------------------------------------
@runtime_checkable
class PaymentProvider(Protocol):
    """Мінімальний контракт, від якого залежать services / api / tasks."""

    name: str
    sandbox: bool

    def create_invoice(self, payment: Payment) -> Invoice:
        """Створити рахунок/чекаут для СПРОБИ оплати (`payment`, не `order` — див. шапку)."""
        ...

    def verify_webhook(self, body: str, signature: str) -> bool:
        """Перевірити підпис вебхука. ОБОВ'ЯЗКОВО constant-time."""
        ...

    def parse_webhook(self, body: str) -> PaymentStatus:
        """Сире тіло вебхука → нормалізований стан. Дані беруться ТІЛЬКИ звідси."""
        ...

    def get_status(self, invoice_id: str) -> PaymentStatus:
        """Звірка: спитати провайдера, що сталося з рахунком. Кидає ProviderError."""
        ...

    def refund(self, payment: Payment, amount: Decimal) -> PaymentStatus:
        """Повернення коштів."""
        ...


# ---------------------------------------------------------------------------
# Реєстр
# ---------------------------------------------------------------------------
def _active_provider_name() -> str:
    """Активний провайдер — значення в адмінці (`SiteSettings.payment_provider`), не деплой."""
    try:
        from core.models import SiteSettings

        return SiteSettings.get_solo().payment_provider or "liqpay"
    except Exception:  # БД/кеш недоступні (напр. на старті процесу) — не падаємо
        log.warning("payments: не вдалось прочитати SiteSettings.payment_provider, беру liqpay")
        return "liqpay"


def get_provider(name: str | None = None) -> PaymentProvider:
    """Фабрика провайдера. `name` порожній → активний із SiteSettings."""
    name = name or _active_provider_name()

    if name == "liqpay":
        from payments.providers.liqpay import LiqPayProvider

        return LiqPayProvider.from_settings()

    raise ProviderError(f"Платіжний провайдер '{name}' не реалізований")
