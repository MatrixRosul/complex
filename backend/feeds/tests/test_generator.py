"""Генератор XML — структура, скіпи, характеристики, гарантія."""

from __future__ import annotations

from decimal import Decimal

import pytest
from lxml import etree

from catalog.enums import Availability, Condition
from catalog.models import Category
from feeds import generator
from feeds.generator import (
    SKIP_INACTIVE,
    SKIP_NO_HOTLINE_CATEGORY,
    SKIP_NO_IMAGE,
    SKIP_NO_PRICE,
    SKIP_OUT_OF_STOCK,
    build_feed,
)
from feeds.tests.conftest import make_product

pytestmark = pytest.mark.django_db


def parse(xml: bytes) -> etree._Element:
    """Розпарсити фід НАЗАД — головна перевірка валідності."""
    return etree.fromstring(xml)


# ---------------------------------------------------------------------------
# Структура
# ---------------------------------------------------------------------------
def test_feed_is_valid_xml_and_matches_spec(site, product):
    xml, stats = build_feed()
    root = parse(xml)

    assert xml.startswith(
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
    )  # перший рядок, з 0-го символу
    assert root.tag == "price"
    assert root.findtext("firmId") == "12345"
    assert root.findtext("firmName") == "Complex"
    assert root.find("categories") is not None
    assert root.find("items") is not None

    # <date> — строго РРРР-ММ-ДД ГГ:ХХ
    assert etree.fromstring(xml).findtext("date")
    date = root.findtext("date")
    assert len(date) == 16 and date[4] == "-" and date[13] == ":"

    assert stats.items == 1
    item = root.find("items/item")
    assert item.findtext("id") == str(product.pk)
    assert item.findtext("vendor") == "Gorenje"
    assert item.findtext("name") == "NRK6202"
    assert item.findtext("url") == f"https://complex.example/ua/p/{product.pk}/cmpx-1"
    assert item.findtext("priceRUAH") == "23600"  # БЕЗ роздільників розрядів
    assert item.findtext("stock") == "В наявності"
    assert item.findtext("shipping") == "0"
    assert item.findtext("condition") == "0"


def test_categories_come_from_hotline_rubricator_with_parent_chain(site, product, hotline_tree):
    """<category><name> — назва З РУБРИКАТОРА, parentId — з дерева рубрикатора."""
    xml, _ = build_feed()
    root = parse(xml)

    cats = {c.findtext("id"): c for c in root.findall("categories/category")}
    leaf, mid, root_cat = hotline_tree["leaf"], hotline_tree["mid"], hotline_tree["root"]

    # У фіді — уся гілка предків, інакше parentId нерозв'язний.
    assert set(cats) == {str(leaf.pk), str(mid.pk), str(root_cat.pk)}
    assert cats[str(leaf.pk)].findtext("name") == "Холодильники"
    assert cats[str(leaf.pk)].findtext("parentId") == str(mid.pk)
    assert cats[str(root_cat.pk)].find("parentId") is None  # корінь без батька

    item = root.find("items/item")
    assert item.findtext("categoryId") == str(leaf.pk)


def test_only_hotline_enabled_products_are_exported(site, category, brand):
    make_product(category=category, brand=brand, sku="ON", hotline_enabled=True)
    make_product(category=category, brand=brand, sku="OFF", hotline_enabled=False)

    xml, stats = build_feed()
    root = parse(xml)

    assert stats.items == 1
    assert [i.findtext("name") for i in root.findall("items/item")] == ["NRK6202"]
    # Вимкнений тумблер — не «пропуск»: інакше лічильник дорівнював би розміру каталогу.
    assert stats.skipped_total == 0


