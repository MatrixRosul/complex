"""Пошук: FTS + trigram-фолбек на одруківки (ADR-009).

ТРИ ШАРИ, РІВНО ЯК В ADR-009
---------------------------
1. **FTS по `search_vector_{lang}`** — основний шлях. Конфіг `uk` (simple+unaccent) або
   `ru_complex` (russian_stem).
2. **Префіксний матчинг** (`холод:*`) — бо «холод» мусить знаходити «Холодильник». Без нього
   пошук працює лише на повних словах, і живий рядок пошуку (де людина дописує на льоту)
   не знаходить нічого, доки слово не дописане до кінця.
3. **Trigram-фолбек** — одруківки («холодильнек», «Gorenge»). Індекси `prod_name_uk_trgm` /
   `prod_name_ru_trgm` (gin_trgm_ops) уже є в моделі.

   ⚠️ Саме `word_similarity`, а НЕ `similarity`. Це не мікрооптимізація, а різниця між «працює» і
   «не працює»: `similarity()` порівнює запит з УСІМ рядком назви, тому короткий запит проти
   довгої назви дає мізерний бал і не проходить жоден розумний поріг. Виміряно на цій же базі:

       запит «Gorenge» vs «Варильна поверхня Gorenje GKT641SYW»
           similarity      = 0.132   ← нижче будь-якого порога, одруківка НЕ знаходиться
           word_similarity = 0.625   ← порівнюється з НАЙСХОЖІШИМ СЛОВОМ у назві

       «телевізр» → 0.200 / 0.778 · «холодильнек» → 0.265 / 0.750 · «Bosh» → 0.081 / 0.600

   З `similarity` і порогом 0.3 фолбек мовчав би рівно там, де він і потрібен — на одруківках.

⚠️ СИМЕТРІЯ ЗІ СТЕМЕРОМ ІНДЕКСУ. Для `uk` запит проганяється через ТОЙ САМИЙ
   `core.text.uk_stem`, що й текст при побудові вектора (search_index.py). Якби ми застемили
   документ, а запит — ні, то «холодильники» шукалось би як `холодильники`, а в індексі лежить
   `холодильник` — нуль результатів, і жодної помилки в логах.

⚠️ Чому tsquery будується вручну, а не `websearch_to_tsquery`: нам потрібен префіксний оператор
   `:*`, якого той не вміє. Токени жорстко санітизуються (лишаються тільки букви/цифри), тому
   ін'єкція неможлива — у tsquery не потрапляє жоден спецсимвол.
"""

from __future__ import annotations

import re
from typing import Final

from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramWordSimilarity
from django.db.models import Case, F, IntegerField, Q, QuerySet, When

from catalog.models import Product
from core.text.uk_stem import stem_text

__all__ = ["TRIGRAM_THRESHOLD", "search_products"]

_CONFIG: Final[dict[str, str]] = {"uk": "uk", "ru": "ru_complex"}

# Артикул: суцільний токен з цифр/латиниці (можливі дефіси/підкреслення) — «2401574»,
# «CMG7241B1», «HS836GVB6». Кирилиця сюди НЕ входить: «холодильник» не має ганяти
# зайвий LIKE по sku на кожен запит пошуку.
_SKU_LIKE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_/]{2,}$")

# Поріг word_similarity для одруківок. Реальні одруківки на цій базі дають 0.60–0.78
# (див. шапку модуля), тому 0.5 ловить їх усі й лишає запас від шуму. Нижче 0.4 у видачу
# починають протікати випадкові збіги по спільних коренях.
TRIGRAM_THRESHOLD: Final[float] = 0.5

