"""
manage.py sync_prices [--source=complex-demo] [--force] [--dry-run]

`--dry-run` — ОБОВ'ЯЗКОВИЙ крок перед першим бойовим прогоном і перед підключенням кожного
нового постачальника (SYNC.md §10): усе виконується в транзакції, наприкінці — ROLLBACK.
`SyncRun` при цьому зберігається (він створюється ПОЗА транзакцією запису) і містить повний
diff: скільки б створилось / оновилось / деактивувалось, звіт запобіжників, невідомі категорії.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from sync.models import PriceSource, SyncLogEntry, SyncRun
from sync.services import run_price_sync
from sync.sheets import is_fixture_mode


class Command(BaseCommand):
    help = "Синхронізація прайсу з Google Sheets (SYNC.md §2)"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--source", default="", help="Код джерела. Порожньо — усі активні.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Порахувати diff і відкотити транзакцію. Каталог не змінюється.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Застосувати попри попередження запобіжників (SYNC.md §2, крок 6).",
        )
        parser.add_argument(
            "--no-specs", action="store_true", help="Не читати таблицю характеристик."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        code = options["source"]
        sources = (
            PriceSource.objects.filter(code=code)
            if code
            else PriceSource.objects.filter(is_active=True)
        )
        if not sources.exists():
            raise CommandError(f"Джерело {code!r} не знайдено (або немає активних джерел).")

        if is_fixture_mode():
            self.stdout.write(
                self.style.WARNING(
                    "ФІКСТУРНИЙ РЕЖИМ: Google SA не налаштований, читаю sync/fixtures/*.csv"
                )
            )

        for source in sources.order_by("priority"):
            self.stdout.write(f"\n=== {source.name} [{source.code}] ===")
            run = run_price_sync(
                source,
                trigger=SyncRun.Trigger.MANUAL,
                dry_run=options["dry_run"],
                force=options["force"],
                fill_specs=not options["no_specs"],
            )
            self._report(run)

    def _report(self, run: SyncRun) -> None:
        style = {
            SyncRun.Status.SUCCESS: self.style.SUCCESS,
            SyncRun.Status.PARTIAL: self.style.WARNING,
            SyncRun.Status.ABORTED: self.style.ERROR,
            SyncRun.Status.FAILED: self.style.ERROR,
            SyncRun.Status.SKIPPED: self.style.WARNING,
        }.get(run.status, self.style.NOTICE)

        stats = run.stats or {}
        self.stdout.write(style(f"{run.status.upper()}  run={run.id}  {run.duration_ms} мс"))
        self.stdout.write(
            f"  рядків: прочитано {run.rows_read} / у сітці {run.rows_expected}, "
            f"пропущено {run.rows_skipped} "
            f"(секцій {stats.get('rows_section', 0)}), "
            f"биті {run.rows_failed}, дублі {run.rows_duplicated}"
        )
        self.stdout.write(
            f"  товари: створено {run.products_created}, оновлено {run.products_updated}, "
            f"знято з наявності {run.products_deactivated}"
        )
        # ЧЕРНЕТКИ (SYNC.md §4.3) — це НЕ помилки, це нормальний стан даних замовника.
        self.stdout.write(
            f"  НЕПОВНІ (сховані з сайту): {stats.get('rows_incomplete', 0)} "
            f"— без артикула {stats.get('rows_no_sku', 0)} "
            f"(з них створено з сурогатним ключем {stats.get('rows_surrogate_sku', 0)}), "
            f"без фото {stats.get('rows_no_photo', 0)}"
        )
        if stats.get("surrogates_adopted") or stats.get("products_auto_activated"):
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ДОЗАПОВНИЛИ: артикул з'явився у {stats.get('surrogates_adopted', 0)} "
                    f"товарів, автоматично показано на сайті "
                    f"{stats.get('products_auto_activated', 0)}"
                )
            )
        self.stdout.write(
            f"  фото в черзі {run.images_queued}, характеристик заповнено {run.specs_filled}"
        )

        issues = stats.get("issues") or {}
        if issues:
            from sync.models import DataIssue

            parts = ", ".join(
                f"{DataIssue.Kind(kind).label}: {count}" for kind, count in sorted(issues.items())
            )
            self.stdout.write(self.style.WARNING(f"  ПРОБЛЕМИ В ДАНИХ — {parts}"))
            self.stdout.write(
                f"  Повний список для замовника:  manage.py data_issues --run={run.id} "
                f"--csv issues.csv"
            )
        if run.guard_report:
            self.stdout.write(f"  запобіжники: {run.guard_report}")
        if run.categories_missing:
            self.stdout.write(
                self.style.WARNING(f"  НЕВІДОМІ КАТЕГОРІЇ: {', '.join(run.categories_missing)}")
            )
        if run.traceback:
            self.stdout.write(self.style.ERROR(f"  {run.traceback.strip()[:800]}"))

        levels = (SyncLogEntry.Level.WARN, SyncLogEntry.Level.ERROR)
        entries = run.entries.filter(level__in=levels).order_by("level", "row_number")[:20]
        if entries:
            self.stdout.write("  журнал (WARN/ERROR, до 20):")
            for e in entries:
                where = f"{e.sheet}:{e.row_number}" if e.row_number else e.sheet or "-"
                self.stdout.write(f"    [{e.level}] {e.action} {where} {e.sku} — {e.message[:110]}")
