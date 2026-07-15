"""Enum-и каталогу.

Винесені з models.py навмисно: їх імпортують sync (PriceSource, SupplierOffer) і orders.
Імпорт з `catalog.enums` не тягне за собою моделі → нуль циклічних імпортів.
`catalog.models` реекспортує ці ж імена, тому обидва шляхи імпорту робочі.
"""

from __future__ import annotations

from django.db import models


class Availability(models.TextChoices):
    IN_STOCK = "in_stock", "В наявності"
    ON_ORDER = "on_order", "Під замовлення"
    OUT_OF_STOCK = "out_of_stock", "Немає в наявності"


class SourceCurrency(models.TextChoices):
    UAH = "UAH", "Гривня"
    USD = "USD", "Долар США"


class ProductSource(models.TextChoices):
    SHEET = "sheet", "Google Sheets"
    MANUAL = "manual", "Створено вручну"
    AI = "ai", "ШІ-імпорт"


class Condition(models.IntegerChoices):
    """Сумісно з тегом <condition> у фіді Hotline (ADR-013).

    ⚠️ Уцінка — це ОЗНАКА ТОВАРУ, а не місце в дереві категорій (INPUTS §5 Q6):
    уцінений холодильник лишається в «Холодильники» з усіма фільтрами (об'єм, No Frost),
    а сторінка «Уцінка» будується як Category.virtual_tokens=["cond:2"] — нуль дублювання.
    """

    NEW = 0, "Новий"
    REFURBISHED = 1, "Відновлений"
    DISCOUNTED = 2, "Уцінений"
    USED = 3, "Б/в"
