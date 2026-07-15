"""
Читання довідників НП — з НАШОЇ БД, ніколи з API НП (INTEGRATIONS §1.6).

Автокомпліт по 27k населених пунктів через Postgres + GIN(gin_trgm_ops) — <5 мс проти
200–400 мс через НП. Головне не швидкість, а те, що аптайм НП НЕ БЛОКУЄ ЧЕКАУТ:
API НП на критичному шляху не викликається взагалі, крім розрахунку ціни.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.postgres.search import TrigramWordSimilarity
from django.db.models import Q, QuerySet

from delivery.models import NPArea, NPSettlement, NPWarehouse

__all__ = [
    "list_areas",
    "list_warehouses",
    "search_settlements",
]

#: Нижче цього word_similarity — це вже не «описка в назві», а інше слово.
#:
#: ⚠️ Саме WORD-similarity, а не звичайна similarity. `present` — це довгий рядок
#:    («м. Ужгород, Закарпатська обл.»), і similarity('Ужгорд', <увесь рядок>) = 0.17:
#:    короткий запит тоне в довгому рядку і НЕ знаходить нічого. word_similarity бере
#:    найкраще СЛОВО в рядку → 0.71 на тій самій описці. Заміряно на нашій же БД.
#:    Оператор `%>` теж лягає на GIN(gin_trgm_ops), який уже є в NPSettlement.
_MIN_SIMILARITY = 0.4


def list_areas() -> QuerySet[NPArea]:
    return NPArea.objects.all().order_by("name")


def search_settlements(q: str, *, limit: int = 10) -> list[NPSettlement]:
    """Автокомпліт населеного пункту.

    Порядок (INTEGRATIONS §1.6): спершу схожість назви, потім кількість відділень —
    щоб «Ужгород» (178 відділень) був вище за однойменне село з одним.

    ⚠️ `warehouses_count` — НАШ лічильник з NPWarehouse, а не поле `Warehouses` з відповіді
    НП: воно брехливе (НП каже 317 для Ужгорода, реально getWarehouses → 178).
    """
    q = (q or "").strip()
    if len(q) < 2:
        return []

    return list(
        NPSettlement.objects.filter(is_active=True)
        .select_related("area")  # SettlementOut.area — інакше N+1 на кожен рядок дропдауна
        .annotate(similarity=TrigramWordSimilarity(q, "present"))
        # icontains по GIN(gin_trgm_ops) працює (LIKE '%…%'), word_similarity ловить описки.
        .filter(Q(present__icontains=q) | Q(similarity__gte=_MIN_SIMILARITY))
        .order_by("-similarity", "-warehouses_count", "name")[:limit]
    )


def list_warehouses(
    *,
    settlement_ref: str = "",
    city_ref: str = "",
    category: str = "",
    fits: tuple[Decimal, Decimal, Decimal, Decimal] | None = None,
    limit: int = 200,
) -> list[NPWarehouse]:
    """Відділення/поштомати населеного пункту.

    🔴 `fits` = (width_cm, height_cm, length_cm, weight_kg) габаритів УПАКОВКИ товару.
       Передали — відсікаємо точки, куди посилка фізично не влізе. Холодильник у комірку
       поштомата 40×30×60 не заходить, і пропонувати його там — найчастіший баг чекаутів.
       Порівняння з урахуванням ПОВОРОТУ коробки (див. NPWarehouseQuerySet.fits_package).

    ⚠️ Групувати за `category` — робота UI: в Ужгороді поштоматів (148) у 7 разів більше,
       ніж відділень (20), і плоский список ховає відділення під поштоматами.
    """
    qs = NPWarehouse.objects.active()

    if settlement_ref:
        qs = qs.filter(settlement_ref=settlement_ref)
    elif city_ref:
        qs = qs.filter(city_ref=city_ref)
    else:
        return []

    if category:
        qs = qs.filter(category=category)

    if fits is not None:
        w, h, length, weight = fits
        qs = qs.fits_package(width_cm=w, height_cm=h, length_cm=length, weight_kg=weight)

    return list(qs.order_by("category", "number")[:limit])