# У tsquery-токен пускаємо ТІЛЬКИ букви й цифри. Усе інше (& | ! : * ' — оператори tsquery)
# вирізається — саме це робить ручну збірку запиту безпечною.
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _tsquery(q: str, lang: str) -> SearchQuery | None:
    """«холодильник Bosch» → tsquery `холодильник:* & bosch:*` (префікс на кожному токені)."""
    source = stem_text(q) if lang == "uk" else q.lower()

    tokens = [t for t in _TOKEN.findall(source) if t]
    if not tokens:
        return None

    raw = " & ".join(f"{t}:*" for t in tokens)
    return SearchQuery(raw, config=_CONFIG[lang], search_type="raw")


def search_products(q: str, lang: str) -> QuerySet[Product]:
    """QuerySet товарів, відсортований за релевантністю.

    Повертає саме QuerySet (а не список), щоб /catalog міг накласти на нього фасети й
    пагінацію — пошук і фільтри мусять працювати РАЗОМ, а не бути двома різними сторінками.
    """
    q = (q or "").strip()
    if not q:
        return Product.objects.none()

    vector_field = f"search_vector_{lang}"
    name_field = f"name_{lang}"

    # Схожість із НАЙСХОЖІШИМ СЛОВОМ назви (не з усім рядком — див. шапку модуля).
    trigram = Product.objects.annotate(similarity=TrigramWordSimilarity(q, name_field)).filter(
        similarity__gte=TRIGRAM_THRESHOLD
    )

    # ── АРТИКУЛ ─────────────────────────────────────────────────────────────
    # sku лежить у векторі з вагою 'A' (search_index.py), тому FTS його вже знаходить.
    # Але цього мало: PostgreSQL ділить «CMG7241B1» на лексеми, і рейтинг точного збігу
    # за артикулом може програти товару, у якого це число трапилось в описі. Людина, що
    # ввела артикул, хоче РІВНО цей товар першим рядком — тому точний/префіксний збіг по
    # sku піднімається над рейтингом FTS окремим ключем сортування.
    is_sku_query = bool(_SKU_LIKE.match(q))
    sku_rank = (
        Case(
            When(sku__iexact=q, then=2),
            When(sku__istartswith=q, then=1),
            default=0,
            output_field=IntegerField(),
        )
        if is_sku_query
        else None
    )

    query = _tsquery(q, lang)
    if query is None:
        # Запит зі самих спецсимволів («???») — FTS не з чого будувати, лишається тільки trigram.
        return trigram.order_by("-similarity", "-is_featured", "-id")

    fts = Product.objects.annotate(rank=SearchRank(F(vector_field), query))
    if sku_rank is not None:
        # `| Q(sku__istartswith=q)` — щоб частковий артикул знаходився НАВІТЬ там, де
        # токенізація FTS його не врятувала (напр. sku зі слешем чи крапкою).
        fts = (
            fts.annotate(sku_rank=sku_rank)
            .filter(Q(**{f"{vector_field}__exact": query}) | Q(sku__istartswith=q))
            .order_by("-sku_rank", "-rank", "-is_featured", "-id")
        )
    else:
        fts = fts.filter(**{f"{vector_field}__exact": query}).order_by(
            "-rank", "-is_featured", "-id"
        )

    # ⚠️ TRIGRAM — САМЕ ФОЛБЕК, А НЕ ПОСТІЙНИЙ `OR` (ADR-009: «…для опечаток і КОЛИ FTS ДАВ
    #    0 РЕЗУЛЬТАТІВ»). Спокуса написати `Q(fts) | Q(trigram)` одним запитом закінчується тим,
    #    що нечіткі збіги РОЗБАВЛЯЮТЬ чисту видачу: на цій же базі «пральна машина» віддавала
    #    11 товарів замість 5 — до п'яти справжніх пралок домішувалось шість випадкових,
    #    що зачепились коренем. Користувач, який ввів точний запит, має отримати точну відповідь.
    #    Тому: спершу FTS; тригами вмикаються, ЛИШЕ якщо FTS не знайшов нічого.
    #    Ціна — один дешевий EXISTS (індексований GIN-скан) на запит пошуку.
    if fts.exists():
        return fts

    return trigram.order_by("-similarity", "-is_featured", "-id")
