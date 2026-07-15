"""🔴 Найважливіший тест набору: асистент не має чим вигадувати характеристики.

Стан бази замовника — не «трохи порожньо», а реально порожньо: у 1290 з 1578
товарів рівно одна характеристика (зазвичай бренд). Якщо `get_product`
поверне моделі бодай натяк на неіснуючі specs — модель їх «допоможе» переказати,
і покупець отримає вигадану діагональ чи об'єм. Тому інструмент мусить віддавати
порожнечу як порожнечу.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from assistant import tools

pytestmark = pytest.mark.django_db


def _payload(outcome: tools.ToolOutcome) -> dict[str, Any]:
    """Витягти JSON із обгортки <product_data>…</product_data>."""
    content = outcome["content"]
    start = content.index("{")
    end = content.rindex("}") + 1
    return json.loads(content[start:end])


def test_product_without_specs_returns_empty_specs(product_without_specs: Any) -> None:
    """Товар без характеристик → specs порожні, а не вигадані."""
    outcome = tools.dispatch("get_product", {"id": product_without_specs.pk}, "uk")

    assert outcome["is_error"] is False
    payload = _payload(outcome)
    # specs може бути [] або відсутнім — головне, що ЖОДНОГО рядка характеристики немає.
    assert not payload.get("specs"), f"інструмент вигадав характеристики: {payload.get('specs')}"


def test_product_with_specs_returns_only_real_rows(product_with_specs: Any) -> None:
    """Товар З характеристиками → рівно ті, що в БД, не більше.

    Форма з get_product: specs = [{"group": ..., "rows": [{"name", "value"}, ...]}].
    """
    outcome = tools.dispatch("get_product", {"id": product_with_specs.pk}, "uk")

    payload = _payload(outcome)
    rows = [row for group in (payload.get("specs") or []) for row in group["rows"]]
    # У фікстурі рівно 3 характеристики (Загальний об'єм, Висота, No Frost).
    assert len(rows) == 3
    names = {row["name"] for row in rows}
    assert names == {"Загальний об'єм", "Висота", "No Frost"}
    # Кожен рядок має і назву, і значення — жодного «порожнього» вигаданого поля.
    assert all(row["name"] and row["value"] for row in rows)
