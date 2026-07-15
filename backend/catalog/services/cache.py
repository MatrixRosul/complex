"""Redis-кеш каталогу: ключі, TTL, інвалідація (ARCHITECTURE §2.2, ADR-003).

⚠️ МОВА — ЧАСТИНА КОЖНОГО КЛЮЧА. Інакше перший запит українською нагріє кеш, а російський
   користувач отримає українське дерево категорій. Це не гіпотетично: `cat:tree` без `:{lang}`
   — класичний спосіб «здати» RU-версію сайту в перший же день.

Що кешуємо і чому саме це:
  * **дерево категорій** — 24 вузли, змінюється раз на місяць, читається на КОЖНІЙ сторінці
    (мегаменю). Ідеальний кандидат: висока частота читання, майже нульова частота запису.
  * **метадані фасетів** (підписи, одиниці, порядок, опції) — беруться з EAV, змінюються раз на
    тиждень, а потрібні на кожен запит каталогу.

Що НЕ кешуємо принципово:
  * **лічильники фасетів і списки товарів.** Ключем був би (категорія × усі активні фільтри ×
    сортування × сторінка × мова) — комбінаторний вибух, при якому hit-rate прагне нуля, а Redis
    забивається сміттям. Вони й так рахуються одним індексованим запитом по GIN (ADR-008).
  * **ціни й наявність.** Це те, заради чого існує /products/bulk (ADR-017). Кешувати їх —
    означає повернути протухлі ціни, від яких ми тікали.
"""

from __future__ import annotations

import logging
from typing import Final

from django.core.cache import cache

log = logging.getLogger(__name__)

__all__ = [
    "BRANDS_TTL",
    "COLLECTIONS_TTL",
    "FACET_META_TTL",
    "TREE_TTL",
    "brands_key",
    "collections_key",
    "facet_meta_key",
    "invalidate_catalog_cache",
    "invalidate_collections_cache",
    "tree_key",
]

# Короткий TTL — це не «оптимізація», а СТРАХОВКА: якщо сигнал інвалідації десь не спрацював
# (нова модель, bulk-операція в обхід save()), кеш самозагоїться максимум за годину, а не
# «ніколи, до наступного деплою» (той самий принцип, що й heal-задачі в ADR-011).
TREE_TTL: Final[int] = 3600  # 1 год
FACET_META_TTL: Final[int] = 3600  # 1 год
BRANDS_TTL: Final[int] = 3600  # 1 год — рядок брендів на головній (services/brands.py)

# ⚠️ 5 хв, а не година. Добірки на головній — це те, ЧИМ ЗАМОВНИК КЕРУЄ РУКАМИ: поставив
#    «Рекомендований» в адмінці → хоче побачити товар у «Хітах продажів», а не через годину.
#    post_save по Product зносить ключ одразу (див. invalidate_collections_cache), TTL лишається
#    страховкою на випадок bulk_update у синку, який сигналів не шле взагалі.
COLLECTIONS_TTL: Final[int] = 300  # 5 хв

_TREE_PREFIX: Final[str] = "cat:tree"
_FACET_META_PREFIX: Final[str] = "facets:meta"
_COLLECTIONS_PREFIX: Final[str] = "cat:collections"
_BRANDS_KEY: Final[str] = "cat:brands"


def tree_key(lang: str) -> str:
    return f"{_TREE_PREFIX}:{lang}"


def brands_key() -> str:
    """⚠️ ЄДИНИЙ ключ каталогу БЕЗ мови — і це не забудькуватість. `Brand.name` не перекладний
    (власна назва, DATA_MODEL §2.2), як і `slug` з лічильником. Ділити ті самі 12 рядків на
    `:uk` і `:ru` означало б удвічі більше промахів кешу за ідентичні дані.
    """
    return _BRANDS_KEY


def facet_meta_key(lang: str) -> str:
    return f"{_FACET_META_PREFIX}:{lang}"


def collections_key(lang: str) -> str:
    """⚠️ БЕЗ `limit` у ключі. Кеш тримає МАКСИМАЛЬНУ вибірку, а зріз під `?limit=` робиться вже
    в Python. Інакше ключ став би (мова × limit), і кожен новий limit з фронту заводив би свою
    копію добірок — а інвалідація мусила б знати ВСІ limit'и, які колись хтось запитав.
    """
    return f"{_COLLECTIONS_PREFIX}:{lang}"


def invalidate_catalog_cache() -> None:
    """Зносить дерево категорій і метадані фасетів для ОБОХ мов.

    ⚠️ Свідомо тупо: б'ємо по всіх мовах одразу. Точкова інвалідація («змінилась лише RU-назва
    категорії → знести лише `cat:tree:ru`») economить мікросекунди і коштує цілого класу багів
    «забули інвалідувати другу мову». Ключів тут одиниці — не той масштаб, щоб хитрувати.

    ⚠️ БЕЗ `cache.clear()`: він зносить ВЕСЬ Redis db1 — сесії (`cached_db`), кеш синглтона
    SiteSettings, троттлінг. Видаляємо рівно свої ключі.
    """
    keys = [tree_key(lang) for lang in ("uk", "ru")]
    keys += [facet_meta_key(lang) for lang in ("uk", "ru")]
    keys += [collections_key(lang) for lang in ("uk", "ru")]
    keys.append(brands_key())
    try:
        cache.delete_many(keys)
    except Exception:
        # Кеш недоступний — це не привід валити збереження в адмінці. TTL добере своє.
        log.warning("Не вдалося інвалідувати кеш каталогу", exc_info=True)


def invalidate_collections_cache() -> None:
    """Зносить ТІЛЬКИ добірки для головної (обидві мови).

    ⚠️ Окремо від `invalidate_catalog_cache()` навмисно. Цю функцію смикає post_save по Product —
    тобто вона спрацьовує НА КОЖНЕ збереження товару в адмінці. Знести заразом дерево категорій
    і метадані фасетів (24 вузли + весь EAV, які до товару стосунку не мають) означало б
    перебудовувати їх щоразу, коли контент-менеджер поправив опис одного холодильника.
    """
    try:
        cache.delete_many([collections_key(lang) for lang in ("uk", "ru")])
    except Exception:
        log.warning("Не вдалося інвалідувати кеш добірок", exc_info=True)
