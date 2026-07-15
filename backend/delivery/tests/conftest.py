"""Спільні фікстури тестів доставки.

Мережі тут немає ніде: `NovaPoshtaClient` без ключа працює на JSON-фікстурах
`delivery/fixtures/np/`, а там, де треба зімітувати падіння НП, підставляється
httpx-транспорт-заглушка.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from django.core.cache import cache

from catalog.models import Category, Product
from core.models import SiteSettings
from delivery.client import CircuitBreaker, NovaPoshtaClient, Timeouts, reset_clients
from delivery.tasks import sync_np_refs

UZHHOROD_SETTLEMENT_REF = "e71f4773-4b33-11e4-ab6d-005056801329"
KYIV_SETTLEMENT_REF = "e715719e-4b33-11e4-ab6d-005056801329"
KYIV_CITY_REF = "8d5a980d-391c-11dd-90d9-001a92567626"

UZH_POSTOMAT_REF = "b1c4bdf9-3b98-11ed-a60f-48df37b921db"
UZH_BRANCH_CARGO_REF = "16006cee-e1c2-11e3-8c4a-0050568002cf"
UZH_BRANCH_30KG_REF = "1ec09d88-e1c2-11e3-8c4a-0050568002cf"


@pytest.fixture(autouse=True)
def _clean_cache():
    """Redis db1 спільний з dev-процесом → чистимо до і після кожного тесту.

    Інакше circuit breaker, відкритий одним тестом, «протікає» в наступний, а кеш
    quote з попереднього прогону робить тести залежними від порядку.
    """
    cache.clear()
    reset_clients()
    yield
    cache.clear()
    reset_clients()


# ---------------------------------------------------------------------------
# Клієнти НП
# ---------------------------------------------------------------------------


@pytest.fixture
def np_client() -> NovaPoshtaClient:
    """Фікстурний клієнт: без ключа, без мережі."""
    return NovaPoshtaClient(api_key="", force_fixtures=True)


@pytest.fixture
def breaker() -> CircuitBreaker:
    b = CircuitBreaker("np-test")
    b.reset()
    return b


def make_failing_client(breaker: CircuitBreaker, exc: Exception | None = None) -> NovaPoshtaClient:
    """Клієнт, у якого КОЖЕН похід у НП падає таймаутом. Мережі не торкається."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc or httpx.ReadTimeout("НП не відповідає", request=request)

    return NovaPoshtaClient(
        api_key="test-key",
        timeouts=Timeouts(connect=0.1, read=0.1, retries=0),
        breaker=breaker,
        transport=httpx.MockTransport(handler),
    )


def make_json_client(payload: dict, breaker: CircuitBreaker | None = None) -> NovaPoshtaClient:
    """Клієнт, який отримує заданий JSON з HTTP 200 (як робить справжня НП)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return NovaPoshtaClient(
        api_key="test-key",
        timeouts=Timeouts(connect=1, read=1, retries=0),
        breaker=breaker,
        transport=httpx.MockTransport(handler),
    )


# ---------------------------------------------------------------------------
# Каталог
# ---------------------------------------------------------------------------


@pytest.fixture
def site_settings(db) -> SiteSettings:
    SiteSettings.invalidate()
    s = SiteSettings.get_solo()
    s.default_weight_kg = Decimal("5")
    s.default_width_cm = Decimal("40")
    s.default_height_cm = Decimal("40")
    s.default_depth_cm = Decimal("40")
    s.save()
    SiteSettings.invalidate()
    return s


@pytest.fixture
def category(db) -> Category:
    return Category.objects.create(
        external_id="5609730",
        name="Холодильники",
        slug="kholodylnyky",
        path="5609730",
        depth=0,
    )


@pytest.fixture
def fridge(category: Category, site_settings: SiteSettings) -> Product:
    """Реальний холодильник з INPUTS §3.4 / INTEGRATIONS §1.7.

    Упаковка 171,5 × 56 × 35 см, 61,5 кг. Об'єм 0.33614 м³ → об'ємна вага 84.035 кг,
    тобто ціна рахується від ОБ'ЄМУ, а не від ваги.
    """
    return Product.objects.create(
        sku="2400042",
        name="Холодильник Gorenje NRK6202AXL4",
        slug="gorenje-nrk6202axl4",
        category=category,
        base_price=Decimal("23600"),
        price=Decimal("23600"),
        package_height_cm=Decimal("171.5"),
        package_width_cm=Decimal("56"),
        package_depth_cm=Decimal("35"),
        package_weight_kg=Decimal("61.5"),
        package_dims_source="sheet",
    )


@pytest.fixture
def np_box(category: Category, site_settings: SiteSettings) -> Product:
    """Товар з габаритами рівно з живого заміру НП: 50×50×60 см, 2 кг, Cost=1500.

    Це той самий запит, на якому в INTEGRATIONS §1.7 заміряно 97.5 грн БЕЗ габаритів
    проти 539.5 грн з габаритами.
    """
    return Product.objects.create(
        sku="NP-TEST-2KG",
        name="Тестова коробка 50×50×60",
        slug="test-box",
        category=category,
        base_price=Decimal("1500"),
        price=Decimal("1500"),
        package_height_cm=Decimal("50"),
        package_width_cm=Decimal("50"),
        package_depth_cm=Decimal("60"),
        package_weight_kg=Decimal("2"),
        package_dims_source="sheet",
    )


@pytest.fixture
def small_item(category: Category, site_settings: SiteSettings) -> Product:
    """Дрібнота, яка ВЛАЗИТЬ у поштомат (комірка 40×30×60, до 20 кг)."""
    return Product.objects.create(
        sku="KETTLE-1",
        name="Чайник Bosch TWK3P420",
        slug="bosch-twk3p420",
        category=category,
        base_price=Decimal("1200"),
        price=Decimal("1200"),
        package_height_cm=Decimal("25"),
        package_width_cm=Decimal("22"),
        package_depth_cm=Decimal("30"),
        package_weight_kg=Decimal("1.5"),
        package_dims_source="sheet",
    )


# ---------------------------------------------------------------------------
# Довідники НП, залиті з фікстур
# ---------------------------------------------------------------------------


@pytest.fixture
def np_refs(db, np_client: NovaPoshtaClient) -> dict:
    """Проганяє справжній sync_np_refs на фікстурах — тести їдуть на РЕАЛЬНОМУ довіднику."""
    return sync_np_refs(client=np_client)
