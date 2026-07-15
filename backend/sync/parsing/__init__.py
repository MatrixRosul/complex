"""
Ядро парсингу прайсу — ЧИСТІ функції: ні ORM, ні мережі, ні Django-налаштувань.

Це фундамент синхронізації (SYNC.md §4). Модуль свідомо не імпортує нічого з `django.db`:
його можна ганяти fuzz-тестами й переграти на CSV-снапшоті прогону без бази взагалі.

    normalize  — нормалізація моделі (порт скрипта Артура), кандидати, зіставлення зі SpecSheet
    numbers    — числа в українській локалі ("6 600,00", "41,65", "61.5")
    rows       — класифікація рядка прайсу: DATA / SECTION / SKIP (пастки INPUTS.md §3.2)
    specs      — трійки характеристик, габарити упаковки, гарантія
"""

from sync.parsing.normalize import (
    PLACEHOLDER_VALUES,
    clean_or_empty,
    collapse_spaces,
    find_spec_match,
    is_placeholder,
    model_candidates,
    normalize_model,
)
from sync.parsing.numbers import (
    NumberParseError,
    is_ambiguous_thousands,
    parse_decimal,
    parse_int,
    parse_qty,
)
from sync.parsing.rows import (
    ERROR_REASONS,
    MAX_SANE_PRICE,
    Reason,
    RowKind,
    RowVerdict,
    classify_row,
    is_section_header,
    split_photos,
)
from sync.parsing.specs import (
    SPEC_TRIPLET_START_COL,
    PackageDims,
    PackageInfo,
    Spec,
    extract_package_dims_and_weight,
    format_spec,
    format_spec_line,
    format_spec_value,
    parse_package_dims,
    parse_spec_triples,
    parse_warranty,
)

__all__ = [
    "ERROR_REASONS",
    "MAX_SANE_PRICE",
    "PLACEHOLDER_VALUES",
    "SPEC_TRIPLET_START_COL",
    "NumberParseError",
    "PackageDims",
    "PackageInfo",
    "Reason",
    "RowKind",
    "RowVerdict",
    "Spec",
    "classify_row",
    "clean_or_empty",
    "collapse_spaces",
    "extract_package_dims_and_weight",
    "find_spec_match",
    "format_spec",
    "format_spec_line",
    "format_spec_value",
    "is_ambiguous_thousands",
    "is_placeholder",
    "is_section_header",
    "model_candidates",
    "normalize_model",
    "parse_decimal",
    "parse_int",
    "parse_package_dims",
    "parse_qty",
    "parse_spec_triples",
    "parse_warranty",
    "split_photos",
]
