"""
Характеристики: трійки з колонки J, габарити упаковки, гарантія (INPUTS.md §3.3–3.4).

Дві речі тут коштують грошей, якщо їх зробити неправильно:

1. **Одиниця клеїться до ЗНАЧЕННЯ, а не до назви** (INPUTS.md §2).
   ПРАВИЛЬНО:   "Висота"      →  "284 мм"
   НЕПРАВИЛЬНО: "Висота (мм)" →  "284"
   Замовник просить зберегти формат galiton: таблиця «назва зліва, значення справа»,
   і читатись вона має як «Висота: 284 мм». Одиниця в назві ще й ламає фільтри —
   «Висота (мм)» і «Висота (см)» стали б ДВОМА різними атрибутами.

2. **Габарити упаковки — вхід калькулятора Нової Пошти** (INPUTS.md §3.4).
   Беремо `Габарити упаковки (ВхШхГ) (см)` і `Вага в упаковці (кг)`, а НЕ габарити
   й вагу самого товару. Різниця не косметична: `Вага (кг): 58` проти
   `Вага в упаковці (кг): 61.5`. Без габаритів об'ємна вага не рахується взагалі,
   і доставка занижується в рази — на КОЖНІЙ посилці.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NamedTuple

from sync.parsing.normalize import collapse_spaces, is_placeholder
from sync.parsing.numbers import NumberParseError, parse_decimal

__all__ = [
    "PackageDims",
    "PackageInfo",
    "Spec",
    "extract_package_dims_and_weight",
    "format_spec",
    "format_spec_line",
    "format_spec_value",
    "parse_package_dims",
    "parse_spec_triples",
    "parse_warranty",
]

#: Трійки починаються з колонки J = 10-та (1-based). Це ПОЗИЦІЙНА константа, а не пошукова:
#: заголовки трійок повторюються, шукати їх за іменем не можна (SYNC.md §1.5.3).
SPEC_TRIPLET_START_COL = 10


@dataclass(frozen=True, slots=True)
class Spec:
    name: str
    unit: str
    value: str


class PackageDims(NamedTuple):
    """Габарити упаковки, см. Порядок — як у таблиці: **ВхШхГ**."""

    height_cm: Decimal
    width_cm: Decimal
    depth_cm: Decimal


@dataclass(frozen=True, slots=True)
class PackageInfo:
    """Те, що йде в калькулятор Нової Пошти."""

    height_cm: Decimal | None = None
    width_cm: Decimal | None = None
    depth_cm: Decimal | None = None
    weight_kg: Decimal | None = None
    #: `True` → вагу взяли з `Вага (кг)` (вага ТОВАРУ), бо `Вага в упаковці` не заповнена.
    #: Це заниження: реальна посилка важча. Викликач має логувати WARN.
    weight_is_fallback: bool = False

    @property
    def has_dims(self) -> bool:
        return None not in (self.height_cm, self.width_cm, self.depth_cm)

    @property
    def is_complete(self) -> bool:
        return self.has_dims and self.weight_kg is not None


# ---------------------------------------------------------------------------
# 1. Трійки Назва / Одиниця / Значення
# ---------------------------------------------------------------------------


def parse_spec_triples(
    row: Sequence[Any],
    start_col: int = SPEC_TRIPLET_START_COL,
    *,
    stop_on_empty_name: bool = True,
) -> list[Spec]:
    """
    Розібрати повторювані трійки `Назва` / `Одиниця` / `Значення`, починаючи з колонки J.

    `start_col` — **1-based** номер колонки (J = 10), як у `SpecSheet.spec_triplet_start_col`.

        >>> row = [""] * 9 + ["Колір виробу", "", "Слонова кістка", "Висота", "мм", "284"]
        >>> parse_spec_triples(row)
        [Spec(name='Колір виробу', unit='', value='Слонова кістка'), Spec(name='Висота', unit='мм', value='284')]

    `stop_on_empty_name=True` (дефолт) — поведінка зі SYNC.md §6.2: зупиняємось на першій
    порожній назві. `False` — пропускаємо дірку і читаємо рядок до кінця (корисно, якщо
    в таблиці колись з'явиться порожня трійка посередині: дефолт у такому разі мовчки
    відрізав би весь хвіст характеристик).

    Трійка з порожнім ЗНАЧЕННЯМ (або значенням-плейсхолдером «Уточнюється») пропускається,
    але читання НЕ зупиняє.
    """
    if start_col < 1:
        raise ValueError(f"start_col має бути 1-based (>=1), отримано {start_col}")

    out: list[Spec] = []
    idx = start_col - 1

    while idx < len(row):
        name = collapse_spaces(_at(row, idx))
        unit = collapse_spaces(_at(row, idx + 1))
        value = collapse_spaces(_at(row, idx + 2))
        idx += 3

        if not name:
            if stop_on_empty_name:
                break
            continue

        if is_placeholder(value):
            continue  # характеристика є, значення ще не заповнили — не характеристика

        out.append(Spec(name=name, unit="" if is_placeholder(unit) else unit, value=value))

    return out


def _at(row: Sequence[Any], idx: int) -> Any:
    return row[idx] if idx < len(row) else ""


# ---------------------------------------------------------------------------
# 2. Рендер: одиниця клеїться до ЗНАЧЕННЯ
# ---------------------------------------------------------------------------


def format_spec_value(unit: Any, value: Any) -> str:
    """
    `("мм", "284")` → `"284 мм"`. Одиниця йде до ЗНАЧЕННЯ (INPUTS.md §2).

    Якщо одиниця вже присутня у значенні («284 мм»), другий раз не клеїмо.
    """
    unit_s = "" if is_placeholder(unit) else collapse_spaces(unit)
    value_s = collapse_spaces(value)
    if not unit_s or not value_s:
        return value_s
    if value_s.casefold().endswith(unit_s.casefold()):
        return value_s
    return f"{value_s} {unit_s}"


def format_spec(name: Any, unit: Any, value: Any) -> tuple[str, str]:
    """
    `("Висота", "мм", "284")` → `("Висота", "284 мм")`.

    Пара (назва, значення) — рівно те, що рендериться таблицею: назва зліва, значення справа.

    ⚠️ НЕ `("Висота (мм)", "284")`. Скрипт Артура клеїв одиницю до назви
    (`Назва (одиниця): значення`) — саме це ми свідомо НЕ переносимо.
    """
    return collapse_spaces(name), format_spec_value(unit, value)


def format_spec_line(name: Any, unit: Any, value: Any) -> str:
    """`("Висота", "мм", "284")` → `"Висота: 284 мм"` — для плоского тексту (фід, дебаг)."""
    label, rendered = format_spec(name, unit, value)
    return f"{label}: {rendered}"


# ---------------------------------------------------------------------------
# 3. Габарити упаковки
# ---------------------------------------------------------------------------

#: Роздільник габаритів: латинська x, КИРИЛИЧНА х (візуальний двійник!), *, ×.
_DIM_SEPARATOR_RE = re.compile(r"\s*[xх*×✕]\s*", re.IGNORECASE)

#: Хвости, які менеджер дописує в ту саму клітинку.
_DIM_JUNK_RE = re.compile(r"\b(см|мм|cm|mm)\b\.?", re.IGNORECASE)


def parse_package_dims(raw: Any) -> PackageDims | None:
    """
    `"171,5 x 56 x 35"` → `PackageDims(Decimal("171.5"), Decimal("56"), Decimal("35"))`.

    Формат **ВхШхГ** (висота x ширина x глибина). Обробляє:
      * роздільник `x` / кириличну `х` / `*` / `×`;
      * десяткову кому (`171,5`) і крапку (`171.5`);
      * зайві пробіли й хвіст одиниць (`"60×65×185 см"`);
      * відсутність значення → `None`.

        >>> parse_package_dims("171,5 x 56 x 35")
        PackageDims(height_cm=Decimal('171.5'), width_cm=Decimal('56'), depth_cm=Decimal('35'))
        >>> parse_package_dims("уточнюється") is None
        True

    Одиниці НЕ конвертуються: число повертається як є. Колонка-джерело оголошує сантиметри
    («Габарити упаковки (ВхШхГ) **(см)**»), і мовчки вгадувати «це, мабуть, міліметри»
    означало б наздогад ділити чужу посилку на 10.
    """
    if is_placeholder(raw):
        return None

    text = _DIM_JUNK_RE.sub("", collapse_spaces(raw)).strip()
    if not text:
        return None

    parts = [p for p in _DIM_SEPARATOR_RE.split(text) if p.strip()]
    if len(parts) != 3:
        return None

    try:
        values = [parse_decimal(p) for p in parts]
    except NumberParseError:
        return None

    if any(v is None or v <= 0 for v in values):
        return None

    return PackageDims(*values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4. Витяг габаритів / ваги / гарантії з характеристик
# ---------------------------------------------------------------------------

#: Для пошуку характеристики за назвою: лишаємо тільки літери, casefold.
#: «Габарити упаковки (ВхШхГ) (см)» і «Габарити упаковки, см» дають один ключ.
_LETTERS_RE = re.compile(r"[^a-zа-яґєії]+", re.IGNORECASE)


def _key(name: Any) -> str:
    return _LETTERS_RE.sub("", collapse_spaces(name).casefold())


def _is_dims(spec: Spec) -> bool:
    k = _key(spec.name)
    # «Габарити упаковки (ВхШхГ)» / «Розміри в упаковці» — обидва варіанти реальні.
    return ("габарит" in k or "розмір" in k or "розмiр" in k) and "упаков" in k


def _is_package_weight(spec: Spec) -> bool:
    k = _key(spec.name)
    return "вага" in k and "упаков" in k


def _is_item_weight(spec: Spec) -> bool:
    k = _key(spec.name)
    return "вага" in k and "упаков" not in k


def _is_warranty(spec: Spec) -> bool:
    return "гарант" in _key(spec.name)


def extract_package_dims_and_weight(specs: Iterable[Spec]) -> PackageInfo:
    """
    Витягти з характеристик усе, що потрібно калькулятору Нової Пошти.

    Шукає `Габарити упаковки (ВхШхГ) (см)` і `Вага в упаковці (кг)`.
    Якщо ваги в упаковці немає — **фолбек на `Вага (кг)`** з прапорцем
    `weight_is_fallback=True`.

    ⚠️ Фолбек — це ЗАНИЖЕННЯ, і воно позначене свідомо. `Вага (кг): 58` проти
    `Вага в упаковці (кг): 61.5` — 3.5 кг різниці на кожній посилці, які оплатимо ми.
    Прапорець існує рівно для того, щоб викликач міг це залогувати, а не щоб мовчки
    вважати, ніби вагу знайшли.

        >>> specs = [Spec("Габарити упаковки (ВхШхГ)", "см", "171,5 x 56 x 35"),
        ...          Spec("Вага в упаковці", "кг", "61.5")]
        >>> info = extract_package_dims_and_weight(specs)
        >>> info.height_cm, info.weight_kg, info.weight_is_fallback
        (Decimal('171.5'), Decimal('61.5'), False)
    """
    dims: PackageDims | None = None
    package_weight: Decimal | None = None
    item_weight: Decimal | None = None

    for spec in specs:
        if dims is None and _is_dims(spec):
            dims = parse_package_dims(spec.value)
        elif package_weight is None and _is_package_weight(spec):
            package_weight = _safe_decimal(spec.value)
        elif item_weight is None and _is_item_weight(spec):
            item_weight = _safe_decimal(spec.value)

    weight = package_weight if package_weight is not None else item_weight
    if weight is not None and weight <= 0:
        weight = None

    return PackageInfo(
        height_cm=dims.height_cm if dims else None,
        width_cm=dims.width_cm if dims else None,
        depth_cm=dims.depth_cm if dims else None,
        weight_kg=weight,
        weight_is_fallback=weight is not None and package_weight is None,
    )


#: "12", "12 міс", "24 місяці" → перше ціле число.
_FIRST_INT_RE = re.compile(r"\d+")
#: Гарантія в РОКАХ («2 роки», «1 рік», «річна») → множимо на 12.
#: Українське «рік» / «роки» / «років» — це РІЗНІ корені (рік/рок), тому одного «рок» мало.
_YEARS_RE = re.compile(r"\b(?:рік|рок|річ|роч|год|лет|year|yr)", re.IGNORECASE)


def parse_warranty(specs: Iterable[Spec]) -> int | None:
    """
    Гарантія в МІСЯЦЯХ — для тега `<warranty>` у фіді Hotline.

        >>> parse_warranty([Spec("Гарантійний термін", "міс", "12")])
        12
        >>> parse_warranty([Spec("Гарантія", "", "2 роки")])
        24

    Роки конвертуються в місяці. Якщо число не знайшлось — `None` (не помилка:
    Hotline просто не отримає тег, фід від цього не ламається).
    """
    for spec in specs:
        if not _is_warranty(spec):
            continue

        match = _FIRST_INT_RE.search(spec.value)
        if not match:
            continue

        months = int(match.group())
        if months <= 0:
            continue

        haystack = f"{spec.name} {spec.unit} {spec.value}"
        if _YEARS_RE.search(haystack):
            months *= 12

        return months

    return None


def _safe_decimal(raw: Any) -> Decimal | None:
    """Число або None. Сміття в характеристиці рядок не вбиває — це не ціна."""
    try:
        return parse_decimal(_strip_units(raw))
    except NumberParseError:
        return None


_UNIT_TAIL_RE = re.compile(r"\s*(кг|kg|г|g|см|cm|мм|mm|міс\.?|мес\.?)\s*$", re.IGNORECASE)


def _strip_units(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    return _UNIT_TAIL_RE.sub("", raw).strip()
