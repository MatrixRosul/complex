"""manage.py generate_feed [--dry-run]

Ручна генерація фіда Hotline. На відміну від Celery-задачі, ігнорує тумблер
SiteSettings.hotline_enabled: це явна дія людини, а не розклад.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from feeds import services
from sync.models import SyncRun


class Command(BaseCommand):
    help = "Генерує XML-фід Hotline у media/feeds/hotline.xml"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Побудувати XML і показати лічильники, але НЕ писати файл і НЕ зберігати артефакт.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run: bool = options["dry_run"]

        artifact = services.generate_hotline_feed(trigger=SyncRun.Trigger.MANUAL, dry_run=dry_run)

        mode = "DRY-RUN (файл не записано)" if dry_run else str(services.feed_path())
        self.stdout.write(self.style.MIGRATE_HEADING("Фід Hotline"))
        self.stdout.write(f"  Файл:            {mode}")
        self.stdout.write(f"  Розмір:          {artifact.size_bytes} байт")
        self.stdout.write(f"  Тривалість:      {artifact.duration_ms} мс")
        self.stdout.write(self.style.SUCCESS(f"  Товарів у фіді:  {artifact.items_count}"))
        self.stdout.write(f"  Пропущено:       {artifact.skipped_count}")

        for reason, count in sorted(artifact.skipped_reasons.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"    · {reason:<24} {count}")

        # ⚡ НЕ причина скіпу — лічильник. Порожній mpn не викидає товар з фіда (ADR-013).
        self.stdout.write(
            f"  Без mpn (<code> не віддано): {artifact.no_mpn_count} з {artifact.items_count}"
        )
        self.stdout.write(f"  На дефолтних габаритах:      {artifact.default_dims_count}")

        if not dry_run and artifact.items_count == 0:
            self.stdout.write(
                self.style.WARNING(
                    "  Фід ПОРОЖНІЙ. Найчастіша причина — не імпортовано рубрикатор "
                    "(manage.py sync_hotline_tree) або категоріям не проставлено hotline_category."
                )
            )
