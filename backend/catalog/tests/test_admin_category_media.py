"""
Категорії в адмінці: емблема, плитка, галочки в списку — і кеш, який усе це показує.

Це другий бік того самого сценарію, що й банери: замовник — не програміст, і єдиний його
інструмент — форма адмінки. Тут перевіряється рівно те, що він робитиме руками.

Чому саме ці чотири блоки:

  1. **Емблема (`icon`).** Це `FileField` з РУЧНИМ валідатором (`catalog/validators.py`), а не
     ImageField, бо основний формат — SVG, якого Pillow не читає. Ручний валідатор — місце,
     де «неправильний файл» легко перетворюється на 500 замість підпису під полем.

  2. **Негативні кейси .gif і >64 КБ.** Ліміт стоїть не з педантизму: емблеми ВСІХ категорій
     вантажаться в мегаменю разом. Замовник цього не знає і заллє фото з телефона — він має
     побачити зрозумілу помилку форми, а не «Server Error (500)».

  3. **`list_editable`.** «Активна» і «Показувати в мегаменю» — галочки прямо в списку, тобто
     formset (див. `core/tests/admin_forms.py`), а не звичайний POST.

  4. **🔴 ІНВАЛІДАЦІЯ КЕШУ.** Дерево категорій кешується на ГОДИНУ (`TREE_TTL`). Без скидання
     кешу на збереження замовник вимикає категорію, оновлює сайт — і вона все ще там. Він
     вимикає її ще раз, потім дзвонить. Тому інвалідація перевіряється НЕ на рівні функції
     (`invalidate_catalog_cache()` викликали — молодці), а наскрізно: POST з адмінки →
     ключ `cat:tree:*` зник → наступне читання віддає нове дерево.
"""

from __future__ import annotations

import io
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from catalog.enums import Availability
from catalog.models import Category, Product
from catalog.services.cache import invalidate_catalog_cache, tree_key
from catalog.services.tree import get_category_tree
from core.tests.admin_forms import changelist_data, form_data

pytestmark = pytest.mark.django_db

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><circle r="8"/></svg>'


@pytest.fixture(autouse=True)
def _media_root(settings: Any, tmp_path: Path) -> None:
    settings.MEDIA_ROOT = str(tmp_path)


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    """Дерево кешується на годину — між тестами кеш мусить бути порожній."""
    invalidate_catalog_cache()
    yield
    invalidate_catalog_cache()


