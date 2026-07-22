"""Схема-прев'ю банера в адмінці.

Сенс тестів — не «чи гарно», а дві речі, які тихо ламаються:
  1. форма банера взагалі відкривається (readonly-поле легко впустити 500-ю);
  2. попередження про НЕПІДТРИМАНЕ розміщення показується. Якщо фронт колись навчиться
     `category_top`, а `SUPPORTED` забудуть оновити — тест почне брехати разом з адмінкою,
     тому він прив'язаний саме до розбіжності «є в choices, немає в SUPPORTED».
"""

from __future__ import annotations

import pytest

from cms.admin_previews import SLOT_COLORS, SUPPORTED, layout_preview, placement_badge
from cms.models import Banner

pytestmark = pytest.mark.django_db


def test_add_form_renders_preview(admin_client) -> None:
    """Форма створення відкривається і вже містить макет — ще до вибору розміщення."""
    res = admin_client.get("/admin/cms/banner/add/")
    assert res.status_code == 200
    html = res.content.decode()
    assert "Каталог закритий" in html
    assert "Каталог відкритий" in html


def test_change_form_highlights_slot_and_shows_image(admin_client, tmp_path) -> None:
    """На формі редагування картинка банера стоїть у своєму слоті макета."""
    banner = Banner.objects.create(placement=Banner.Placement.HOME_SIDE, image="banners/x.png")
    res = admin_client.get(f"/admin/cms/banner/{banner.pk}/change/")
    assert res.status_code == 200
    html = res.content.decode()
    assert "banners/x.png" in html  # картинка вмонтована в схему
    assert "ВІДКРИТИЙ" in html  # підказка саме про відкритий стан


def test_unsupported_placement_is_flagged() -> None:
    """`category_top` фронт не читає → підказка попереджає, а бейдж каже «Не виводиться»."""
    unsupported = set(Banner.Placement.values) - SUPPORTED
    assert unsupported, "Якщо всі розміщення підтримані — прибрати попередження й цей тест"

    for placement in unsupported:
        html = str(layout_preview(placement))
        assert "НЕ ВИВОДИТЬ" in html
        assert placement_badge(placement) == "Не виводиться"


def test_every_placement_has_a_slot_label() -> None:
    """Нове значення в choices не повинно тихо лишитись без підпису й кольору."""
    for placement in Banner.Placement.values:
        label = placement_badge(placement)
        assert label, f"{placement} без підпису слота"
        assert label in SLOT_COLORS, f"{label} без кольору бейджа"


def test_slider_is_marked_as_fallback() -> None:
    """«Слайдер» не можна називати «виводиться»: він програє будь-якому «Промо-блоку»."""
    assert placement_badge("home_slider") == "Запасний"
    assert placement_badge("home_promo") == "Широкий слот"


def test_preview_escapes_image_url() -> None:
    """URL картинки йде в HTML — переконуємось, що через format_html, а не конкатенацію.

    ⚠️ Перевіряємо саме ЕКРАНУВАННЯ payload'а, а не відсутність тега <script> взагалі:
    у прев'ю є власний скрипт-перемикач схем, і наївний `"<script>" not in html`
    ловив би його замість ін'єкції.
    """
    html = str(layout_preview("home_side", '"><script>alert(1)</script>'))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)" in html
