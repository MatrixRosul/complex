"""Сервіси доставки.

Публічний фасад: `from delivery.services import effective_dims, quote, ...`
Глибокі шляхи теж робочі: `delivery.services.dims` (на нього посилаються docstring-и
моделей і ADR-021), `.calculator`, `.refs`.

⚠️ Модуль калькулятора називається `calculator.py`, а НЕ `quote.py`, хоч головна функція —
`quote()`. Інакше ім'я `delivery.services.quote` означало б і субмодуль, і реекспортовану
функцію: атрибут пакета перетирався б функцією, і `monkeypatch.setattr("delivery.services
.quote.X", …)` (як і будь-який getattr-обхід) ламався б неочевидно.
"""

from __future__ import annotations

from delivery.services.calculator import (
    QUOTE_TTL,
    QuoteItem,
    QuoteResult,
    Reason,
    quote,
)
from delivery.services.dims import (
    CartDims,
    EffectiveDims,
    cart_dims,
    effective_dims,
)
from delivery.services.refs import (
    list_areas,
    list_warehouses,
    search_settlements,
)

__all__ = [
    "QUOTE_TTL",
    "CartDims",
    "EffectiveDims",
    "QuoteItem",
    "QuoteResult",
    "Reason",
    "cart_dims",
    "effective_dims",
    "list_areas",
    "list_warehouses",
    "quote",
    "search_settlements",
]
