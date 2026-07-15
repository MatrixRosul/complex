"""Мова відповіді: `lang ∈ {uk, ru}`, дефолт `uk`.

⚠️ ЧОМУ ЯВНИЙ getattr, А НЕ `translation.activate(lang)`
--------------------------------------------------------
modeltranslation підміняє дескриптор поля і віддає `_ru`/`_uk` за АКТИВНОЮ мовою потоку.
Спокусливо викликати `activate(lang)` і читати `product.name`. Ми цього не робимо, бо:

  1. **Фолбек на порожньому JSON.** `MODELTRANSLATION_FALLBACK_LANGUAGES = ("uk",)` вважає
     значення «порожнім» за списком `fallback_values`, і для `JSONField` порожній список `[]`
     у `specs_json_ru` — це НЕ те саме, що `""` для CharField. Тобто RU-картка з незаповненим
     перекладом характеристик могла б віддати `[]` замість фолбеку на UK — і блок
     «Характеристики» став би порожнім МОВЧКИ (§ARCHITECTURE ADR-003: «Порожнеча користувачу
     не показується ніколи»).
  2. **Стан у потоці.** `activate()` — це thread-local. Під gunicorn з кількома воркерами й
     `CONN_MAX_AGE` забути `deactivate()` = наступний запит у тому ж потоці віддає чужу мову.
     Клас багів, який відтворюється лише під навантаженням.

Тому мова тут — ЯВНИЙ АРГУМЕНТ, а не глобальний стан. `tr()` реалізує фолбек сам:
непорожній `_ru` → `_ru`, інакше `_uk`.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = ["DEFAULT_LANG", "LANGS", "normalize_lang", "tr"]

LANGS: Final[tuple[str, ...]] = ("uk", "ru")
DEFAULT_LANG: Final[str] = "uk"


def normalize_lang(lang: str | None) -> str:
    """`ua` (локаль у URL фронту) → `uk` (код Django). Невідоме → дефолт.

    ADR-003: у URL локаль — `ua`/`ru`, у Django — `uk`/`ru`. Мапінг живе тут, щоб фронт міг
    слати те, що в нього в шляху, і не думати про це.
    """
    if not lang:
        return DEFAULT_LANG
    code = lang.strip().lower()
    if code == "ua":
        return "uk"
    return code if code in LANGS else DEFAULT_LANG


def tr(obj: Any, field: str, lang: str) -> Any:
    """Перекладне поле з фолбеком на UK.

    UK — мова-джерело (DATA_MODEL §0): `_ru` є ПОХІДНИМ і може бути порожнім, доки переклад не
    схвалено. У такому разі показуємо український текст, а НЕ порожнечу.
    """
    if lang != DEFAULT_LANG:
        value = getattr(obj, f"{field}_{lang}", None)
        if value:  # "" / None / [] / {} → фолбек
            return value
    value = getattr(obj, f"{field}_{DEFAULT_LANG}", None)
    if value:
        return value
    # Остання лінія: непатчене поле (напр. виклик до modeltranslation.ready()).
    return getattr(obj, field, None)
