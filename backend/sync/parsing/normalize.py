"""
Текстова нормалізація: моделі, кандидати, плейсхолдери, зіставлення зі SpecSheet.

`normalize_model()` — це порт нормалізації з Google Apps Script Артура (INPUTS.md §3.5),
перенесений 1:1. Логіка вистраждана на реальних даних, тому міняти її «бо так гарніше»
не можна: будь-яка зміна алфавіту або порядку кроків міняє ключ зіставлення прайсу
з таблицею характеристик — і товари мовчки лишаються без габаритів.

⚠️ Це РЕЗЕРВНИЙ ключ. Основний ключ зіставлення в нас — АРТИКУЛ (SYNC.md §1.5.3):
обидві таблиці його мають, на відміну від скрипта Артура, який зшивав тільки по назві.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

__all__ = [
    "PLACEHOLDER_VALUES",
    "collapse_spaces",
    "find_spec_match",
    "is_placeholder",
    "model_candidates",
    "normalize_model",
]

# --- Алфавіти -----------------------------------------------------------------

#: Усі види пробілів, які Sheets/Excel радо кладуть у клітинки замість звичайного.
#: NBSP, вузький NBSP, тонкий, цифровий, волосяний, ZWSP, ZWNJ, ZWJ, BOM, ideographic.
SPACE_CHARS = "          ​‌‍﻿　\t\r\n"
_SPACE_RE = re.compile(f"[{re.escape(SPACE_CHARS)}]")

#: Усі види тире й мінусів → звичайний дефіс.
#: figure dash, en, em, horizontal bar, non-breaking hyphen, minus sign, small/full-width.
DASH_CHARS = "‒–—―‑−﹘﹣－‐"
_DASH_RE = re.compile(f"[{re.escape(DASH_CHARS)}]")

#: Апострофи (в т.ч. український U+02BC) → прямий '.
APOSTROPHE_CHARS = "’‘`´ʼʹ′՚"
_APOSTROPHE_RE = re.compile(f"[{re.escape(APOSTROPHE_CHARS)}]")

#: Лапки → прямі ".
QUOTE_CHARS = "“”„‟«»″"
_QUOTE_RE = re.compile(f"[{re.escape(QUOTE_CHARS)}]")

#: Сміття, яке просто викидаємо.
_JUNK_RE = re.compile("[®™©]")  # ® ™ ©

#: Знаки множення → латинська "x" (важливо для габаритів: "60×65×185").
_TIMES_RE = re.compile("[×✕✖⨯]")

#: Кириличні двійники латинських літер, які реально трапляються в назвах моделей.
#: Скрипт Артура обробляв тільки І/і — переносимо 1:1 (див. ⚠️ у звіті модуля).
_CYRILLIC_LOOKALIKES = str.maketrans({"І": "I", "і": "i"})  # І → I, і → i

#: Фінальний білий список. Усе, що не сюди, — викидається.
#: Пробіл лишається в класі свідомо: він потрібен для схлопування, а вже потім зникає.
_ALLOWED_RE = re.compile(r"[^a-z0-9._\-/ ]")

_MULTISPACE_RE = re.compile(r" {2,}")

# --- Плейсхолдери -------------------------------------------------------------

#: «Уточнюється» — це НЕ значення, це «ще не знаю» (INPUTS.md §3.2, пастка 2).
#: Без цієї перевірки синк створює бренд «Уточнюється» на 200 товарів, шукає категорію
#: з external_id="Уточнюється" і 200 разів довбиться в DNS з «URL» `Уточнюється`.
#: Причому все це — тихо і «успішно», із зеленим SUCCESS.
PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {
        "",
        "уточнюється",
        "уточнюеться",
        "уточняется",
        "уточнити",
        "немає даних",
        "нет данных",
        "немає",
        "нема",
        "нет",
        "n/a",
        "na",
        "н/д",
        "-",
        "—",
        "–",
        "‒",
        "?",
        "...",
        "…",
    }
)


def collapse_spaces(raw: Any) -> str:
    """NBSP та рідня → звичайний пробіл, схлопнути подвійні, обрізати краї."""
    s = _SPACE_RE.sub(" ", str(raw or ""))
    return _MULTISPACE_RE.sub(" ", s).strip()


def is_placeholder(raw: Any) -> bool:
    """
    `True`, якщо значення насправді означає «порожньо».

    ⚠️ Ціна й артикул через `is_placeholder` НЕ проходять — там «Уточнюється»
    це битий рядок (ERROR), а не порожнє поле (SYNC.md §4.2).
    """
    s = collapse_spaces(raw).casefold().rstrip(".:")
    return not s or s in PLACEHOLDER_VALUES


def clean_or_empty(raw: Any) -> str:
    """Нормалізований текст або "" для плейсхолдера. Для brand/country/category/photo."""
    return "" if is_placeholder(raw) else collapse_spaces(raw)


# --- Ядро: нормалізація моделі ------------------------------------------------


def normalize_model(raw: Any) -> str:
    """
    Порт `normalizeModel()` зі скрипта Артура (INPUTS.md §3.5), крок у крок.

        >>> normalize_model("Варильна поверхня газова Bosch PNK6B2P40R")
        'boschpnk6b2p40r'
        >>> normalize_model("Siemens\\u00a0HZ66D910")   # NBSP
        'siemenshz66d910'

    Порядок кроків має значення — не міняти місцями.

    ⚠️ ПОБІЧНИЙ ЕФЕКТ, УСПАДКОВАНИЙ ВІД ОРИГІНАЛУ. Мапа `І→I, і→i` не вміє відрізнити
    літеру в моделі від літери у звичайному слові, тому «і» з описової частини назви
    просочується в ключ латинською:

        "Вакуумні пакети Siemens HZ66D910"  →  "isiemenshz66d910"   (а не "siemenshz66d910")

    Це не баг порту — так само поводиться скрипт Артура. Але це ще одна причина,
    чому нормалізована назва в нас — РЕЗЕРВНИЙ ключ, а основний — артикул: ключ залежить
    від того, чи є в описовому хвості назви буква «і», і ламається від переставляння слів.
    """
    s = str(raw or "")

    s = _SPACE_RE.sub(" ", s)  # NBSP / вузькі / ZWSP → звичайний пробіл
    s = _DASH_RE.sub("-", s)  # ‒ – — − → -
    s = _APOSTROPHE_RE.sub("'", s)  # ’ ‘ ` ´ → '
    s = _QUOTE_RE.sub('"', s)  # “ ” → "
    s = _JUNK_RE.sub("", s)  # ® ™ ©
    s = _TIMES_RE.sub("x", s)  # × → x
    s = s.translate(_CYRILLIC_LOOKALIKES)  # І → I, і → i

    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    s = _MULTISPACE_RE.sub(" ", s).strip()

    s = _ALLOWED_RE.sub("", s)  # лишаємо тільки [a-z0-9._\-/ ]
    return s.replace(" ", "")  # і прибираємо ВСІ пробіли


#: Роздільники, по яких обрізаємо назву, щоб дістатись «голої» моделі.
#: Порядок = порядок у скрипті Артура.
_CANDIDATE_SEPARATORS = ("(", "[", ",", "/", " | ", " - ")


def model_candidates(raw: Any) -> list[str]:
    """
    Кандидати для зіставлення: сама назва + обрізки по роздільниках.

    Повертає вже НОРМАЛІЗОВАНІ рядки (бо саме такими є ключі індексу),
    без дублів і без порожніх, у порядку від найточнішого до найкоротшого.

        >>> model_candidates("Bosch PNK6B2P40R (нержавіюча сталь)")
        ['boschpnk6b2p40ri', 'boschpnk6b2p40r']

    Обрізок «спрацьовує» лише тоді, коли роздільник у назві реально є, —
    інакше кандидат збігся б з оригіналом і був би відкинутий як дубль.
    """
    text = str(raw or "")
    variants = [text]
    variants.extend(text.split(sep, 1)[0] for sep in _CANDIDATE_SEPARATORS if sep in text)

    out: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        key = normalize_model(variant)
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


#: Нижче цієї довжини кандидат у фолбек-пошук по підрядку не йде.
#: "lg" як підрядок є в половині каталогу — такий пошук дає або шум, або (частіше)
#: неоднозначність і None. Відсікаємо одразу, щоб не робити O(n) прохід дарма.
MIN_SUBSTRING_LEN = 4


def find_spec_match[T](raw_name: Any, index: Mapping[str, T]) -> T | None:
    """
    Знайти запис у `index` (ключі — вже нормалізовані назви) за назвою з прайсу.

    Дві сходинки, як у скрипта Артура:
      1. **Точний збіг** по будь-якому з кандидатів — від найточнішого до найкоротшого.
      2. **Фолбек**: унікальний збіг по входженню підрядка (в будь-який бік —
         назва в прайсі буває і довша, і коротша за назву в таблиці характеристик).

    ⚠️ Якщо на фолбеку кандидат дає БІЛЬШЕ ОДНОГО збігу — повертаємо `None`. Це навмисно:
    «Bosch PNK6B2P40R» проти «...PNK6B2P40R» і «...PNK6B2P40R2» — дві різні плити, і
    вгадувати, яка з них «та сама», означає тихо приліпити товару чужі габарити й вагу.
    Краще нічого, ніж не те: порожні габарити ловить фільтр адмінки, а чужі — ніхто.
    """
    candidates = model_candidates(raw_name)
    if not candidates or not index:
        return None

    # 1. Точний збіг.
    for cand in candidates:
        hit = index.get(cand)
        if hit is not None:
            return hit

    # 2. Фолбек по входженню підрядка. Перший кандидат, що дав хоч один збіг, — вирішальний.
    for cand in candidates:
        if len(cand) < MIN_SUBSTRING_LEN:
            continue
        matches = [value for key, value in index.items() if cand in key or key in cand]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None  # неоднозначно → свідомо здаємось

    return None