def small_png(size: tuple[int, int] = (32, 32)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (10, 120, 200, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _category(external_id: str = "700", name: str = "Холодильники") -> Category:
    category = Category(external_id=external_id, name=name, slug=f"cat-{external_id}")
    category.name_uk, category.slug_uk = name, category.slug
    category.save()
    return category


def _product(category: Category, sku: str = "SKU-1") -> Product:
    """Дерево ховає ПОРОЖНІ категорії — без товару вузол у відповідь API не потрапить."""
    product = Product(
        sku=sku,
        name=f"Товар {sku}",
        slug=f"tovar-{sku}",
        category=category,
        base_price=Decimal("10000"),
        price=Decimal("10000"),
        main_image_url="https://cdn.example.com/p.webp",
        is_active=True,
        availability=Availability.IN_STOCK,
    )
    product.name_uk, product.slug_uk = product.name, product.slug
    product.save()
    return product


def _post_change(client: Client, category: Category, **overrides: Any) -> Any:
    return client.post(
        reverse("admin:catalog_category_change", args=[category.pk]),
        data=form_data(category, **overrides),
    )


# ---------------------------------------------------------------------------
# 1. Емблема і плитка завантажуються
# ---------------------------------------------------------------------------
def test_upload_icon_png(admin_client: Client) -> None:
    category = _category()

    response = _post_change(
        admin_client,
        category,
        icon=SimpleUploadedFile("ico.png", small_png(), content_type="image/png"),
    )

    assert response.status_code == 302, _form_errors(response)
    category.refresh_from_db()
    assert category.icon.name.startswith("categories/icons/")
    assert category.icon.storage.exists(category.icon.name)


def test_upload_icon_svg(admin_client: Client) -> None:
    """SVG — основний формат емблеми, і саме заради нього поле не ImageField."""
    category = _category()

    response = _post_change(
        admin_client,
        category,
        icon=SimpleUploadedFile("ico.svg", SVG, content_type="image/svg+xml"),
    )

    assert response.status_code == 302, _form_errors(response)
    category.refresh_from_db()
    assert category.icon.name.endswith(".svg")


def test_upload_tile_image(admin_client: Client) -> None:
    """«Плитка» — окреме поле від емблеми; замовник плутає їх, код плутати не має."""
    category = _category()

    response = _post_change(
        admin_client,
        category,
        image=SimpleUploadedFile("tile.png", small_png((90, 60)), content_type="image/png"),
    )

    assert response.status_code == 302, _form_errors(response)
    category.refresh_from_db()
    assert category.image.name.startswith("categories/tiles/")
    assert not category.icon  # емблему не зачепили


def test_uploaded_icon_reaches_tree_api(admin_client: Client) -> None:
    """Головне, заради чого це поле існує: емблема доїжджає до мегаменю через API."""
    category = _category()
    _product(category)

    response = _post_change(
        admin_client,
        category,
        icon=SimpleUploadedFile("ico.png", small_png(), content_type="image/png"),
    )
    assert response.status_code == 302, _form_errors(response)

    api = admin_client.get("/api/v1/categories/tree")
    assert api.status_code == 200
    node = next(n for n in api.json() if n["id"] == category.pk)
    assert node["icon_url"], "емблема завантажена, але дерево віддає icon_url=None"
    assert node["icon_url"].endswith(".png")


# ---------------------------------------------------------------------------
# 2. Негативні кейси валідатора — помилка форми, а НЕ 500
# ---------------------------------------------------------------------------
def test_icon_rejects_gif(admin_client: Client) -> None:
    category = _category()

    response = _post_change(
        admin_client,
        category,
        icon=SimpleUploadedFile("ico.gif", small_png(), content_type="image/gif"),
    )

    assert response.status_code == 200, "валідатор мусить повернути форму, а не редірект"
    assert "icon" in _form_errors(response)
    category.refresh_from_db()
    assert not category.icon


def test_icon_rejects_oversized_file(admin_client: Client) -> None:
    """>64 КБ. Байти навмисно випадкові: PNG зі шуму не стискається, тож розмір гарантований."""
    category = _category()
    heavy = b"\x89PNG\r\n\x1a\n" + os.urandom(70 * 1024)

    response = _post_change(
        admin_client,
        category,
        icon=SimpleUploadedFile("big.png", heavy, content_type="image/png"),
    )

    assert response.status_code == 200
    errors = _form_errors(response)
    assert "icon" in errors and "64" in errors, errors
    category.refresh_from_db()
    assert not category.icon


# ---------------------------------------------------------------------------
# 3. Галочки прямо в списку (list_editable)
# ---------------------------------------------------------------------------
def test_changelist_toggles_flags(admin_client: Client) -> None:
    category = _category()
    other = _category("701", "Пральні машини")
    assert category.is_active and category.show_in_megamenu

    response = admin_client.post(
        reverse("admin:catalog_category_changelist"),
        data=changelist_data(
            Category,
            {category.pk: {"is_active": False, "show_in_megamenu": False, "sort_order": 3}},
        ),
    )

    assert response.status_code == 302, _form_errors(response)
    category.refresh_from_db()
    other.refresh_from_db()
    assert category.is_active is False
    assert category.show_in_megamenu is False
    assert category.sort_order == 3
    assert other.is_active is True and other.show_in_megamenu is True


# ---------------------------------------------------------------------------
# 4. Інвалідація кешу — інакше зміни «не видно на сайті» до години
# ---------------------------------------------------------------------------
def test_change_form_drops_tree_cache(admin_client: Client) -> None:
    category = _category()
    _product(category)
    get_category_tree("uk")
    assert cache.get(tree_key("uk")) is not None, "кеш не нагрівся — тест нічого не перевіряє"

    response = _post_change(admin_client, category, name_uk="Холодильники та морозильники")

    assert response.status_code == 302, _form_errors(response)
    assert cache.get(tree_key("uk")) is None, (
        "збереження категорії не скинуло cat:tree:uk — на сайті стара назва до кінця TTL"
    )
    assert get_category_tree("uk")[0]["name"] == "Холодильники та морозильники"


def test_changelist_toggle_drops_tree_cache(admin_client: Client) -> None:
    """Найчастіша дія замовника: зняв галочку в списку → категорія зникає з меню ОДРАЗУ."""
    category = _category()
    _product(category)
    assert get_category_tree("uk"), "категорія з товаром мусить бути в дереві"

    response = admin_client.post(
        reverse("admin:catalog_category_changelist"),
        data=changelist_data(Category, {category.pk: {"is_active": False}}),
    )

    assert response.status_code == 302, _form_errors(response)
    assert cache.get(tree_key("uk")) is None
    assert get_category_tree("uk") == [], "вимкнена категорія все ще в дереві"


# ---------------------------------------------------------------------------
def _form_errors(response: Any) -> str:
    context = response.context_data if getattr(response, "context_data", None) else {}
    admin_form = context.get("adminform")
    if admin_form is not None:
        return str(admin_form.form.errors)
    changelist = context.get("cl")
    if changelist is not None and getattr(changelist, "formset", None) is not None:
        return str(changelist.formset.errors)
    return "форма збереглась або контексту немає"
