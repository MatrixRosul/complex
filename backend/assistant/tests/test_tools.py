"""Інструменти віддають РЕАЛЬНІ дані каталогу — і ламаються голосно, а не тихо.

Тут перевіряється не «функція щось повернула», а межа між моделлю і базою:

  1. кожен інструмент дістає дані з фікстур (а не з голови й не з порожнечі);
  2. 🔴 `build_catalog_link` ВІДХИЛЯЄ вигаданий код/значення фасета. Мовчазне ігнорування —
     найгірший з можливих варіантів: модель придумала «brand: siemens», фільтр тихо зник,
     а в чаті з'явився впевнений лінк «ось усі Siemens» — на видачу, де Siemens немає;
  3. нуль результатів — це ВІДПОВІДЬ, а не збій: інструмент віддає порожній список і
     явну note, а не падає й не вигадує товар;
  4. `limit` клампиться МОВЧКИ (це не помилка моделі), а `ids` поза 2..4 — навпаки, is_error:
     на клампі порівняння втратило б рівно той товар, про який питали.

Помилка інструмента ЗАВЖДИ повертається як `is_error=True`, ніколи як виняток: виняток убив би
SSE-стрім посеред відповіді (tools.py::dispatch).
"""

from __future__ import annotations

from typing import Any

import pytest

from assistant import tools
from assistant.tests.conftest import unwrap
from catalog.enums import Availability
from catalog.models import Product

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# search_products
# ---------------------------------------------------------------------------


def test_search_products_returns_real_products_from_db(
    product_with_specs: Product,
    product_without_specs: Product,
) -> None:
    """Пошук віддає товари з БАЗИ: ті самі id, ціни й посилання, що й у каталозі."""
    outcome = tools.dispatch("search_products", {"query": "Холодильник"}, "uk")
    payload = unwrap(outcome)

    assert payload["total"] == 2
    assert payload["shown"] == 2

    by_id = {card["id"]: card for card in payload["products"]}
    assert set(by_id) == {product_with_specs.pk, product_without_specs.pk}

    bosch = by_id[product_with_specs.pk]
    assert bosch["sku"] == "KGN39VLEB"
    assert bosch["name"] == "Холодильник Bosch KGN39VLEB"
    # Ціна — РЯДОК і рівно з двома знаками: модель ціни переказує, а не рахує.
    assert bosch["price"] == "27445.00"
    assert bosch["old_price"] == "31000.00"
    assert bosch["brand"] == "Bosch"
    assert bosch["availability"] == Availability.IN_STOCK
    assert bosch["availability_label"] == "В наявності"
    assert bosch["url"] == f"/uk/p/{product_with_specs.pk}/kgn39vleb"

    # Картки їдуть і фронту (SSE), не лише моделі.
    assert outcome["products"] == payload["products"]
    assert payload["catalog_url"].startswith("/uk/")


def test_search_products_filters_by_brand_and_availability(
    product_with_specs: Product,
    product_without_specs: Product,
) -> None:
    """Фасети звужують видачу по-справжньому (filter_tokens), а не декоративно."""
    payload = unwrap(
        tools.dispatch(
            "search_products",
            {"brand": ["bosch"], "availability": "in_stock"},
            "uk",
        )
    )

    assert payload["total"] == 1
    assert [card["id"] for card in payload["products"]] == [product_with_specs.pk]
    # LG «під замовлення» у видачу не потрапив — саме тому, що фільтр реальний.
    assert product_without_specs.pk not in [card["id"] for card in payload["products"]]


def test_search_products_with_no_results_does_not_crash(product_with_specs: Product) -> None:
    """🔴 Нуль результатів — це відповідь, а не збій.

    Інструмент мусить повернути ПОРОЖНІЙ список і сказати моделі прямо: «не вигадуй товарів».
    Виняток тут обірвав би стрім, а мовчазний порожній payload — розв'язав би моделі руки.
    """
    outcome = tools.dispatch(
        "search_products",
        {"query": "Холодильник", "price_min": 900000},
        "uk",
    )
    payload = unwrap(outcome)

    assert outcome["is_error"] is False
    assert payload["total"] == 0
    assert payload["shown"] == 0
    assert payload["products"] == []
    assert outcome["products"] == []
    assert "не вигадуй" in payload["note"].lower()


