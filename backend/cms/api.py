"""API CMS: банери, новини, статичні сторінки, меню.

⚠️ `StaticPage` маршрутизується по `key`, а НЕ по slug (DATA_MODEL §7). Причина: slug —
   перекладний, і при перемиканні мови URL сторінки «поплив» би, ламаючи зовнішні посилання
   й індексацію. Тому в API поле зветься `slug` (так його чекає фронт), але фізично це `key`.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q
from django.http import HttpRequest
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from catalog.services.lang import normalize_lang, tr
from cms.models import Banner, MenuItem, NewsPost, StaticPage
from cms.schemas import BannerOut, ContactsOut, MenuItemOut, NewsPostOut, StaticPageOut
from core.models import SiteSettings, WorkingHours

router = Router(tags=["cms"])


def _url(field: Any) -> str | None:
    try:
        return field.url if field else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Банери
# ---------------------------------------------------------------------------
@router.get("/banners", response=list[BannerOut], summary="Банери")
def banners(request: HttpRequest, lang: str = "uk", placement: str = ""):
    """Тільки ЖИВІ банери: активні і в межах вікна показу.

    ⚠️ Фільтр по `starts_at`/`ends_at` робить БАЗА, а не фронт. Інакше промо-банер «Чорна
       п'ятниця» висів би на сайті до першої ревалідації ISR — тобто ще 10 хвилин після
       закінчення акції.
    """
    lang = normalize_lang(lang)
    now = timezone.now()

    qs = Banner.objects.filter(is_active=True).filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now),
        Q(ends_at__isnull=True) | Q(ends_at__gt=now),
    )
    if placement:
        qs = qs.filter(placement=placement)

    return [
        {
            "id": b.pk,
            "title": tr(b, "title", lang) or "",
            "subtitle": tr(b, "subtitle", lang) or "",
            "image_url": _url(tr(b, "image", lang)) or "",
            "mobile_image_url": _url(tr(b, "image_mobile", lang)),
            "link": b.link_url or "",
            "cta_label": "",
            "placement": b.placement,
            # ⚠️ Словник збирається ВРУЧНУ, тож нові поля моделі саме сюди не потраплять:
            # у схеми є дефолти, і без цих рядків API мовчки віддавав би центр і 100%
            # незалежно від того, як замовник скадрував банер в адмінці.
            "focus_x": b.focus_x,
            "focus_y": b.focus_y,
            "zoom": b.zoom,
        }
        for b in qs.order_by("placement", "sort_order", "id")
    ]


# ---------------------------------------------------------------------------
# Новини
# ---------------------------------------------------------------------------
@router.get("/news", response=list[NewsPostOut], summary="Новини")
def news_list(request: HttpRequest, lang: str = "uk", limit: int = 20):
    lang = normalize_lang(lang)
    qs = NewsPost.objects.filter(is_published=True).order_by("-published_at", "-id")

    return [
        {
            "id": n.pk,
            "title": tr(n, "title", lang),
            "slug": n.slug_uk,  # роут спільний для обох мов (ключ — UK-slug)
            "excerpt": tr(n, "excerpt", lang) or "",
            "body": "",  # у списку тіло не віддаємо — це кілобайти HTML на кожну картку
            "cover_url": _url(n.cover),
            "published_at": n.published_at,
        }
        for n in qs[: max(1, min(limit, 100))]
    ]


@router.get("/news/{slug}", response=NewsPostOut, summary="Новина")
def news_detail(request: HttpRequest, slug: str, lang: str = "uk"):
    lang = normalize_lang(lang)
    post = (
        NewsPost.objects.filter(is_published=True).filter(Q(slug_uk=slug) | Q(slug_ru=slug)).first()
    )
    if post is None:
        raise HttpError(404, "Новину не знайдено")

    return {
        "id": post.pk,
        "title": tr(post, "title", lang),
        "slug": post.slug_uk,
        "excerpt": tr(post, "excerpt", lang) or "",
        "body": tr(post, "body", lang) or "",
        "cover_url": _url(post.cover),
        "published_at": post.published_at,
    }


# ---------------------------------------------------------------------------
# Статичні сторінки
# ---------------------------------------------------------------------------
@router.get("/pages/{slug}", response=StaticPageOut, summary="Статична сторінка")
def page_detail(request: HttpRequest, slug: str, lang: str = "uk"):
    lang = normalize_lang(lang)
    page = StaticPage.objects.filter(key=slug, is_published=True).first()
    if page is None:
        raise HttpError(404, "Сторінку не знайдено")

    return {
        "id": page.pk,
        "title": tr(page, "title", lang),
        "slug": page.key,
        "body": tr(page, "body", lang) or "",
        "updated_at": page.updated_at,
    }


@router.get("/pages", response=list[StaticPageOut], summary="Список сторінок")
def pages_list(request: HttpRequest, lang: str = "uk"):
    lang = normalize_lang(lang)
    return [
        {
            "id": p.pk,
            "title": tr(p, "title", lang),
            "slug": p.key,
            "body": "",
            "updated_at": p.updated_at,
        }
        for p in StaticPage.objects.filter(is_published=True).order_by("sort_order", "id")
    ]


# ---------------------------------------------------------------------------
# Меню
# ---------------------------------------------------------------------------
@router.get("/menu", response=list[MenuItemOut], summary="Меню")
def menu(request: HttpRequest, lang: str = "uk", zone: str = ""):
    lang = normalize_lang(lang)
    qs = MenuItem.objects.filter(is_active=True).select_related("static_page")
    if zone:
        qs = qs.filter(zone=zone)

    result: list[dict[str, Any]] = []
    for item in qs.order_by("zone", "sort_order", "id"):
        # Прив'язана сторінка виграє в поля `url` (модель: «url ігнорується»).
        url = f"/page/{item.static_page.key}" if item.static_page_id else (item.url or "")
        result.append(
            {
                "id": item.pk,
                "title": tr(item, "title", lang),
                "url": url,
                "block": item.zone,
                "icon": item.icon or "",
            }
        )
    return result


# ---------------------------------------------------------------------------
# Контакти
# ---------------------------------------------------------------------------
# Короткі назви днів. У моделі вони повні («Понеділок») і тільки українською, а в шапку
# треба «ПН – ПТ» і обома мовами — тому словник тут, а не в choices моделі.
_WEEKDAY_SHORT: dict[str, tuple[str, ...]] = {
    "uk": ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "НД"),
    "ru": ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"),
}
_DAY_OFF: dict[str, str] = {"uk": "вихідний", "ru": "выходной"}


def _format_hours(rows: list[Any], lang: str) -> list[dict[str, str]]:
    """7 рядків «день → години» → «ПН – ПТ 09:00 – 20:00 · СБ – НД 10:00 – 17:00».

    ⚠️ Групуємо ПІДРЯД ІДУЧІ дні з однаковими годинами, а не всі однакові скопом. Інакше
       графік «ПН–ПТ 9–20, СБ 10–17, НД 9–20» злився б у брехливе «ПН–ПТ, НД», де діапазон
       перескакує через суботу. Дірка в тижні (день без рядка в БД) теж рве групу.
    """
    short = _WEEKDAY_SHORT.get(lang, _WEEKDAY_SHORT["uk"])
    day_off = _DAY_OFF.get(lang, _DAY_OFF["uk"])

    def label(row: Any) -> str:
        if row.is_day_off or row.open_time is None or row.close_time is None:
            return day_off
        return f"{row.open_time:%H:%M} – {row.close_time:%H:%M}"

    groups: list[tuple[int, int, str]] = []  # (перший день, останній день, години)
    for row in sorted(rows, key=lambda r: r.weekday):
        text = label(row)
        if groups and groups[-1][2] == text and groups[-1][1] == row.weekday - 1:
            first, _, _ = groups[-1]
            groups[-1] = (first, row.weekday, text)
        else:
            groups.append((row.weekday, row.weekday, text))

    return [
        {
            "days": short[first] if first == last else f"{short[first]} – {short[last]}",
            "time": text,
        }
        for first, last, text in groups
    ]


@router.get("/contacts", response=ContactsOut, summary="Контакти")
def contacts(request: HttpRequest, lang: str = "uk"):
    """Телефони, e-mail, адреса і графік — З АДМІНКИ (Налаштування сайту + Графік роботи).

    🔴 Цього ендпоінта не було, і фронт роками показував у шапці ЗАХАРДКОДЖЕНІ телефони з
       `frontend/src/lib/api/types.ts`-сусіда `lib/site.ts::FALLBACK_CONTACTS`. Тобто поля в
       адмінці існували, замовник міг їх заповнити — і на сайті не мінялось нічого.

    ⚠️ Фолбек на константу у фронті ЛИШАЄТЬСЯ, але тепер він ловить лише мережеву помилку
       (хедер стоїть вище за error.tsx). Порожньо в адмінці = порожньо на сайті: адмінка —
       джерело правди, і тихо підставляти інший телефон замість прибраного не можна.
    """
    lang = normalize_lang(lang)
    settings = SiteSettings.get_solo()

    return {
        "phones": [p for p in (settings.phones or []) if p],
        "email": settings.email or "",
        "address": tr(settings, "address", lang) or "",
        "working_hours": _format_hours(list(WorkingHours.objects.all()), lang),
    }
