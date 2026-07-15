"""Кореневий URLConf.

Адмінка живе за ADMIN_URL з env (НЕ /admin/ — ARCHITECTURE §5).
i18n_patterns не використовуємо: мовні URL (/ua/, /ru/) — зона відповідальності Next.js,
Django віддає API, а мова приходить заголовком Accept-Language або параметром.
"""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from config.api import api


def healthz(request):
    """Проба для Caddy / docker healthcheck."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    # set_language: перемикач мови в адмінці (Unfold рендерить його, бо USE_I18N=True).
    # Без цього /admin/ падає з NoReverseMatch на unfold/helpers/language_form.html.
    path("i18n/", include("django.conf.urls.i18n")),
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/v1/", api.urls),
    path("healthz", healthz, name="healthz"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    if "debug_toolbar" in settings.INSTALLED_APPS:
        urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
