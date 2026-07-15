"""Продакшн: VPS, Docker Compose, Caddy як TLS-термінатор."""

from __future__ import annotations

from .base import *
from .base import (
    AWS_S3_CUSTOM_DOMAIN,
    LOGGING,
    SITE_URL,
    env,
    init_sentry,
)

DEBUG = False

# Обов'язкові у проді — впадемо на старті, а не на першому запиті.
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[SITE_URL])

# --- HTTPS (TLS тримає Caddy, тому довіряємо його заголовку) ---
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = False  # http→https редіректить Caddy, Django його вже не бачить
# ↑ саме тому security.W008 тут не є проблемою: гасимо явно, щоб `check --deploy` був чистим
# і реальна нова W-ка не загубилась серед відомого шуму.
SILENCED_SYSTEM_CHECKS = ["security.W008"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

AXES_ENABLED = True
AXES_BEHIND_REVERSE_PROXY = True
AXES_IPWARE_PROXY_COUNT = 1

# --- Сховище: Cloudflare R2 через django-storages ---
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "querystring_auth": False,
            "file_overwrite": False,
            "default_acl": None,
            "custom_domain": AWS_S3_CUSTOM_DOMAIN or None,
        },
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
    },
}

# --- Пошта ---
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@complex.ua")

# --- Логи: рівень з env, усе в stdout (збирає docker) ---
LOGGING["root"]["level"] = env("DJANGO_LOG_LEVEL", default="INFO")

init_sentry(env("SENTRY_ENVIRONMENT", default="production"))
