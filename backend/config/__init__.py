"""Ініціалізація Celery разом із Django: `from config import celery_app` доступний скрізь."""

from .celery import app as celery_app

__all__ = ("celery_app",)
