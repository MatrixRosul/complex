"""Схеми CMS (банери, новини, статичні сторінки, меню)."""

from __future__ import annotations

from datetime import datetime

from ninja import Schema

__all__ = ["BannerOut", "MenuItemOut", "NewsPostOut", "StaticPageOut"]


class BannerOut(Schema):
    id: int
    title: str = ""
    subtitle: str = ""
    image_url: str = ""
    mobile_image_url: str | None = None
    link: str = ""
    cta_label: str = ""  # моделі поки не має — фронт (types.ts) поле чекає, віддаємо порожнім
    placement: str = ""
    # Кадр у відсотках → CSS object-position: {x}% {y}%; zoom — CSS scale.
    focus_x: int = 50
    focus_y: int = 50
    zoom: int = 100


class NewsPostOut(Schema):
    id: int
    title: str
    slug: str
    excerpt: str = ""
    body: str = ""  # rich HTML, санітизований на бекенді
    cover_url: str | None = None
    published_at: datetime | None = None


class StaticPageOut(Schema):
    id: int
    title: str
    slug: str  # ⚠️ = StaticPage.key (роут по key, не по slug — ADR: URL не змінюється з мовою)
    body: str = ""
    updated_at: datetime


class MenuItemOut(Schema):
    id: int
    title: str
    url: str = ""
    block: str = ""  # zone: header | footer | mobile
    icon: str = ""