# ---------------------------------------------------------------------------
# 🔴 РЕГРЕСІЯ: товар БЕЗ mpn усе одно у фіді
# ---------------------------------------------------------------------------
def test_product_without_mpn_is_still_in_feed_without_code_tag(site, category, brand):
    """У v1 порожній mpn був умовою скіпу, а mpn нічим не заповнювався → фід був би ПОРОЖНІЙ."""
    make_product(category=category, brand=brand, sku="NO-MPN", mpn="")

    xml, stats = build_feed()
    item = parse(xml).find("items/item")

    assert stats.items == 1, "товар без mpn ЗОБОВ'ЯЗАНИЙ бути у фіді"
    assert item is not None
    assert item.find("code") is None, "<code> без mpn віддавати не можна"
    assert stats.no_mpn == 1  # ЛІЧИЛЬНИК…
    assert "no_mpn" not in stats.as_reasons()  # …а НЕ причина скіпу


def test_code_is_mpn_not_internal_sku(site, category, brand):
    """<code> — ТІЛЬКИ артикул виробника. Внутрішній артикул туди класти заборонено."""
    product = make_product(category=category, brand=brand, sku="CMPX-777", mpn="RK6192EW4")

    xml, stats = build_feed()
    item = parse(xml).find("items/item")

    assert item.findtext("code") == "RK6192EW4"
    assert item.findtext("id") == str(product.pk)  # внутрішній ідентифікатор іде в <id>
    assert product.sku not in xml.decode()
    assert stats.no_mpn == 0


# ---------------------------------------------------------------------------
# Правила скіпу + логування причин
# ---------------------------------------------------------------------------
def test_product_without_hotline_category_is_skipped_with_reason(site, brand, db):
    """Немає прив'язки до рубрикатора → у фід не йде, причина залогована."""
    orphan = Category.objects.create(external_id="999", name="Без рубрики", slug="bez-rubryky")
    make_product(category=orphan, brand=brand, sku="ORPHAN")

    xml, stats = build_feed()

    assert stats.items == 0
    assert parse(xml).find("items/item") is None
    assert stats.as_reasons() == {SKIP_NO_HOTLINE_CATEGORY: 1}


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"price": Decimal("0")}, SKIP_NO_PRICE),
        ({"image": ""}, SKIP_NO_IMAGE),
        ({"is_active": False}, SKIP_INACTIVE),
        ({"availability": Availability.OUT_OF_STOCK}, SKIP_OUT_OF_STOCK),
    ],
)
def test_skip_rules_are_counted_with_reason(site, category, brand, kwargs, reason):
    make_product(category=category, brand=brand, sku="SKIP", **kwargs)

    xml, stats = build_feed()

    assert stats.items == 0
    assert stats.as_reasons() == {reason: 1}
    assert parse(xml).find("items/item") is None


def test_skipped_reasons_accumulate_per_reason(site, category, brand):
    make_product(category=category, brand=brand, sku="A", price=Decimal("0"))
    make_product(category=category, brand=brand, sku="B", price=Decimal("0"))
    make_product(category=category, brand=brand, sku="C", image="")

    _, stats = build_feed()

    assert stats.as_reasons() == {SKIP_NO_PRICE: 2, SKIP_NO_IMAGE: 1}
    assert stats.skipped_total == 3


# ---------------------------------------------------------------------------
# <param> — характеристики (заради них і обрано XML)
# ---------------------------------------------------------------------------
def test_specs_become_params_with_unit_glued_to_value(site, product):
    """INPUTS §2: одиниця клеїться до ЗНАЧЕННЯ («1635 мм»), а не до назви («Висота (мм)»)."""
    xml, _ = build_feed()
    params = {p.get("name"): p.text for p in parse(xml).findall("items/item/param")}

    assert params["Висота"] == "1635 мм"
    assert "Висота (мм)" not in params
    assert params["Країна виготовлення"] == "Сербія"  # окремо задокументований параметр Hotline


