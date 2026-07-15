"""Фікстури для тестів фіда.

Будуємо мінімальний, але РЕАЛЬНИЙ зріз: рубрикатор Hotline → наша категорія з FK на нього →
товар з характеристиками, брендом, фото і гарантією.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from catalog.enums import Availability, Condition
from catalog.models import (
    Attribute,
    AttributeGroup,
    Brand,
    Category,
    Country,
    Product,
    Unit,
)
from core.models import SiteSettings
from feeds.models import HotlineCategory

SITE_URL = "https://complex.example"


@pytest.fixture(autouse=True)
def _site_url(settings):
    """Фід вимагає HTTPS і абсолютних URL. Локальний дефолт (http://localhost:3000) не годиться."""
    settings.SITE_URL = SITE_URL
    return SITE_URL


@pytest.fixture
def feed_dir(settings, tmp_path):
    """Фід пишемо в tmp_path, щоб тести не топтали media/ розробника."""
    settings.HOTLINE_FEED_DIR = str(tmp_path / "feeds")
    return tmp_path / "feeds"


@pytest.fixture
def site(db) -> SiteSettings:
    SiteSettings.objects.all().delete()
    SiteSettings.invalidate()
    obj = SiteSettings.objects.create(
        pk=1,
        hotline_enabled=True,
        hotline_firm_id="12345",
        hotline_firm_name="Complex",
        installment_provider="Privatbank",
        installment_min_payment=Decimal("500"),
        installment_max_period=12,
    )
    SiteSettings.invalidate()
    yield obj
    SiteSettings.invalidate()


@pytest.fixture
def hotline_tree(db) -> dict[str, HotlineCategory]:
    """Гілка рубрикатора: Побутова техніка → Велика побутова техніка → Холодильники."""
    root = HotlineCategory.objects.create(path="Побутова техніка", name="Побутова техніка", depth=0)
    mid = HotlineCategory.objects.create(
        path="Побутова техніка/Велика побутова техніка",
        name="Велика побутова техніка",
        parent=root,
        depth=1,
    )
    leaf = HotlineCategory.objects.create(
        path="Побутова техніка/Велика побутова техніка/Холодильники",
        name="Холодильники",
        parent=mid,
        depth=2,
        is_leaf=True,
    )
    return {"root": root, "mid": mid, "leaf": leaf}


@pytest.fixture
def category(db, hotline_tree) -> Category:
    return Category.objects.create(
        external_id="5609730",
        name="Холодильники",
        slug="holodylnyky",
        hotline_category=hotline_tree["leaf"],
    )


@pytest.fixture
def brand(db) -> Brand:
    return Brand.objects.create(name="Gorenje", slug="gorenje")


@pytest.fixture
def country(db) -> Country:
    return Country.objects.create(code="RS", name="Сербія", slug="serbiia")


@pytest.fixture
def attributes(db) -> dict[str, Attribute]:
    group = AttributeGroup.objects.create(code="main", name="Основні", sort_order=1)
    mm = Unit.objects.create(code="mm", name="мм")
    month = Unit.objects.create(code="month", name="міс")

    height = Attribute.objects.create(
        code="vysota",
        name="Висота",
        unit=mm,
        group=group,
        value_type=Attribute.ValueType.NUMBER,
        # Назва в каталозі Hotline відрізняється від нашої → мапимо явно, інакше параметр
        # не змержиться з карткою.
        hotline_param_name="Висота",
    )
    warranty = Attribute.objects.create(
        code="garantiia",
        name="Гарантійний термін",
        unit=month,
        group=group,
        value_type=Attribute.ValueType.NUMBER,
        role=Attribute.Role.WARRANTY_MONTHS,
    )
    return {"height": height, "warranty": warranty}


def make_product(
    *,
    category: Category,
    brand: Brand | None,
    country: Country | None = None,
    sku: str = "CMPX-1",
    name: str = "NRK6202",
    mpn: str = "",
    price: Decimal = Decimal("23600.00"),
    availability: str = Availability.IN_STOCK,
    warranty_months: int | None = 12,
    image: str = "https://cdn.complex.example/p/gorenje-nrk6202-1.jpg",
    hotline_enabled: bool = True,
    is_active: bool = True,
    condition: int = Condition.NEW,
    installment_available: bool = False,
    specs: list[dict] | None = None,
    **extra,
) -> Product:
    product = Product(
        sku=sku,
        mpn=mpn,
        name=name,
        slug=sku.lower(),
        category=category,
        brand=brand,
        country=country,
        base_price=price,
        price=price,
        availability=availability,
        order_lead_days=3 if availability == Availability.ON_ORDER else None,
        warranty_months=warranty_months,
        main_image_url=image,
        hotline_enabled=hotline_enabled,
        is_active=is_active,
        condition=condition,
        installment_available=installment_available,
        **extra,
    )
    product.specs_json_uk = specs if specs is not None else []
    product.save()
    return product


@pytest.fixture
def product(db, category, brand, country, attributes) -> Product:
    return make_product(
        category=category,
        brand=brand,
        country=country,
        specs=[
            {
                "g": "Основні",
                "gs": 1,
                "code": "vysota",
                "n": "Висота",
                "u": "мм",
                "v": "1635",
                "vn": 1635.0,
                "s": 10,
            }
        ],
    )
