"""Фікстури checkout'а.

На відміну від `payments/tests`, тут потрібні РЕАЛЬНІ `catalog.Product`: checkout свідомо
рахує суму з БД через `cards.bulk_products()`, і підміняти цей крок моком означало б
тестувати не те, що працює в проді.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from catalog.models import Category, Product


@pytest.fixture(autouse=True)
def checkout_settings(settings):
    settings.SITE_URL = "https://complex.ua"
    # Telegram вимкнений: тести не ходять у мережу. Окремі тести вмикають його самі.
    settings.TELEGRAM_BOT_TOKEN = ""
    settings.TELEGRAM_CHAT_ID = ""
    # ⚠️ Задачі — інлайном. Інакше `.delay()` з checkout'а пише в РЕАЛЬНИЙ локальний
    #    Redis, і запущений воркер розсилав би сповіщення про тестові замовлення
    #    у справжній чат. Тест не має права слати повідомлення живим людям.
    settings.CELERY_TASK_ALWAYS_EAGER = True
    # debug_toolbar падає на reverse('djdt:render_panel') під тестовим клієнтом
    # (той самий обхід, що й у delivery/tests/test_api.py). До замовлень стосунку не має.
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "debug_toolbar" not in m]
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "orders-tests",
        }
    }
    from django.core.cache import cache

    cache.clear()
    return settings


@pytest.fixture
def category(db) -> Category:
    return Category.objects.create(
        external_id="c1",
        name="Холодильники",
        slug="holodylnyky",
        path="c1",
        depth=0,
        is_active=True,
    )


@pytest.fixture
def product_factory(db, category):
    def _make(
        *,
        sku: str = "SKU-1",
        price: str = "15000.00",
        is_active: bool = True,
        installment: bool = True,
    ) -> Product:
        return Product.objects.create(
            sku=sku,
            name=f"Товар {sku}",
            slug=f"tovar-{sku.lower()}",
            category=category,
            base_price=Decimal(price),
            price=Decimal(price),
            is_active=is_active,
            installment_available=installment,
        )

    return _make


@pytest.fixture
def payload_factory():
    """Тіло POST /orders — рівно те, що шле checkout-форма."""
    from uuid import uuid4

    from orders.schemas import CreateOrderIn

    def _make(**overrides: Any) -> CreateOrderIn:
        data: dict[str, Any] = {
            "items": [],
            "expected_total": "0",
            "idempotency_key": str(uuid4()),
            "last_name": "Шевченко",
            "first_name": "Тарас",
            "phone": "0671234567",
            "email": "taras@example.com",
            "comment": "",
            "delivery_method": "np_warehouse",
            "np_city_ref": "city-ref",
            "np_city_name": "Ужгород",
            "np_warehouse_ref": "wh-ref",
            "np_warehouse_name": "Відділення №1",
            "payment_method": "cod",
        }
        data.update(overrides)
        return CreateOrderIn(**data)

    return _make
