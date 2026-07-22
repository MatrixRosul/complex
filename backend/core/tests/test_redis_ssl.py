"""`rediss://` без `ssl_cert_reqs` = мертва адмінка на проді.

Heroku Redis — TLS із самопідписаним сертифікатом. redis-py за замовчуванням вимагає
перевірки ланцюжка і падає з CERTIFICATE_VERIFY_FAILED. Сесії в проєкті — `cached_db`,
тобто ходять у кеш, тож ламається НЕ якась одна сторінка, а вся адмінка під логіном:
кожна віддає 500. Симптом виглядав як «падає /admin/sync/usdratechange» — сторінка була
ні до чого.

Тест дешевий і ловить регресію, яку інакше видно лише на проді.
"""

from __future__ import annotations

import pytest

from config.settings.base import _redis_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Локальний Redis без TLS — не чіпаємо.
        ("redis://localhost:6379/1", "redis://localhost:6379/1"),
        # Heroku: параметр дописується.
        ("rediss://h:18080/1", "rediss://h:18080/1?ssl_cert_reqs=none"),
        # Уже є query — додаємо через &, а не другий '?'.
        ("rediss://h:18080/1?foo=bar", "rediss://h:18080/1?foo=bar&ssl_cert_reqs=none"),
        # Явно заданий режим поважаємо: раптом колись буде нормальний сертифікат.
        ("rediss://h:18080/1?ssl_cert_reqs=required", "rediss://h:18080/1?ssl_cert_reqs=required"),
    ],
)
def test_redis_url_ssl_param(url: str, expected: str) -> None:
    assert _redis_url(url) == expected


def test_cache_location_is_patched_for_tls() -> None:
    """Значення саме `none` малими — redis-py приймає лише none/optional/required."""
    assert "ssl_cert_reqs=none" in _redis_url("rediss://x/1")
