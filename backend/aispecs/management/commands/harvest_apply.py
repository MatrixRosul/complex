"""Застосувати джоби SpecHarvestJob у каталог (те саме, що кнопка «Застосувати» в адмінці).

За замовчуванням застосовує всі «на перегляд». Фільтри: --ids, --category, --limit.
--dry-run лише показує, що застосувалося б.

⚠️ Це запис у каталог (source=ai). Викликати свідомо — команда, не автозапуск.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from aispecs.models import SpecHarvestJob
from aispecs.services import apply_job


class Command(BaseCommand):
    help = "Застосувати джоби ШІ-збору (за замовчуванням усі 'на перегляд')."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--ids", nargs="*", type=int, help="product_id (порожньо = всі)")
        parser.add_argument("--category", default=None, help="фільтр за шаблоном категорії")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument(
            "--dry-run", action="store_true", help="показати, що застосувалося б, без запису"
        )

    def handle(self, *args, **opts) -> None:
        qs = SpecHarvestJob.objects.filter(
            status=SpecHarvestJob.Status.NEEDS_REVIEW
        ).select_related("product")
        if opts["category"]:
            qs = qs.filter(category_key=opts["category"])
        if opts["ids"]:
            qs = qs.filter(product_id__in=opts["ids"])
        qs = qs.order_by("product__name_uk")
        if opts["limit"]:
            qs = qs[: opts["limit"]]

        applied = failed = 0
        for job in qs:
            if opts["dry_run"]:
                self.stdout.write(f"  DRY  {job.product} — {len(job.confirmed_specs())} полів")
                continue
            try:
                n = apply_job(job)
                self.stdout.write(self.style.SUCCESS(f"  ✓ {job.product} — {n} полів"))
                applied += 1
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"  ✗ {job.product}: {exc}")
                failed += 1

        if opts["dry_run"]:
            self.stdout.write(f"\nDry-run: до застосування {qs.count()} джоб.")
        else:
            self.stdout.write(
                self.style.SUCCESS(f"\nЗастосовано: {applied}. Помилок: {failed}.")
            )