def test_search_products_unknown_brand_is_error_not_silent_drop(
    product_with_specs: Product,
) -> None:
    """Вигаданий бренд → гучна помилка. Мовчки зняти фільтр — значить збрехати впевнено."""
    outcome = tools.dispatch("search_products", {"brand": ["siemens"]}, "uk")

    assert outcome["is_error"] is True
    assert "siemens" in outcome["content"].lower()
    assert "get_facets" in outcome["content"]
    assert outcome["products"] == []


def test_search_products_unknown_category_is_error(product_with_specs: Product) -> None:
    outcome = tools.dispatch("search_products", {"category": "kosmichni-korabli"}, "uk")

    assert outcome["is_error"] is True
    assert "kosmichni-korabli" in outcome["content"]


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (99, tools.SEARCH_MAX_LIMIT),  # згори — до стелі
        (0, 1),  # знизу — до одиниці
        (3, 3),  # у межах — як просили
        (None, tools.SEARCH_DEFAULT_LIMIT),  # без limit — дефолт
    ],
)
def test_search_products_limit_is_clamped_silently(
    make_product: Any,
    limit: int | None,
    expected: int,
) -> None:
    """`limit` клампиться МОВЧКИ: модель, яка попросила 99 карток, не помилилась — просто
    стільки в чат не влізе. Помилка тут змусила б її переспрашувати замість відповідати."""
    for i in range(10):
        make_product(f"SKU-{i}", f"Холодильник Bosch тест {i}")

    data: dict[str, Any] = {"query": "Холодильник"}
    if limit is not None:
        data["limit"] = limit

    outcome = tools.dispatch("search_products", data, "uk")
    payload = unwrap(outcome)

    assert payload["total"] == 10
    assert payload["shown"] == expected
    assert len(payload["products"]) == expected


# ---------------------------------------------------------------------------
# get_product
# ---------------------------------------------------------------------------


def test_get_product_returns_specs_from_db(product_with_specs: Product) -> None:
    """Характеристики — рівно ті, що в specs_json, з готовим «331 л» у значенні."""
    payload = unwrap(tools.dispatch("get_product", {"id": product_with_specs.pk}, "uk"))

    assert payload["id"] == product_with_specs.pk
    assert payload["warranty_months"] == 24
    assert payload["category"] == "Холодильники"

    rows = {row["name"]: row["value"] for group in payload["specs"] for row in group["rows"]}
    assert rows == {"Загальний об'єм": "331 л", "Висота": "203 см", "No Frost": "Так"}

    # Опис — без HTML-тегів (модель їх однаково переказала б словами).
    assert payload["description"] == "Двокамерний холодильник із зоною свіжості."
    assert "<p>" not in payload["description"]
    assert "specs_note" not in payload


def test_get_product_missing_id_is_error(db: None) -> None:
    outcome = tools.dispatch("get_product", {"id": 999_999}, "uk")

    assert outcome["is_error"] is True
    assert "search_products" in outcome["content"]


def test_get_product_inactive_is_error(make_product: Any) -> None:
    """Знятий з продажу товар — це «немає», а не «є, але тихо»."""
    hidden = make_product("HIDDEN-1", "Холодильник Bosch знятий", is_active=False)

    outcome = tools.dispatch("get_product", {"id": hidden.pk}, "uk")
    assert outcome["is_error"] is True


# ---------------------------------------------------------------------------
# compare_products
# ---------------------------------------------------------------------------


def test_compare_products_marks_differences(
    product_with_specs: Product,
    product_without_specs: Product,
) -> None:
    """Порівняння рахує is_different — і це єдине, про що моделі варто говорити."""
    payload = unwrap(
        tools.dispatch(
            "compare_products",
            {"ids": [product_with_specs.pk, product_without_specs.pk]},
            "uk",
        )
    )

    assert [card["id"] for card in payload["products"]] == [
        product_with_specs.pk,
        product_without_specs.pk,
    ]

    rows = {row["name"]: row for group in payload["groups"] for row in group["rows"]}
    volume = rows["Загальний об'єм"]
    # У LG характеристики НЕМАЄ — і це не «прочерк для краси», а сама відмінність.
    assert volume["values"] == ["331 л", ""]
    assert volume["is_different"] is True
    assert "is_different" in payload["hint"]


