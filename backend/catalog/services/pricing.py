"""Ціноутворення — ЄДИНЕ джерело формули (ADR-005).

⚠️ ФОРМУЛА ЦІНИ ЖИВЕ В SQL, а не тут. `compute_uah_price()` не переписує формулу на Python —
   вона ВИКЛИКАЄ ту саму функцію `complex_price_uah()`, яку створює core/migrations/0001_extensions
   і якою користуються синк і recalc. Тому паритет «адмінка vs синк» гарантований за побудовою,
   а не тестом: одна функція, один код, нуль шансів на дрейф округлення.
   (DATA_MODEL §0 припускав Python-двійник + fuzz-тест паритету. Прямий виклик SQL робить і
   двійник, і тест непотрібними.)

⚠️ ІСТОРІЯ ЦІН пишеться PG-тригером `price_history_trg` (ADR-006), а не звідси. Тригер читає
   причину зі змінної сесії `complex.price_reason` — її виставляє контекст-менеджер `price_reason()`.
   БЕЗ нього будь-яка зміна ціни запишеться в PriceHistory як 'manual'.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db import connection, transaction
from django.utils import timezone

from catalog.enums import SourceCurrency
from core.models import SiteSettings

if TYPE_CHECKING:
    from catalog.models import Product

log = logging.getLogger(__name__)

__all__ = ["PriceError", "apply_pricing", "compute_uah_price", "price_reason"]


class PriceError(ValueError):
    """Ціну обчислити неможливо (USD без курсу тощо)."""


@contextmanager
def price_reason(reason: str, run_id: str | None = None) -> Iterator[None]:
    """Проставляє причину зміни ціни для PG-тригера `complex_log_price()` (ADR-006).

    ⚠️ `set_config(..., is_local => true)` діє ЛИШЕ всередині транзакції; в autocommit це
    мовчазний no-op. Тому `transaction.atomic()` тут обов'язковий, а не «про всяк випадок».
    """
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('complex.price_reason', %s, true)", [reason])
            if run_id:
                cur.execute("SELECT set_config('complex.run_id', %s, true)", [str(run_id)])
        yield


def compute_uah_price(
    base: Decimal | None,
    currency: str,
    rate: Decimal | None,
    markup: Decimal | None,
    rule: str,
) -> Decimal | None:
    """Ціна вітрини у грн. Тонкий проксі на SQL-функцію complex_price_uah() (ADR-005).

    Повертає None, якщо ціну обчислити неможливо (немає base; або USD без валідного курсу) —
    рівно так само, як SQL-функція.
    """
    if base is None:
        return None
    with connection.cursor() as cur:
        cur.execute(
            "SELECT complex_price_uah(%s::numeric, %s::text, %s::numeric, %s::numeric, %s::text)",
            [base, currency, rate, markup or Decimal(0), rule],
        )
        return cur.fetchone()[0]


def apply_pricing(
    product: Product,
    *,
    rate: Decimal | None = None,
    rule: str | None = None,
) -> None:
    """Перераховує похідні поля ціни ІН-ПЛЕЙС (без save()).

    Похідні: price, old_price, usd_rate_used, price_updated_at.
    Джерела:  base_price, base_old_price, source_currency, markup_percent + курс/округлення.

    ⚠️ old_price теж ПОХІДНА (з base_old_price тією ж функцією). Якщо після перерахунку
       old_price виявилась НЕ БІЛЬШОЮ за price — вона гаситься в None, інакше INSERT впаде на
       CheckConstraint `prod_old_price_gt_price`, а картка показала б «знижку» вгору.
    """
    solo = SiteSettings.get_solo()
    rate = solo.usd_rate if rate is None else rate
    rule = solo.price_rounding if rule is None else rule

    is_usd = product.source_currency == SourceCurrency.USD
    # CheckConstraint prod_usd_needs_rate: USD ⇒ usd_rate_used NOT NULL.
    product.usd_rate_used = rate if is_usd else None

    price = compute_uah_price(
        product.base_price, product.source_currency, rate, product.markup_percent, rule
    )
    if price is None:
        raise PriceError(
            "Не вдалося обчислити ціну. Для валюти USD потрібен додатний курс "
            "у «Налаштуваннях магазину»."
        )
    product.price = price

    old = compute_uah_price(
        product.base_old_price, product.source_currency, rate, product.markup_percent, rule
    )
    product.old_price = old if (old is not None and old > price) else None

    product.price_updated_at = timezone.now()


def recalc_products(products: Any, *, reason: str = "recalc") -> int:
    """Перерахунок ціни для невеликої вибірки (адмінська дія).

    ⚠️ НЕ для всього каталогу: тут по 2 SQL-виклики формули на товар і save() на товар.
       Масовий перерахунок (10k товарів після зміни курсу) — це `catalog.tasks.recalc_prices`
       одним `UPDATE ... SET price = complex_price_uah(...)`; він з'явиться наступним кроком.
    """
    n = 0
    with price_reason(reason):
        for product in products:
            apply_pricing(product)
            product.save(
                update_fields=[
                    "price",
                    "old_price",
                    "usd_rate_used",
                    "price_updated_at",
                    "updated_at",
                ]
            )
            n += 1
    return n
