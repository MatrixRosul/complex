"""Нормалізація назви моделі — резервний ключ зіставлення прайсу з характеристиками.

INPUTS §3.5. Основний ключ у нас — АРТИКУЛ (він є в обох таблицях). Але скрипт Артура
(Google Apps Script) зшивав таблиці по НАЗВІ, і в даних лишились рядки з порожнім або
битим артикулом → потрібен фолбек по нормалізованій назві.

Логіка нормалізації — порт скрипта Артура 1:1. Вона вистраждана на реальних даних;
міняти її «щоб було красивіше» не можна: зміна алгоритму = розсинхрон з тим, що вже
збережено в Product.name_normalized.
"""

from __future__ import annotations

import re
import unicodedata

# Усі варіанти тире, які реально трапляються в таблицях, → звичайний дефіс.
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
# Усі варіанти апострофів → прямий.
_APOSTROPHES = dict.fromkeys(map(ord, "’‘`´"), "'")
# Знак множення з габаритів («171,5 × 56 × 35») → латинська x.
_TIMES = {ord("×"): "x"}

# ⚠️ КЛАСИЧНА ПАСТКА: кирилична І/і/Ї/ї у латинських назвах брендів («Sіemens» з
#    кириличною «і» — реальний випадок у прайсі). Без цієї заміни той самий товар з
#    двох таблиць дає два різні ключі і не зшивається ніколи.
_CYRILLIC_I = {
    ord("І"): "I",
    ord("і"): "i",
    ord("Ї"): "I",
    ord("ї"): "i",
}

_ALLOWED = re.compile(r"[^a-z0-9._\-/]")

_TRANSLATION = {**_DASHES, **_APOSTROPHES, **_TIMES, **_CYRILLIC_I}

# --- транслітерація для slug'ів (uk/ru → латиниця) ---------------------------
# ⚠️ django.utils.text.slugify на кирилиці дає ПОРОЖНІЙ рядок — тому для назв категорій
#    («Аудіо, відео техніка») потрібна саме транслітерація, а не slugify.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie",
    "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "",
    "ю": "iu", "я": "ia", "ы": "y", "э": "e", "ё": "e", "ъ": "",
    "'": "", "’": "", "`": "",
}  # fmt: skip


def translit_slug(text: str, max_len: int = 180) -> str:
    """`«Аудіо, відео техніка»` → `audio-video-tekhnika`. Кирилиця → латиниця.

    Єдине джерело правди для слагів, які будуються з українських назв (категорії з
    довідника, бренди, атрибути). `slugify()` тут не годиться: він викидає кирилицю
    цілком і повертає "" — а slug у нас NOT NULL.
    """
    out: list[str] = []
    for ch in text.lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum() and ch.isascii():
            out.append(ch)
        else:
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:max_len].strip("-")


def normalize_model_name(name: str | None) -> str:
    """`Холодильник Gorenje NRK‑6202 CLI` → `холодильникgorenjenrk-6202cli` → `gorenjenrk-6202cli`.

    Кроки (порядок важливий):
      NBSP → пробіл; усі тире → "-"; апострофи → "'"; "×" → "x";
      unicodedata.normalize("NFKC"); .lower();
      кирилична І/і/Ї/ї → латинські I/i;
      викинути все, крім [a-z0-9._\\-/]  (кирилиця зникає тут же);
      прибрати пробіли.
    """
    if not name:
        return ""

    s = name.replace(" ", " ")
    s = s.translate(_TRANSLATION)
    s = unicodedata.normalize("NFKC", s)
    # Заміну кириличних І/і робимо ДО lower() і ПІСЛЯ нього: NFKC не чіпає кирилицю,
    # а великі «І» мають стати «i», а не зникнути.
    s = s.translate(_CYRILLIC_I)
    s = s.lower()
    s = s.translate(_CYRILLIC_I)
    s = _ALLOWED.sub("", s)
    return s.strip()