@pytest.mark.parametrize("count", [1, 5])
def test_compare_products_rejects_ids_outside_2_4(make_product: Any, count: int) -> None:
    """🔴 2..4 — це is_error, а НЕ мовчазний кламп.

    Обрізати список до чотирьох означало б викинути з порівняння саме той товар, про який
    людина спитала, — і модель ніколи б про це не дізналась.
    """
    ids = [make_product(f"CMP-{i}", f"Холодильник Bosch порівняння {i}").pk for i in range(count)]

    outcome = tools.dispatch("compare_products", {"ids": ids}, "uk")

    assert outcome["is_error"] is True
    assert f"{tools.COMPARE_MIN}" in outcome["content"]
    assert f"{tools.COMPARE_MAX}" in outcome["content"]


def test_compare_products_deduplicates_before_counting(product_with_specs: Product) -> None:
    """[7, 7] — це ОДИН товар, а не два: дублікати не мають пролізати повз межу 2..4."""
    pk = product_with_specs.pk
    outcome = tools.dispatch("compare_products", {"ids": [pk, pk]}, "uk")

    assert outcome["is_error"] is True


# ---------------------------------------------------------------------------
# get_facets
# ---------------------------------------------------------------------------


def test_get_facets_returns_live_counts(
    product_with_specs: Product,
    product_without_specs: Product,
) -> None:
    """Фасети — з живими лічильниками з тієї самої вибірки, що й лістинг."""
    payload = unwrap(tools.dispatch("get_facets", {"category": "kholodylnyky"}, "uk"))

    assert payload["category"] == "Холодильники"
    assert payload["total"] == 2
    assert payload["price_min"] == "27445.00"
    assert payload["price_max"] == "41999.00"

    facets = {group["code"]: group for group in payload["facets"]}
    brand_values = {v["value"]: v["count"] for v in facets["brand"]["values"]}
    assert brand_values == {"bosch": 2}

    avail_values = {v["value"]: v["count"] for v in facets["avail"]["values"]}
    assert avail_values == {"in_stock": 1, "on_order": 1}


def test_get_facets_unknown_category_is_error(product_with_specs: Product) -> None:
    outcome = tools.dispatch("get_facets", {"category": "nemaie-takoi"}, "uk")
    assert outcome["is_error"] is True


# ---------------------------------------------------------------------------
# build_catalog_link
# ---------------------------------------------------------------------------


def test_build_catalog_link_builds_full_slug_chain(product_with_specs: Product) -> None:
    """Лінк — це ЛАНЦЮГ слагів предків, а не один слаг категорії.

    `/uk/catalog/kholodylnyky` — це 404 на фронті: канонічний роут каталогу — повний шлях
    (frontend/src/lib/catalog-path.ts::findSlugPath).
    """
    outcome = tools.dispatch(
        "build_catalog_link",
        {"category": "5609710", "filters": {"brand": ["bosch"], "avail": ["in_stock"]}},
        "uk",
    )
    payload = unwrap(outcome)

    assert payload["url"] == (
        "/uk/catalog/velyka-pobutova-tekhnika/kholodylnyky?avail=in_stock&brand=bosch"
    )
    # Той самий лінк їде фронту окремою SSE-подією.
    assert outcome["link"] == payload["url"]


def test_build_catalog_link_rejects_unknown_facet_code(product_with_specs: Product) -> None:
    """🔴 ГОЛОВНЕ ТУТ. Вигаданий КОД фасета → is_error і ЖОДНОГО посилання.

    Битий лінк у чаті виглядає правдоподібно: людина клікає, бачить порожній каталог і
    вирішує, що в магазині нічого немає. Краще помилка інструмента, яку модель виправить у
    наступній ітерації, ніж «робоче» посилання в нікуди.
    """
    outcome = tools.dispatch(
        "build_catalog_link",
        {"category": "kholodylnyky", "filters": {"kolir_korpusu": ["chornyi"]}},
        "uk",
    )

    assert outcome["is_error"] is True
    assert outcome["link"] is None
    assert "kolir_korpusu" in outcome["content"]
    assert "get_facets" in outcome["content"]


def test_build_catalog_link_rejects_unknown_facet_value(product_with_specs: Product) -> None:
    """Код правильний, значення вигадане — теж is_error і теж без лінка."""
    outcome = tools.dispatch(
        "build_catalog_link",
        {"category": "kholodylnyky", "filters": {"brand": ["siemens"]}},
        "uk",
    )

    assert outcome["is_error"] is True
    assert outcome["link"] is None
    assert "siemens" in outcome["content"].lower()


