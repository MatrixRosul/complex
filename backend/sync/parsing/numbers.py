"""
Числа в українській локалі (SYNC.md §4.1).

У прайсі реально зустрічається все одразу:

    "145,00"      кома як десятковий роздільник
    "41,65"       курс USD з комірки E4
    "6 600,00"    NBSP як роздільник тисяч
    "6'600,00"    апостроф як роздільник тисяч
    "27 445,50 ₴" ще й валюта в тій самій клітинці
    "61.5"        крапка — так пише таблиця характеристик
    6600.0        Sheets при UNFORMATTED_VALUE віддає ЧИСЛО, а не рядок

Один парсер має з'їсти все це і не вдавитись.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sync.parsing.normalize import SPACE_CHARS, is_placeholder

__all__ = [
    "NumberParseError",
    "is_ambiguous_thousands",
    "parse_decimal",
    "parse_int",
    "parse_qty",
]


class NumberParseError(ValueError):
    """
    Значення непорожнє, але числом не є ("домовитись", "від 5000").

    Спадкоємець `ValueError` навмисно: SYNC.md §4.1 обіцяє, що викликач ловить
    `ValueError`/`InvalidOperation` і перетворює це на `ROW_INVALID` + `rows_failed++`.
    """


#: Викидається з рядка перед парсингом.
_JUNK_TOKENS = ("₴", "$", "€", "грн", "uah", "usd", "eur")

#: Апострофи як роздільник тисяч ("6'600,00" — зустрічається в експортах з Excel).
_APOSTROPHE_SEPARATORS = "'’‘`´ʼ"


#: УСІ пробіли — роздільник тисяч зникає саме тут.
#:
#: ⚠️ `\s` і `SPACE_CHARS` — РІЗНІ множини, і потрібні обидві. `\s` дає звичайний
#: пробіл ("6 600,00") і NBSP, а `SPACE_CHARS` — символи нульової ширини (ZWSP, BOM),
#: які `\s` НЕ матчить, бо вони формально не whitespace.
#:
#: ⚠️ Писати пробіл ЛІТЕРАЛОМ у списку токенів не можна: NBSP і ASCII-пробіл у коді
#: виглядають ОДНАКОВО. Саме на цьому парсер уже спіймався — у списку замість пробілу
#: лежав U+00A0, і "6 600,00" (звичайний пробіл) падав з NumberParseError.
_SPACE_RE = re.compile(r"\s|[" + re.escape(SPACE_CHARS) + r"]")


def _clean(raw: Any) -> str:
    s = str(raw).strip().casefold()
    for token in _JUNK_TOKENS:
        s = s.replace(token, "")
    s = _SPACE_RE.sub("", s)
    for ch in _APOSTROPHE_SEPARATORS:
        s = s.replace(ch, "")
    return s.strip()


def parse_decimal(raw: Any) -> Decimal | None:
    """
    Число в українській локалі → `Decimal`. Порожньо / плейсхолдер / "—" → `None`.

        >>> parse_decimal("6 600,00")      # NBSP-роздільник тисяч
        Decimal('6600.00')
        >>> parse_decimal("41,65")
        Decimal('41.65')
        >>> parse_decimal("145.00")
        Decimal('145.00')
        >>> parse_decimal("—") is None
        True

    ⚠️ **Свідома пастка, яку лишаємо відкритою** (SYNC.md §4.1): `"6,600"` (кома як тисячі,
    US-стиль) стане `Decimal("6.600")`, а не `6600`. Автовизначити тут НЕМОЖЛИВО —
    `"41,65"` виглядає точно так само. Тому локаль зафіксована (кома = десяткова),
    а неоднозначність ловлять три інші сітки: WARN `AMBIGUOUS_NUMBER` (рівно 3 цифри
    після коми), guard #2 по медіанній зміні ціни і snapshot для відкату.

    :raises NumberParseError: значення непорожнє, але не число.
    """
    # Sheets уже віддав число (UNFORMATTED_VALUE) — НЕ чіпаємо, не ганяємо через str-логіку.
    if isinstance(raw, bool):  # bool — підклас int, і True став би Decimal("1"). Ні.
        raise NumberParseError(f"Очікувалось число, отримано bool: {raw!r}")
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int | float):
        return Decimal(str(raw))

    if raw is None or is_placeholder(raw):
        return None

    s = _clean(raw)
    if not s:
        return None

    has_comma, has_dot = "," in s, "." in s
    if has_comma and has_dot:
        # "1.234,56" або "1,234.56" → десятковий той роздільник, що ПРАВІШЕ.
        decimal_sep = "," if s.rfind(",") > s.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        s = s.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif has_comma:
        # Локаль ЗАФІКСОВАНА: кома = десятковий роздільник.
        s = s.replace(",", ".")
    # Тільки крапка ("61.5") → вона й є десятковою: тисячі в цій локалі — пробіл, а не крапка.

    try:
        return Decimal(s)
    except InvalidOperation as exc:
        raise NumberParseError(f"Не число: {raw!r}") from exc


def is_ambiguous_thousands(raw: Any) -> bool:
    """
    `True` для `"6,600"` — рівно 3 цифри після коми і жодної крапки.

    Це кандидат на «кома як роздільник тисяч» (US-стиль). Числом воно розпарситься
    (у `6.600`), але результат майже напевно занижений у 1000 разів → `WARN AMBIGUOUS_NUMBER`.
    `"145,00"` (2 цифри) і `"41,65"` під це НЕ підпадають.
    """
    if isinstance(raw, int | float | Decimal):
        return False
    s = _clean(raw)
    if "." in s or s.count(",") != 1:
        return False
    _, _, frac = s.partition(",")
    return len(frac) == 3 and frac.isdigit()


def parse_int(raw: Any) -> int | None:
    """
    Ціле число. `"2,00"` → `2`, `2.0` → `2`. Порожньо → `None`.

    :raises NumberParseError: не число АБО дробове (`"1,5"` як кількість — це зіпсовані
        дані, а не привід тихо округлити: `int(0.5) == 0` перетворив би товар
        на «немає в наявності»).
    """
    value = parse_decimal(raw)
    if value is None:
        return None
    if value != value.to_integral_value():
        raise NumberParseError(f"Очікувалось ціле, отримано дробове: {raw!r}")
    return int(value)


#: Одиниці, які менеджер може дописати просто в клітинку кількості.
_QTY_TOKENS = ("шт.", "шт", "од.", "од", "pcs", "ks")


def parse_qty(raw: Any) -> int | None:
    """
    Кількість з прайсу. **Порожньо → `None`, а НЕ `0`.** Це не стилістика, це критично.

    `None` і `0` означають ПРОТИЛЕЖНІ речі (SYNC.md §9, крок 9):

        qty is None  →  «кількість невідома» → наявність визначає САМ ФАКТ присутності
                        рядка в прайсі → `source.default_availability` (тобто товар Є)
        qty <= 0     →  «кількості немає»    → `OUT_OF_STOCK` (товару НЕМА)

    Клієнтський лист UAH взагалі не має колонки «К-сть» (INPUTS.md §3.1). Якби порожнє
    поле давало `0`, увесь прайс постачальника миттєво став би «Немає в наявності» —
    рівно той сценарій, який ТЗ просить закласти на майбутнє, ламався б першим.

        >>> parse_qty("2,00")
        2
        >>> parse_qty("") is None
        True
        >>> parse_qty("0")
        0

    :raises NumberParseError: сміття в клітинці (ловить `classify_row` → WARN, `qty=None`).
    """
    return parse_int(_strip_qty_units(raw))


def _strip_qty_units(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    s = raw.strip().casefold()
    for token in _QTY_TOKENS:
        if s.endswith(token):
            return s[: -len(token)].strip()
    return raw