def test_packaging_attributes_do_not_leak_into_params(site, category, brand, attributes):
    """Габарити/вага УПАКОВКИ — внутрішня логістика для Нової Пошти, а не характеристика товару.

    У каталозі Hotline таких параметрів немає: модератор отримав би сміття, яке нікуди не
    змержиться. Тому вони відсікаються за Attribute.role, а не за назвою.
    """
    from catalog.models import Attribute, AttributeGroup

    group = AttributeGroup.objects.get(code="main")
    Attribute.objects.create(
        code="hab-upak",
        name="Габарити упаковки (ВхШхГ)",
        group=group,
        value_type=Attribute.ValueType.STRING,
        role=Attribute.Role.PACKAGE_DIMS,
    )

    make_product(
        category=category,
        brand=brand,
        sku="PKG",
        specs=[
            {"code": "hab-upak", "n": "Габарити упаковки (ВхШхГ)", "u": "см", "v": "68 x 108 x 18"},
            {"code": "vysota", "n": "Висота", "u": "мм", "v": "1635"},
        ],
    )

    xml, _ = build_feed()
    params = {p.get("name") for p in parse(xml).findall("items/item/param")}

    assert "Габарити упаковки (ВхШхГ)" not in params
    assert "Висота" in params  # звичайні характеристики не постраждали


def test_param_name_uses_hotline_param_name_override(site, category, brand, attributes):
    """Назва параметра має збігатися з каталогом Hotline, інакше він не змержиться."""
    attributes["height"].hotline_param_name = "Висота, мм (Hotline)"
    attributes["height"].save(update_fields=["hotline_param_name"])

    make_product(
        category=category,
        brand=brand,
        sku="P",
        specs=[{"code": "vysota", "n": "Висота", "u": "мм", "v": "1635"}],
    )

    xml, _ = build_feed()
    params = {p.get("name") for p in parse(xml).findall("items/item/param")}

    assert "Висота, мм (Hotline)" in params
    assert "Висота" not in params


# ---------------------------------------------------------------------------
# Гарантія
# ---------------------------------------------------------------------------
def test_guarantee_is_manufacturer_and_taken_from_spec(site, product):
    xml, _ = build_feed()
    guarantee = parse(xml).find("items/item/guarantee")

    assert guarantee.get("type") == "manufacturer"  # ЗАВЖДИ від виробника
    assert guarantee.text == "12"  # з характеристики «Гарантійний термін (міс)»


def test_guarantee_omitted_when_unknown(site, category, brand):
    """Немає характеристики → тег ОПУСКАЄМО. Дефолт 12 не вигадуємо — це обіцянка покупцеві."""
    make_product(category=category, brand=brand, sku="NOWAR", warranty_months=None)

    xml, stats = build_feed()

    assert stats.items == 1
    assert parse(xml).find("items/item/guarantee") is None


# ---------------------------------------------------------------------------
# Наявність / уцінка / оплата частинами
# ---------------------------------------------------------------------------
def test_on_order_product_has_mandatory_shipping(site, category, brand):
    make_product(category=category, brand=brand, sku="ORD", availability=Availability.ON_ORDER)

    xml, _ = build_feed()
    item = parse(xml).find("items/item")

    assert item.findtext("stock") == "Під замовлення"
    assert item.findtext("shipping") == "3"  # для «Під замовлення» <shipping> ОБОВ'ЯЗКОВИЙ


def test_condition_comes_from_product(site, category, brand):
    make_product(category=category, brand=brand, sku="UC", condition=Condition.DISCOUNTED)

    xml, _ = build_feed()

    assert parse(xml).findtext("items/item/condition") == "2"  # уцінка


def test_installment_payment_tag(site, category, brand):
    make_product(
        category=category,
        brand=brand,
        sku="INST",
        installment_available=True,
        installment_max_payments=6,
    )

    xml, _ = build_feed()
    payments = {p.get("type"): p for p in parse(xml).findall("items/item/payment")}

    inst = payments["installment"]
    assert inst.get("from") == "Privatbank"  # LiqPay = ПриватБанк; список Hotline фіксований
    assert inst.get("max_period") == "6"  # override з товару
    assert inst.get("min_payment") == "500"
    assert inst.get("no_down_payment") == "true"
    assert inst.text == "true"
    # fee=0 — вимога LiqPay: surcharge заборонений, ціна у фіді = ціна на сайті.
    assert payments["pay-card"].get("fee") == "0"


