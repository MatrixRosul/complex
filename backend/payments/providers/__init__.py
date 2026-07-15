"""Провайдери платежів.

Абстракція (`base.py`) + реалізації (`liqpay.py`). Активний провайдер обирається
значенням `SiteSettings.payment_provider` — зміна провайдера НЕ вимагає переписування
`orders` (ADR-015 / ADR-025).
"""

from __future__ import annotations

from payments.providers.base import (
    Invoice,
    PaymentProvider,
    PaymentStatus,
    ProviderError,
    ProviderNotFound,
    SignatureError,
    get_provider,
)

__all__ = [
    "Invoice",
    "PaymentProvider",
    "PaymentStatus",
    "ProviderError",
    "ProviderNotFound",
    "SignatureError",
    "get_provider",
]
