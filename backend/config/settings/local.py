"""Локальна розробка. PostgreSQL 14 + Redis на localhost."""

from __future__ import annotations

from .base import *
from .base import INSTALLED_APPS, MIDDLEWARE, env, init_sentry

DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = ["*"]

# Локально ліміт спроб входу тільки заважає.
AXES_ENABLED = env.bool("AXES_ENABLED", default=False)

# --- django-debug-toolbar ---
if DEBUG:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    INTERNAL_IPS = ["127.0.0.1", "::1"]
    DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": lambda request: DEBUG}

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
