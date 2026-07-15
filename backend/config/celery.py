"""Celery-застосунок Complex.

Черги (див. SYNC.md §8):
    sync    (-c 1)  — прайси, характеристики, recalc, rollback.  Серіалізація — advisory-lock у PG.
    images  (-c 2)  — завантаження фото, rate_limit 20/s.
    ai      (-c 1)  — Claude: переклад uk→ru, збір характеристик.  Бюджетний ліміт.
    default (-c 4)  — денорм, лічильники, фіди, heal/watchdog, purge.
"""

from __future__ import annotations

import os

from celery import Celery
from celery.signals import setup_logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("complex")

# Усі CELERY_* із Django-налаштувань.
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@setup_logging.connect
def config_loggers(*args, **kwargs):
    """Celery не перевизначає наш LOGGING з settings."""
    from logging.config import dictConfig

    from django.conf import settings

    dictConfig(settings.LOGGING)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
