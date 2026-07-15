"""Рядок брендів на головній: топ-N виробників за кількістю активних товарів.

⚠️ ПОСИЛАННЯ З БРЕНДА ВЕДЕ У ФАСЕТ, А НЕ В ОКРЕМИЙ РОУТ. Токен фільтра — `brand:{slug}`
   (див. sync/services.py::rebuild_product_denorm і facets.py), тому фронт будує URL як
   `/catalog?brand={slug}` — саме `slug`, а НЕ `name`. «Bosch» у query дав би нуль товарів:
   `parse_facet_params` порівнює значення з суфіксом токена, а він завжди в нижньому регістрі й
   латиницею. Через це `slug` тут — обов'язкове поле відповіді, а не декорація.

⚠️ ЛОГОТИПІВ НЕМАЄ (0 з 49 брендів мають `logo`), тому `logo_url` майже завжди None, і фронт
   малює бренд типографікою. Це не тимчасова заглушка, яку треба «чимось замінити»: підставляти
   сюди чужі логотипи з інтернету не можна — це чужі торгові марки на нашому сервері.
   Щойно замовник заллє логотип в адмінці, поле почне віддавати URL само.

⚠️ ЛІЧИЛЬНИК РАХУЄТЬСЯ ПО `filter_tokens`, А НЕ ПО FK `Product.brand` — І ЦЕ КЛЮЧОВЕ.
   Це та сама колонка й той самий запит (`listing.token_counts`), з яких живуть фасети і
   фільтрація (ADR-008: одне джерело). Наївний `Count("product")` по FK дає ІНШЕ число:
   перевірено на живій базі — Bosch по FK = 70, а по токенах = 67, бо в трьох товарів денорм ще
   не прогнався (`denorm_dirty=True`, `filter_tokens=[]`). Плитка «Bosch (70)», яка веде в
   каталог з 67 товарами, — це рівно та critical-бага v1, від якої тікала вся архітектура
   фасетів. Тут вона неможлива за побудовою: лічильник і видача читають одне поле.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from django.core.cache import cache

from catalog.models import Brand
from catalog.services import listing
from catalog.services.cache import BRANDS_TTL, brands_key

log = logging.getLogger(__name__)

__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "get_top_brands"]

DEFAULT_LIMIT: Final[int] = 12
# Стеля вибірки: кеш тримає рівно стільки, `?limit=` ріже вже готовий список у Python (тому
# `limit` не входить у ключ кешу — інакше кожне нове значення заводило б свою копію).
MAX_LIMIT: Final[int] = 48


def _logo_url(brand: Brand) -> str | None:
    """`logo.url` кидає ValueError, коли файлу немає — той самий захист, що й у cards.brand_dict."""
    try:
        return brand.logo.url if brand.logo else None
    except ValueError:
        return None


def _build() -> list[dict[str, Any]]:
    """Топ-бренди: два запити — лічильники по токенах + самі бренди.

    Бренд без жодного товару в рядку не потрібен: клік по ньому веде у порожній каталог (те саме
    правило, що й для порожніх категорій у дереві). Тому беремо ТІЛЬКИ ті slug'и, які реально
    зустрілись у токенах.

    Порядок: за кількістю товарів, тай-брейк — за назвою. Без тай-брейка два бренди з однаковою
    кількістю міняються місцями між запитами, і рядок брендів «мерехтить» на кожній ревалідації.
    """
    # Базовий набір каталогу БЕЗ фільтрів = «усе, що видно на сайті» (активний товар в активній
    # категорії). Рівно з нього рахує лічильники фасет бренду.
    base = listing.product_base_qs(listing.CatalogFilters())
    counts = listing.token_counts(base).get("brand", {})
    if not counts:
        return []

    brands = Brand.objects.filter(is_active=True, slug__in=counts.keys())

    rows = [
        {
            "id": brand.pk,
            "name": brand.name,
            "slug": brand.slug,
            "products_count": counts[brand.slug],
            "logo_url": _logo_url(brand),
        }
        for brand in brands
    ]
    rows.sort(key=lambda row: (-row["products_count"], row["name"]))
    return rows[:MAX_LIMIT]


def get_top_brands(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Топ-`limit` брендів. Кеш Redis, TTL 1 год.

    ⚠️ КЛЮЧ БЕЗ МОВИ — і це єдиний кеш каталогу, де так можна: `Brand.name` не перекладний
       (власна назва, DATA_MODEL §2.2), а `slug` і лічильник тим паче. Дублювати ті самі 12
       рядків під `:uk` і `:ru` означало б удвічі більше промахів кешу ні за що.

    ⚠️ Лічильник залежить від ТОВАРІВ, а кеш зносить лише сигнал по Brand (signals.py). Тобто
       після синку кількість самозагоюється через TTL — так само, як лічильники в дереві
       категорій. Показати «Bosch (70)» замість «(71)» протягом години — прийнятно; знімати кеш
       на кожен з 10 000 товарів прогону — ні.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    key = brands_key()

    try:
        cached = cache.get(key)
    except Exception:
        # Redis лежить — головна має відрендеритись і без нього.
        log.warning("Не вдалося прочитати кеш брендів", exc_info=True)
        cached = None

    if cached is None:
        cached = _build()
        try:
            cache.set(key, cached, BRANDS_TTL)
        except Exception:
            log.warning("Не вдалося записати кеш брендів", exc_info=True)

    return cached[:limit]
