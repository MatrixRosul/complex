"""Верхня смуга шапки стає КЕРОВАНОЮ З АДМІНКИ.

До цієї міграції смуга («Доставка та оплата», «Гарантія», «Контакти») була захардкоджена
списком ключів у `frontend/src/components/layout/header.tsx`. Наслідок, який замовник і
помітив: пункт, доданий в адмінці, не з'являвся НІДЕ — фронт відкидав усе, чого не було
в тому списку. Тепер смуга рендерить зону «Верхнє меню», тож пункти треба в ній завести.

⚠️ Порядок — той, який замовниця продиктувала дослівно, і саме він лягає в `sort_order`.

⚠️ Прив'язка через `static_page`, а не через ручний `url`: модель будує посилання зі
   сторінки (`/page/{key}`), тому перейменування чи зміна сторінки не лишає пункт із
   мертвим посиланням. Сторінки, якої в БД немає, пункт не отримує взагалі — станом на
   зараз це `credit` («Кредит або розстрочка»), і замовник заведе його сам.

Ідемпотентна: пункт, який уже стоїть у зоні header для тієї ж сторінки, не дублюється.
"""

from __future__ import annotations

from django.db import migrations

# (ключ сторінки, запасна назва). Назва береться зі сторінки; запасна потрібна лише тоді,
# коли в самої сторінки порожній заголовок.
TOPBAR: tuple[tuple[str, str], ...] = (
    ("payment-delivery", "Доставка та оплата"),
    ("credit", "Кредит або розстрочка"),
    ("warranty", "Гарантія"),
    ("contacts", "Контакти"),
)


def seed_topbar(apps, schema_editor):
    MenuItem = apps.get_model("cms", "MenuItem")
    StaticPage = apps.get_model("cms", "StaticPage")

    for index, (key, fallback_title) in enumerate(TOPBAR):
        page = StaticPage.objects.filter(key=key).first()
        if page is None:
            continue  # сторінки немає → пункт вів би в 404
        if MenuItem.objects.filter(zone="header", static_page=page).exists():
            continue

        # Заголовки перекладні: пишемо обидві мовні колонки явно. `title` без `title_uk`
        # означав би порожній пункт у меню — `tr()` шукає саме `title_uk`.
        title_uk = getattr(page, "title_uk", None) or page.title or fallback_title
        title_ru = getattr(page, "title_ru", None) or ""
        MenuItem.objects.create(
            zone="header",
            static_page=page,
            title=title_uk,
            title_uk=title_uk,
            title_ru=title_ru,
            sort_order=index * 10,
            is_active=True,
        )


def unseed_topbar(apps, schema_editor):
    MenuItem = apps.get_model("cms", "MenuItem")
    MenuItem.objects.filter(zone="header", static_page__key__in=[k for k, _ in TOPBAR]).delete()


class Migration(migrations.Migration):
    dependencies = [("cms", "0005_banner_real_placements_and_crop")]

    operations = [migrations.RunPython(seed_topbar, unseed_topbar)]