def test_no_installment_tag_when_disabled(site, product):
    xml, _ = build_feed()
    payments = {p.get("type") for p in parse(xml).findall("items/item/payment")}

    assert "installment" not in payments
    assert "pay-card" in payments


def test_installment_omitted_below_liqpay_minimum(site, category, brand):
    """Товар дешевший за мінімальний платіж розстрочку не отримає фізично → не обіцяємо її."""
    make_product(
        category=category,
        brand=brand,
        sku="CHEAP",
        price=Decimal("199.00"),
        installment_available=True,
    )

    xml, _ = build_feed()
    payments = {p.get("type") for p in parse(xml).findall("items/item/payment")}

    assert "installment" not in payments


# ---------------------------------------------------------------------------
# Екранування / кодування / URL
# ---------------------------------------------------------------------------
def test_escaping_and_html_stripping(site, category, brand):
    make_product(
        category=category,
        brand=brand,
        sku="ESC",
        name="Пральна <b>машина</b> A&B «Люкс»",
        specs=[{"code": "x", "n": "Опис", "u": "", "v": "1 < 2 & 3 > 2"}],
    )

    xml, _ = build_feed()

    assert b"&amp;" in xml
    assert b"<b>" not in xml  # HTML заборонений в УСІХ елементах
    item = parse(xml).find("items/item")  # розпарсилось назад → екранування коректне
    assert item.findtext("name") == "Пральна машина A&B «Люкс»"
    assert item.find("param").text == "1 < 2 & 3 > 2"


def test_urls_are_absolute_and_https(site, product):
    xml, _ = build_feed()
    item = parse(xml).find("items/item")

    assert item.findtext("url").startswith("https://complex.example/")
    assert item.findtext("image").startswith("https://")


def test_relative_media_image_is_absolutized(site, category, brand):
    make_product(category=category, brand=brand, sku="REL", image="/media/products/md/a_md.webp")

    xml, _ = build_feed()

    assert (
        parse(xml).findtext("items/item/image")
        == "https://complex.example/media/products/md/a_md.webp"
    )


def test_price_has_no_thousand_separators(site, category, brand):
    make_product(category=category, brand=brand, sku="BIG", price=Decimal("123456.00"))

    xml, _ = build_feed()

    assert parse(xml).findtext("items/item/priceRUAH") == "123456"
    assert b"123 456" not in xml


def test_control_characters_are_stripped(site, category, brand):
    # \x00 тут навмисно НЕМАЄ: PostgreSQL відхиляє NUL у text-колонці ще на INSERT, тобто
    # дотекти до генератора він не може. Решта ASCII 0–31 (крім 9/10/13) у БД проходить,
    # а у фіді заборонена — саме її й вирізаємо, інакше XML не розпарситься.
    make_product(category=category, brand=brand, sku="CTL", name="Холодильник\x07 X\x0bY")

    xml, _ = build_feed()

    assert parse(xml).findtext("items/item/name") == "Холодильник XY"


def test_plain_text_comparison_signs_survive(site, category, brand):
    """Регресія: жадібний HTML-стрипер `<[^>]*>` з'їдав «< 300 л» як «тег»."""
    make_product(
        category=category,
        brand=brand,
        sku="CMP",
        specs=[{"code": "x", "n": "Рівень шуму", "u": "", "v": "< 40 дБ"}],
    )

    xml, _ = build_feed()

    assert parse(xml).find("items/item/param").text == "< 40 дБ"


def test_item_id_is_within_hotline_limits(site, product):
    xml, _ = build_feed()
    item_id = parse(xml).findtext("items/item/id")

    assert len(item_id) <= 20
    assert generator.ITEM_ID_RE.match(item_id)


def test_shop_delivery_blocks_use_allowed_carriers_only(site, product):
    """carrier — тільки SLF|ME|NP|UP. «Delivery» в enum немає, тому його у фіді бути не може."""
    xml, _ = build_feed()
    carriers = {d.get("carrier") for d in parse(xml).findall("delivery") if d.get("carrier")}

    assert carriers <= {"SLF", "ME", "NP", "UP"}
    assert "Delivery" not in carriers
