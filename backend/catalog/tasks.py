"""Задачі каталогу.

⚠️ ЦЕЙ МОДУЛЬ НЕ ОПЦІЙНИЙ. `core.models.SiteSettings.save()` планує `recalc_prices.delay()`
   через `transaction.on_commit`, щойно змінився курс USD або правило округлення. Без цього
   файла будь-яка зміна курсу в адмінці падала б з ModuleNotFoundError НА КОМІТІ — тобто
   головна дія на сторінці налаштувань відповідала б 500-ю.

Перерахунок — ОДИН UPDATE із SQL-функцією `complex_price_uah()` (ADR-005), а не цикл по товарах:
  • 10 000 товарів × 2 виклики формули з Python = 20 000 round-trip-ів;
  • формула мусить бути та сама, що в синку, — інакше адмінка і синк розійдуться в округленні.

Історію цін пише PG-тригер `price_history_trg` (ADR-006). Він бачить масовий UPDATE так само,
як ручну правку, — але лише якщо в транзакції виставлено `complex.price_reason` (див. price_reason()).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from celery import shared_task
from django.db import connection

from catalog.services.pricing import price_reason
from core.models import SiteSettings

log = logging.getLogger(__name__)

__all__ = ["recalc_prices"]


# ⚠️ old_price — ПОХІДНА і мусить лишатись СТРОГО більшою за price, інакше
#    CheckConstraint prod_old_price_gt_price покладе весь UPDATE. Після зростання курсу стара
#    ціна цілком може «наздогнати» нову — такий old_price гаситься в NULL, а не показується
#    як знижка вгору.
_RECALC_SQL = """
UPDATE catalog_product AS p SET
    price = complex_price_uah(p.base_price, p.source_currency, %(rate)s, p.markup_percent, %(rule)s),
    old_price = CASE
        WHEN p.base_old_price IS NULL THEN NULL
        WHEN complex_price_uah(p.base_old_price, p.source_currency, %(rate)s, p.markup_percent, %(rule)s)
             > complex_price_uah(p.base_price, p.source_currency, %(rate)s, p.markup_percent, %(rule)s)
        THEN complex_price_uah(p.base_old_price, p.source_currency, %(rate)s, p.markup_percent, %(rule)s)
        ELSE NULL
    END,
    usd_rate_used = CASE WHEN p.source_currency = 'USD' THEN %(rate)s ELSE NULL END,
    price_updated_at = now(),
    updated_at = now()
WHERE p.price_locked = false
  -- Товар, для якого формула дає NULL (USD без валідного курсу), пропускаємо: price NOT NULL.
  AND complex_price_uah(p.base_price, p.source_currency, %(rate)s, p.markup_percent, %(rule)s) IS NOT NULL
  {scope_filter}
"""


@shared_task(queue="sync", ignore_result=True)
def recalc_prices(
    scope: str = "usd",
    old_rate: str | None = None,
    new_rate: str | None = None,
    old_rounding: str = "",
    new_rounding: str = "",
    user_id: int | None = None,
    **_: Any,
) -> int:
    """Перераховує ціни каталогу за поточним курсом і правилом округлення.

    scope="usd" — тільки USD-товари (змінився курс);
    scope="all" — весь каталог (змінилось ПРАВИЛО ОКРУГЛЕННЯ: інакше половина каталогу
                  лишиться в старому округленні до наступного синку).

    Товари з `price_locked=True` не чіпаються — але з прогону НЕ виключаються нічим іншим:
    фіксація ціни не має вибивати товар з життєвого циклу.
    """
    solo = SiteSettings.get_solo()
    rate: Decimal = solo.usd_rate
    rule: str = solo.price_rounding

    scope_filter = (
        "AND p.source_currency = 'USD'" if scope == "usd" else ""
    )
    sql = _RECALC_SQL.format(scope_filter=scope_filter)

    with price_reason("recalc"), connection.cursor() as cur:
        cur.execute(sql, {"rate": rate, "rule": rule})
        updated = cur.rowcount

    log.info(
        "recalc_prices scope=%s rate=%s rule=%s updated=%s",
        scope,
        rate,
        rule,
        updated,
    )

    _log_rate_change(
        old_rate=old_rate,
        new_rate=new_rate,
        old_rounding=old_rounding,
        new_rounding=new_rounding,
        user_id=user_id,
        updated=updated,
    )
    return updated


def _log_rate_change(
    *,
    old_rate: str | None,
    new_rate: str | None,
    old_rounding: str,
    new_rounding: str,
    user_id: int | None,
    updated: int,
) -> None:
    """Аудит зміни курсу. Рядок пише САМЕ ЗАДАЧА: core про sync нічого не знає."""
    try:
        from sync.models import UsdRateChange
    except ImportError:  # pragma: no cover
        return

    UsdRateChange.objects.create(
        old_rate=Decimal(old_rate) if old_rate else None,
        new_rate=Decimal(new_rate) if new_rate else None,
        old_rounding=old_rounding or "",
        new_rounding=new_rounding or "",
        changed_by_id=user_id,
        products_recalculated=updated,
    )
