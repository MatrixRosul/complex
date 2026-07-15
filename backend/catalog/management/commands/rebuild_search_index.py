"""Перебудова `search_vector_uk` / `search_vector_ru`.

Тимчасовий вхід для того, що згодом робитиме Celery-задача `rebuild_product_denorm`
(ADR-011). Поки її немає, вектори треба вміти побудувати руками — інакше /search мовчить.

    uv run python manage.py rebuild_search_index
    uv run python manage.py rebuild_search_index --dirty-only
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from catalog.models import Product
from catalog.services.search_index import rebuild_search_vectors


class Command(BaseCommand):
    help = "Перебудовує повнотекстові вектори товарів (uk + ru)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dirty-only",
            action="store_true",
            help="Тільки товари з denorm_dirty=True.",
        )
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args: Any, **options: Any) -> None:
        qs = Product.objects.all()
        if options["dirty_only"]:
            qs = qs.filter(denorm_dirty=True)

        total = rebuild_search_vectors(qs, batch_size=options["batch_size"])
        self.stdout.write(self.style.SUCCESS(f"search_vector перебудовано: {total} товар(ів)"))
