"""Побудова `search_vector_uk` / `search_vector_ru` (ADR-009, DATA_MODEL §0.1).

ЧОМУ ЦЕЙ МОДУЛЬ ТУТ, А НЕ В `rebuild_product_denorm`
----------------------------------------------------
За архітектурою вектори наповнює Celery-задача `rebuild_product_denorm`, якої ЩЕ НЕ ІСНУЄ.
Стан бази на момент написання API: `specs_json_*` і `filter_tokens` заповнені сідером, а
`search_vector_uk` / `search_vector_ru` — **NULL у всіх 67 товарів**. Тобто ендпоінт /search
за визначенням віддавав би порожньо (`@@` по NULL — це NULL, тобто «не збіг»).

Тут — рівно та частина денорму, без якої НЕ ПРАЦЮЄ пошук, винесена в окрему ідемпотентну
функцію. Коли з'явиться повний `rebuild_product_denorm`, він має ВИКЛИКАТИ `rebuild_search_vectors()`,
а не переписувати логіку заново — інакше ваги розійдуться з тим, що очікує парсер запиту.

ВАГИ (DATA_MODEL §0.1) — однакові для обох мов:
    A = name + sku + mpn          (назва й АРТИКУЛ — найважливіше)
    B = бренд + категорія
    C = характеристики            (specs_json: назва + значення + одиниця)
    D = опис (текст без HTML)     ⚠️ опис ШУКАЄТЬСЯ, але НЕ ФІЛЬТРУЄТЬСЯ (INPUTS §2)

⚠️ АРТИКУЛ У ВЕКТОРІ (вага 'A'). Плейсхолдер поля пошуку обіцяє «Пошук товарів, брендів,
   артикулів…», але sku в вектор НЕ ПОТРАПЛЯВ ЗОВСІМ: `/search?q=2401574` віддавав 0, хоча
   товар з таким артикулом існує. Людина, що переписала код з цінника, чека або сайту
   конкурента, гарантовано бачила «Нічого не знайдено» — а це один з найчастіших сценаріїв
   пошуку побутової техніки. Стемер артикули не чіпає (перевірено: '2401574' → '2401574',
   'CMG7241B1' → 'cmg7241b1'), тому їх можна класти в той самий A-рядок, що й назву.
   Префіксний оператор запиту (`240157:*`) при цьому ловить і ЧАСТКОВО введений артикул.

⚠️ СИМЕТРІЯ СТЕМЕРА. Для `uk` текст проганяється через `core.text.uk_stem` ПЕРЕД
   `to_tsvector('uk', …)`, бо конфігурація `uk` — це `simple` + `unaccent`, вона не стемить.
   РІВНО ТОЙ САМИЙ стемер застосовується до пошукового запиту (search.py). Порушиш симетрію —
   пошук замовкне мовчки. Для `ru` стемінг робить сам PostgreSQL (`ru_complex` = russian_stem),
   тому текст іде сирим.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from django.db import connection

from catalog.models import Product
from core.text.uk_stem import stem_text

log = logging.getLogger(__name__)

__all__ = ["rebuild_search_vectors"]

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# ⚠️ Дві РІЗНІ конфігурації: `uk` (simple+unaccent, стемимо в Python) і `ru_complex`
#    (russian_stem, стемить сам PG). Імена — з міграції core/0001_extensions.
#    Саме `ru_complex`, а не `ru`: `ru` у деяких збірках PG вже зайнятий як алiас.
_CONFIG = {"uk": "uk", "ru": "ru_complex"}

_SQL = """
    UPDATE catalog_product SET
        search_vector_uk =
            setweight(to_tsvector('uk', %s), 'A') ||
            setweight(to_tsvector('uk', %s), 'B') ||
            setweight(to_tsvector('uk', %s), 'C') ||
            setweight(to_tsvector('uk', %s), 'D'),
        search_vector_ru =
            setweight(to_tsvector('ru_complex', %s), 'A') ||
            setweight(to_tsvector('ru_complex', %s), 'B') ||
            setweight(to_tsvector('ru_complex', %s), 'C') ||
            setweight(to_tsvector('ru_complex', %s), 'D')
    WHERE id = %s
"""


def _strip_html(html: str) -> str:
    """Опис — rich HTML з <img>. У вектор має піти ТЕКСТ, а не розмітка.

    Без цього в індексі опиняються лексеми `div`, `figcaption`, `webp` і шматки URL картинок —
    і пошук по слову «figure» починає знаходити половину каталогу.
    """
    if not html:
        return ""
    return _WS.sub(" ", _TAGS.sub(" ", html)).strip()


def _specs_text(specs: Any) -> str:
    """specs_json → «Загальний об'єм 331 л Висота 284 мм» (назва + значення + одиниця)."""
    if not isinstance(specs, list):
        return ""
    parts: list[str] = []
    for row in specs:
        if not isinstance(row, dict):
            continue
        parts += [str(row.get("n") or ""), str(row.get("v") or ""), str(row.get("u") or "")]
    return " ".join(p for p in parts if p)


def _fields(product: Product, lang: str) -> tuple[str, str, str, str]:
    """(A, B, C, D) для однієї мови. Фолбек на UK — тією самою логікою, що й у lang.tr()."""
    suffix = lang

    def pick(name: str) -> Any:
        value = getattr(product, f"{name}_{suffix}", None)
        if not value:
            value = getattr(product, f"{name}_uk", None)
        return value or ""

    # Назва + АРТИКУЛ (+ MPN виробника) — усе у вазі 'A'. Артикул мовно-незалежний,
    # тому йде в обидва вектори однаково.
    a = " ".join(p for p in (str(pick("name")), product.sku, product.mpn) if p)

    brand = product.brand.name if product.brand_id else ""
    category = ""
    if product.category_id:
        category = getattr(product.category, f"name_{suffix}", "") or product.category.name_uk or ""
    b = f"{brand} {category}".strip()

    c = _specs_text(pick("specs_json"))
    d = _strip_html(str(pick("description")))

    return a, b, c, d


def rebuild_search_vectors(
    products: Iterable[Product] | None = None, *, batch_size: int = 500
) -> int:
    """Перебудовує вектори. Ідемпотентна:ганяти скільки завгодно разів.

    Повертає кількість оброблених товарів.
    """
    qs = products if products is not None else Product.objects.all()
    if hasattr(qs, "select_related"):
        qs = qs.select_related("brand", "category")

    rows: list[tuple] = []
    processed = 0

    def flush() -> None:
        nonlocal rows
        if not rows:
            return
        with connection.cursor() as cursor:
            cursor.executemany(_SQL, rows)
        rows = []

    for product in qs.iterator(chunk_size=batch_size):
        uk_a, uk_b, uk_c, uk_d = _fields(product, "uk")
        ru_a, ru_b, ru_c, ru_d = _fields(product, "ru")

        rows.append(
            (
                # uk — стемимо в Python (конфіг `uk` не стемить сам)
                stem_text(uk_a),
                stem_text(uk_b),
                stem_text(uk_c),
                stem_text(uk_d),
                # ru — стемить PostgreSQL (russian_stem), віддаємо сирий текст
                ru_a,
                ru_b,
                ru_c,
                ru_d,
                product.pk,
            )
        )
        processed += 1

        if len(rows) >= batch_size:
            flush()

    flush()
    log.info("search_vector перебудовано для %s товарів", processed)
    return processed
