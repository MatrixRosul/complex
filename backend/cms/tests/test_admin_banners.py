"""
Банери в адмінці — ЧИ МОЖЕ ЗАМОВНИК ЇХ РЕАЛЬНО ЗАВЕСТИ.

Банер — єдиний блок головної, який редагує НЕ програміст: слайдер, промо, реклама праворуч
від каталогу. Тому перевіряється не «модель зберігається», а сценарій людини:
відкрив «Додати банер» → вибрав розміщення → завантажив картинку → зберіг → побачив на сайті;
потім зі списку зняв галочку «Активний» → банер зник.

Три речі, на яких це ламається, і всі три тут закриті:

  1. **`image` ПЕРЕКЛАДНЕ** (`cms/translation.py`: у банері текст запечений у картинку).
     У формі немає поля `image` — є `image_uk` / `image_ru`, і обов'язкове саме `image_uk`.
     Тест, який шле `image=`, «проходив» би повз валідацію і нічого не завантажував.

  2. **`home_side`** з'явилось міграцією `cms/0002_alter_banner_placement` ПІСЛЯ того, як
     форма вже жила. Якщо choices десь захардкожені (форма, віджет, фільтр) — вибір мовчки
     відхиляється з «Select a valid choice».

  3. **`list_editable`** — галочка «Активний» прямо в списку. Це formset, а не звичайний POST
     (див. `core/tests/admin_forms.py`): найлегший спосіб отримати «натиснув Зберегти, а
     нічого не змінилось».
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from cms.models import Banner
from core.tests.admin_forms import changelist_data, form_data

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media_root(settings: Any, tmp_path: Path) -> None:
    """Файли тестів осідають у tmp, а не в robochomu `backend/media/`."""
    settings.MEDIA_ROOT = str(tmp_path)


def png_bytes(size: tuple[int, int] = (32, 32)) -> bytes:
    """Справжній PNG: `Banner.image` — ImageField, Pillow перевіряє вміст, а не розширення."""
    buffer = io.BytesIO()
    Image.new("RGBA", size, (200, 30, 30, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def png_upload(name: str = "slider.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, png_bytes(), content_type="image/png")


def _banner(**kwargs: Any) -> Banner:
    banner = Banner(
        placement=kwargs.pop("placement", Banner.Placement.HOME_SLIDER),
        sort_order=kwargs.pop("sort_order", 0),
        **kwargs,
    )
    banner.title_uk = banner.title = kwargs.get("title", "Банер")
    banner.image_uk = banner.image = "banners/existing.png"
    banner.save()
    return banner


# ---------------------------------------------------------------------------
# 1. Створення банера з картинкою
# ---------------------------------------------------------------------------
def test_add_banner_uploads_image(admin_client: Client) -> None:
    """Сценарій «Додати банер»: файл долітає до сховища, а не тихо губиться."""
    response = admin_client.post(
        reverse("admin:cms_banner_add"),
        data={
            "placement": Banner.Placement.HOME_SLIDER,
            "sort_order": "0",
            "is_active": "on",
            "title_uk": "Знижки на холодильники",
            "image_uk": png_upload(),
            "_save": "",
        },
    )

    assert response.status_code == 302, _form_errors(response)
    banner = Banner.objects.get()
    assert banner.title_uk == "Знижки на холодильники"
    # ⚠️ Саме `image_uk`: `image` — це проксі modeltranslation на колонку активної мови.
    assert banner.image_uk.name.startswith("banners/")
    assert banner.image_uk.storage.exists(banner.image_uk.name)
    assert banner.image_uk.size > 0


def test_add_banner_accepts_home_side(admin_client: Client) -> None:
    """`home_side` (реклама праворуч від каталогу) — валідний вибір ФОРМИ, а не лише моделі."""
    response = admin_client.post(
        reverse("admin:cms_banner_add"),
        data={
            "placement": Banner.Placement.HOME_SIDE,
            "sort_order": "0",
            "is_active": "on",
            "title_uk": "Реклама збоку",
            "image_uk": png_upload("side.png"),
            "_save": "",
        },
    )

    assert response.status_code == 302, _form_errors(response)
    assert Banner.objects.get().placement == "home_side"


def test_add_banner_without_image_is_rejected(admin_client: Client) -> None:
    """Без картинки банер не заводиться — і це помилка форми, а не 500."""
    response = admin_client.post(
        reverse("admin:cms_banner_add"),
        data={
            "placement": Banner.Placement.HOME_PROMO,
            "sort_order": "0",
            "title_uk": "Без картинки",
            "_save": "",
        },
    )

    assert response.status_code == 200
    assert "image_uk" in _form_errors(response)
    assert not Banner.objects.exists()


# ---------------------------------------------------------------------------
# 2. Заміна картинки на існуючому банері
# ---------------------------------------------------------------------------
def test_change_banner_replaces_image(admin_client: Client) -> None:
    banner = _banner(title="Старий")

    response = admin_client.post(
        reverse("admin:cms_banner_change", args=[banner.pk]),
        data=form_data(banner, title_uk="Новий", image_uk=png_upload("new.png")),
    )

    assert response.status_code == 302, _form_errors(response)
    banner.refresh_from_db()
    assert banner.title_uk == "Новий"
    assert banner.image_uk.name != "banners/existing.png"
    assert banner.image_uk.storage.exists(banner.image_uk.name)


# ---------------------------------------------------------------------------
# 3. Галочка «Активний» прямо в списку (list_editable)
# ---------------------------------------------------------------------------
def test_changelist_toggles_is_active(admin_client: Client) -> None:
    """Замовник знімає галочку в списку → банер справді гасне в БД."""
    visible = _banner(title="Показуємо", sort_order=1)
    other = _banner(title="Другий", sort_order=2)
    assert visible.is_active and other.is_active

    response = admin_client.post(
        reverse("admin:cms_banner_changelist"),
        data=changelist_data(Banner, {visible.pk: {"is_active": False, "sort_order": 7}}),
    )

    assert response.status_code == 302, _form_errors(response)
    visible.refresh_from_db()
    other.refresh_from_db()
    assert visible.is_active is False
    assert visible.sort_order == 7
    # Сусідній рядок формсет не зачепив — інакше «вимкнув один, зникли всі».
    assert other.is_active is True


def test_changelist_turns_banner_back_on(admin_client: Client) -> None:
    banner = _banner(title="Вимкнений")
    Banner.objects.filter(pk=banner.pk).update(is_active=False)

    response = admin_client.post(
        reverse("admin:cms_banner_changelist"),
        data=changelist_data(Banner, {banner.pk: {"is_active": True}}),
    )

    assert response.status_code == 302, _form_errors(response)
    banner.refresh_from_db()
    assert banner.is_active is True


# ---------------------------------------------------------------------------
def _form_errors(response: Any) -> str:
    """Читабельна причина, чому форма не збереглась (інакше в асерті лише «200 != 302»)."""
    form = response.context_data.get("adminform") if response.context_data else None
    if form is not None:
        return str(form.form.errors)
    formset = (response.context_data or {}).get("cl")
    if formset is not None and getattr(formset, "formset", None) is not None:
        return str(formset.formset.errors)
    return "форма збереглась або контексту немає"
