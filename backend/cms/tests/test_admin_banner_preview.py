"""Прев'ю банера в адмінці.

Сенс тестів — не «чи гарно», а чотири речі, які тихо ламаються:
  1. форма банера відкривається (readonly-поля легко впустити 500-ю);
  2. у списку розміщень НЕМАЄ того, чого сайт не виводить — саме за це замовник і
     зачепився («чого воно називається промо-блок, а слайдера нема»);
  3. прев'ю ріже фото так само, як сайт (cover + object-position), а не показує його
     цілком — інакше людина бачить те, чого на сторінці не буває;
  4. у схемі слота видно ЄМНІСТЬ: скільки банерів стане в ряд, а скільки чекатиме.
"""

from __future__ import annotations

import pytest

from cms.admin_previews import (
    SLOT_CAPACITY,
    SLOT_COLORS,
    crop_editor,
    live_preview,
    placement_badge,
    real_slot_preview,
)
from cms.models import Banner

pytestmark = pytest.mark.django_db


def _banner(**kwargs) -> Banner:
    kwargs.setdefault("placement", Banner.Placement.HOME_PROMO)
    kwargs.setdefault("image", "banners/x.png")
    banner = Banner(**kwargs)
    banner.image_uk = banner.image = kwargs["image"]
    banner.save()
    return banner


def test_add_form_opens(admin_client) -> None:
    res = admin_client.get("/admin/cms/banner/add/")
    assert res.status_code == 200
    assert "Слот на сайті" in res.content.decode()


def test_change_form_opens_with_crop_editor(admin_client) -> None:
    banner = _banner()
    res = admin_client.get(f"/admin/cms/banner/{banner.pk}/change/")
    assert res.status_code == 200
    html = res.content.decode()
    assert "banner-crop-stage" in html  # клікабельне фото для кадру
    assert "banners/x.png" in html


def test_dead_placements_are_gone() -> None:
    """Слайдера й банера над категорією на сайті немає — не має бути й у виборі.

    ⚠️ Тест навмисно прив'язаний до ЗНАЧЕНЬ, а не до кількості: якщо колись фронт
    навчиться нового розміщення, його додадуть свідомо, а не поверне випадковий rebase.
    """
    values = set(Banner.Placement.values)
    assert "home_slider" not in values
    assert "category_top" not in values
    assert values == {"home_promo", "home_side"}


def test_every_placement_has_capacity_and_badge() -> None:
    for placement in Banner.Placement.values:
        assert placement in SLOT_CAPACITY, f"{placement} без ємності слота"
        assert placement_badge(placement) in SLOT_COLORS


def test_slot_preview_shows_capacity_and_queue() -> None:
    """Четвертий банер у ряд не влазить → прев'ю це прямо каже."""
    banners = [_banner(sort_order=i) for i in range(4)]
    html = str(real_slot_preview(banners[0], banners))
    assert "перегортанням" in html
    assert "Ще 1" in html


def test_slot_preview_counts_when_row_not_full() -> None:
    banner = _banner()
    html = str(real_slot_preview(banner, [banner]))
    assert "Одночасно на сайті: 1 з 3" in html


def test_preview_crops_like_site() -> None:
    """cover + object-position з відсотків — рівно те, що робить фронт."""
    banner = _banner(focus_x=20, focus_y=80)
    html = str(live_preview(banner))
    assert "object-fit:cover" in html
    assert "object-position:20% 80%" in html
    assert "object-fit:contain" not in html


def test_zoom_applied_only_above_100() -> None:
    assert "scale(" not in str(live_preview(_banner(zoom=100)))
    assert "scale(1.5)" in str(live_preview(_banner(zoom=150)))


def test_crop_editor_escapes_image_url() -> None:
    """URL картинки йде в HTML — через format_html, не конкатенацію.

    ⚠️ Перевіряємо саме ЕКРАНУВАННЯ payload'а: у прев'ю є власний скрипт кадрування,
    тож наївний `"<script>" not in html` ловив би його замість ін'єкції.
    """
    banner = _banner(image='"><script>alert(1)</script>')
    html = str(crop_editor(banner))
    # Головне: payload не став виконуваним тегом і не розірвав атрибут src.
    assert "<script>alert(1)</script>" not in html
    assert "&quot;&gt;&lt;script&gt;" in html or "%22%3E%3Cscript" in html
