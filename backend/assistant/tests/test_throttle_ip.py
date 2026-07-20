"""Per-IP тротлінг має бути НЕ обхідним підміною X-Forwarded-For.

Публічний неавторизований ендпоінт за платною моделлю — це відкритий гаманець.
`AnonRateThrottle("20/m")` захищає лише тоді, коли ключ тротлу — стабільний IP.
Дефолтний `NINJA_NUM_PROXIES=None` ламає це: ninja бере як «особистість» увесь
заголовок X-Forwarded-For, який шле сам клієнт, — і 20/хв обходиться зміною заголовка
в кожному запиті. Тому `NINJA_NUM_PROXIES` мусить бути заданий явно (settings/base.py).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from ninja.throttling import AnonRateThrottle


class _Req:
    def __init__(self, xff: str | None, remote: str) -> None:
        self.META: dict[str, Any] = {"REMOTE_ADDR": remote}
        if xff is not None:
            self.META["HTTP_X_FORWARDED_FOR"] = xff


def test_num_proxies_is_configured() -> None:
    """NINJA_NUM_PROXIES заданий явно (не None) — інакше XFF приймається як є."""
    assert getattr(settings, "NINJA_NUM_PROXIES", None) is not None, (
        "NINJA_NUM_PROXIES не налаштований → per-IP тротл обходиться підміною "
        "X-Forwarded-For. Задай його в settings (дзеркально до AXES_BEHIND_REVERSE_PROXY)."
    )


def test_spoofed_forwarded_for_does_not_change_ident() -> None:
    """Дев (0 проксі): крути XFF як хочеш — ключ тротлу лишається реальним REMOTE_ADDR."""
    throttle = AnonRateThrottle("20/m")
    real = "203.0.113.7"
    idents = {
        throttle.get_ident(_Req(spoof, real))  # type: ignore[arg-type]
        for spoof in ("1.1.1.1", "2.2.2.2", "junk", None)
    }
    assert idents == {real}, f"підміна XFF дала різні ключі тротлу: {idents}"
