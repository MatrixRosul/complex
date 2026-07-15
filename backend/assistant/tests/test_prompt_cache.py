"""Детермінізм системного блоку — передумова роботи prompt cache.

Кеш Anthropic — префіксний: будь-яка зміна байта в префіксі інвалідує весь кеш
після нього. Якщо `build_system` вставить туди `datetime.now()`, uuid чи
проітерує `set`, кеш МОВЧКИ не спрацює (`cache_creation_input_tokens = 0`, без
помилки) — і кожне повідомлення платитиме повну ціну за префікс.

Цей тест ловить «тихий інвалідатор»: два виклики мусять дати БАЙТ-У-БАЙТ однаковий
результат.
"""

from __future__ import annotations

import pytest

from assistant import prompts

pytestmark = pytest.mark.django_db


@pytest.fixture
def _catalog(category: object, product_with_specs: object) -> None:
    """Системний блок вбудовує дерево категорій і словник фасетів — вони мають існувати."""


@pytest.mark.usefixtures("_catalog")
def test_build_system_is_byte_stable_uk() -> None:
    """Два виклики build_system('uk') → ідентичні до байта (інакше кеш не працює)."""
    a = prompts.build_system("uk")
    b = prompts.build_system("uk")
    assert a == b


@pytest.mark.usefixtures("_catalog")
def test_build_system_is_byte_stable_ru() -> None:
    """Те саме для RU — друга жива мова, окремий кеш-ключ."""
    a = prompts.build_system("ru")
    b = prompts.build_system("ru")
    assert a == b


@pytest.mark.usefixtures("_catalog")
def test_cacheable_flag_toggles_cache_control() -> None:
    """cacheable=False прибирає cache_control (гейт вирішив, що префікс закороткий для кешу)."""
    with_cache = prompts.build_system("uk", cacheable=True)
    without = prompts.build_system("uk", cacheable=False)

    def has_cache_control(blocks: list[dict[str, object]]) -> bool:
        return any("cache_control" in block for block in blocks)

    assert has_cache_control(with_cache) is True
    assert has_cache_control(without) is False
    # Текст блоків — той самий; різниця лише в наявності breakpoint'а.
    assert [b.get("text") for b in with_cache] == [b.get("text") for b in without]