def test_build_catalog_link_rejects_unknown_sort(product_with_specs: Product) -> None:
    outcome = tools.dispatch(
        "build_catalog_link",
        {"category": "kholodylnyky", "sort": "naideshevshe"},
        "uk",
    )

    assert outcome["is_error"] is True
    assert outcome["link"] is None


def test_build_catalog_link_without_any_narrowing_is_error(product_with_specs: Product) -> None:
    """Посилання без категорії, пошуку й фільтрів — це просто «/catalog». Такий лінк не
    допомагає нікому, і давати його замість відповіді модель не мусить."""
    outcome = tools.dispatch("build_catalog_link", {}, "uk")

    assert outcome["is_error"] is True
    assert outcome["link"] is None


def test_build_catalog_link_search_goes_to_search_page(product_with_specs: Product) -> None:
    """Запит без категорії веде на /search, а не на /catalog."""
    payload = unwrap(tools.dispatch("build_catalog_link", {"q": "Bosch"}, "uk"))
    assert payload["url"] == "/uk/search?q=Bosch"


# ---------------------------------------------------------------------------
# get_store_info
# ---------------------------------------------------------------------------


def test_get_store_info_reads_published_page(payment_page: Any) -> None:
    """Доставка й оплата — ОДНА сторінка (`payment-delivery`), текст — без HTML і без склейки."""
    payload = unwrap(tools.dispatch("get_store_info", {"topic": "delivery"}, "uk"))

    assert payload["page_key"] == "payment-delivery"
    assert payload["title"] == "Оплата і доставка"
    assert payload["url"] == "/uk/page/payment-delivery"
    assert "Нова Пошта, 1–3 дні." in payload["text"]
    # 🔴 Блокові теги стали переносами, а не зникли: інакше модель чесно переказала б людині
    #    неіснуюче слово «ДоставкаНова».
    assert "ДоставкаНова" not in payload["text"]
    assert "<h2>" not in payload["text"]

    # «payment» — та сама сторінка, а не окрема (і не вигадана).
    assert unwrap(tools.dispatch("get_store_info", {"topic": "payment"}, "uk"))["page_key"] == (
        "payment-delivery"
    )


def test_get_store_info_unpublished_page_is_error(payment_page: Any) -> None:
    """🔴 Чернетка сторінки — це юридично НЕ умови магазину. Краще «не знаю», ніж чернетка."""
    payment_page.is_published = False
    payment_page.save(update_fields=["is_published"])

    outcome = tools.dispatch("get_store_info", {"topic": "delivery"}, "uk")

    assert outcome["is_error"] is True
    assert "менеджер" in outcome["content"].lower()


def test_get_store_info_missing_page_is_error(db: None) -> None:
    outcome = tools.dispatch("get_store_info", {"topic": "warranty"}, "uk")

    assert outcome["is_error"] is True
    assert "не вигадуй" in outcome["content"].lower()


def test_get_store_info_hours_come_from_pickup_points(pickup_point: Any) -> None:
    """Графік роботи живе в cms.PickupPoint (у SiteSettings його немає взагалі)."""
    payload = unwrap(tools.dispatch("get_store_info", {"topic": "hours"}, "uk"))

    point = payload["pickup_points"][0]
    assert point["city"] == "Ужгород"
    assert point["working_hours"] == "ПН–ПТ 09:00–20:00"
    assert point["phone"] == "+380501234567"


def test_get_store_info_unknown_topic_is_error(db: None) -> None:
    outcome = tools.dispatch("get_store_info", {"topic": "pohoda"}, "uk")
    assert outcome["is_error"] is True


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def test_dispatch_unknown_tool_is_error_not_exception(db: None) -> None:
    """Невідомий інструмент — is_error. Виняток тут обірвав би SSE-стрім на півслові."""
    outcome = tools.dispatch("give_discount", {}, "uk")

    assert outcome["is_error"] is True
    assert "search_products" in outcome["content"]


def test_dispatch_survives_broken_input(product_with_specs: Product) -> None:
    """Крива форма аргументів від моделі — теж is_error, а не 500 посеред відповіді."""
    outcome = tools.dispatch("build_catalog_link", {"filters": "bosch"}, "uk")

    assert outcome["is_error"] is True
    assert outcome["link"] is None
