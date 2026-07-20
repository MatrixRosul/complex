"""Локальна розробка. PostgreSQL 14 + Redis на localhost."""

from __future__ import annotations

from .base import *
from .base import INSTALLED_APPS, MIDDLEWARE, env, init_sentry

DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = ["*"]

# Адмінка локально відкривається і напряму (:8001), і через проксі фронта (:3001).
# Django 4+ звіряє заголовок Origin на POST зі списком довірених — без цього вхід і
# кнопки адмінки («Застосувати») падають з CSRF 403 «Origin checking failed».
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:8001",
    "http://127.0.0.1:8001",
]

# Локально ліміт спроб входу тільки заважає.
AXES_ENABLED = env.bool("AXES_ENABLED", default=False)


# --- django-debug-toolbar ---
def _show_toolbar(request) -> bool:
    """Панель — тільки при ЖИВОМУ DEBUG, а не при тому, який був на момент імпорту settings.

    ⚠️ Тут був `lambda request: DEBUG`, і це ламало КОЖЕН тест, який рендерить сторінку
       адмінки. Тест-раннер вимикає DEBUG (`settings.DEBUG = False`) вже після імпорту
       модуля, тому:
         · `config/urls.py` НЕ підключав `__debug__/` (там перевірка в момент імпорту),
         · а замикання лямбди тримало старе `True`, тож middleware все одно вставляв панель
       → рендер падав з `NoReverseMatch: djdt:render_panel`, і виглядало це як «зламана
       адмінка», хоча ламався лише тулбар. Читаємо DEBUG з `django.conf.settings` наживо.
    """
    from django.conf import settings as active_settings

    return bool(active_settings.DEBUG)


if DEBUG:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    INTERNAL_IPS = ["127.0.0.1", "::1"]
    DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": _show_toolbar}

# Пошта — у консоль.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Медіа — локальна ФС, поки не ввімкнено R2 явно.
if env.bool("USE_R2", default=False):
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

# Celery: за замовчуванням задачі йдуть у Redis; CELERY_TASK_ALWAYS_EAGER=True — виконуються синхронно.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = True

# Без DSN Sentry просто не вмикається.
init_sentry("local")
