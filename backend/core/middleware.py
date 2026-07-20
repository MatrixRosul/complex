"""Middleware ядра."""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils import translation


class ForceAdminLanguageMiddleware:
    """Адмінка — ЗАВЖДИ українською, незалежно від Accept-Language браузера.

    Інтерфейс адмінки одномовний (свідоме рішення, див. UNFOLD["SHOW_LANGUAGES"] у settings).
    Без цього LocaleMiddleware обирає мову за заголовком браузера: менеджер з ru-браузером
    отримував російський хром, у списку товарів колонку `name_ru` (часто порожню), а на
    картці — активний RU-таб modeltranslation, тобто відкриту ПОРОЖНЮ колонку замість
    заповненої української. Таб-JS обирає активний таб за `<html lang>`, а той — за активною
    мовою запиту; пін на uk відкриває саме український таб.

    Сайт це НЕ зачіпає: API читає мову явним аргументом (`catalog/services/lang.py`), а не з
    активної мови потоку, а фронт — окремий Next.js. Тому форсуємо лише під ADMIN_URL.

    ⚠️ Має стояти в MIDDLEWARE ПІСЛЯ django.middleware.locale.LocaleMiddleware — інакше той
       перезапише активну мову вже після нас.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self._admin_prefix = "/" + settings.ADMIN_URL.lstrip("/")

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path.startswith(self._admin_prefix):
            translation.activate(settings.LANGUAGE_CODE)
            request.LANGUAGE_CODE = settings.LANGUAGE_CODE
        return self.get_response(request)
