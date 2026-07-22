"""Скидання кешу Next після змін в адмінці.

🔴 ЧОМУ ЦЕ ВАЖЛИВО. Next кешує відповіді каталогу на годину (`TTL.tree = 3600`).
Свого Redis нам мало: ми чистимо СВІЙ кеш, а фронт віддає старе ще до 60 хв. Для
замовника це виглядало як зламана функція — він завантажував емблему категорії,
оновлював сторінку й бачив типовий значок («міні-іконки не працюють»).

Змінна `NEXT_REVALIDATE_URL` жила в конфігу давно, але маршруту `/api/revalidate`
не існувало, і бекенд його не викликав — механізм був лише на папері.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from catalog.services.cache import DEFAULT_REVALIDATE_TAGS, revalidate_frontend


def test_skipped_when_url_empty(settings) -> None:
    """Порожній URL — штатний режим локалі: жодних мережевих спроб."""
    settings.NEXT_REVALIDATE_URL = ""
    with patch("catalog.services.cache.requests.post") as post:
        revalidate_frontend()
    post.assert_not_called()


def test_posts_tags_with_secret(settings) -> None:
    settings.NEXT_REVALIDATE_URL = "http://frontend/api/revalidate"
    settings.NEXT_REVALIDATE_SECRET = "s3cret"  # noqa: S105 — тестова фікстура, не секрет

    with patch("catalog.services.cache.requests.post") as post:
        revalidate_frontend()

    post.assert_called_once()
    kwargs = post.call_args.kwargs
    assert kwargs["json"] == {"tags": DEFAULT_REVALIDATE_TAGS}
    assert kwargs["headers"]["X-Revalidate-Secret"] == "s3cret"
    # Людина в адмінці не має чекати на мережу фронту.
    assert kwargs["timeout"] <= 5


def test_custom_tags(settings) -> None:
    settings.NEXT_REVALIDATE_URL = "http://frontend/api/revalidate"
    with patch("catalog.services.cache.requests.post") as post:
        revalidate_frontend(["collections:uk"])
    assert post.call_args.kwargs["json"] == {"tags": ["collections:uk"]}


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "javascript:alert(1)"])
def test_non_http_url_refused(settings, url: str) -> None:
    """URL з конфігу — не привід ходити куди завгодно."""
    settings.NEXT_REVALIDATE_URL = url
    with patch("catalog.services.cache.requests.post") as post:
        revalidate_frontend()
    post.assert_not_called()


def test_network_error_does_not_break_admin_save(settings) -> None:
    """Фронт лежить → збереження в адмінці все одно проходить (деградуємо до TTL)."""
    settings.NEXT_REVALIDATE_URL = "http://frontend/api/revalidate"
    with patch("catalog.services.cache.requests.post", side_effect=OSError("down")):
        revalidate_frontend()  # не має кинути
