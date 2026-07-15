"""
manage.py data_issues [--run=<uuid>] [--type=duplicate_sku] [--csv issues.csv] [--limit=50]

ЗВІТ ПРО ПРОБЛЕМИ В ДАНИХ ПРАЙСУ — для ЗАМОВНИКА, не для розробника.

Замовник ніколи не відкриє журнал синхронізацій (сотні рядків, коди, payload). Його слова:

    «І може таке бути, що я десь провтикав і два товари будуть з одним артикулом.
     Якщо тобі це відобразиться десь, скажи плз, я виправлю»

Тому тут — простий список: ЩО не так, У ЯКОМУ ЛИСТІ, В ЯКОМУ РЯДКУ, ЩО ЗРОБИТИ.
`--csv` віддає файл, який можна просто кинути замовнику в месенджер: він відкриє його
в Google Sheets і піде правити рядок за рядком.

Типово показує проблеми ОСТАННЬОГО прогону прайсу (у т.ч. dry-run — DataIssue переживає
відкат транзакції саме заради цього).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import IO, Any

from django.core.management.base import BaseCommand, CommandError

from sync.models import DataIssue, SyncRun

#: Порядок виводу: спершу те, що ламає дані, потім те, що просто ховає товар.
KIND_ORDER = (
    DataIssue.Kind.DUPLICATE_SKU,
    DataIssue.Kind.MISSING_CATEGORY,
    DataIssue.Kind.UNKNOWN_CATEGORY,
    DataIssue.Kind.MISSING_SKU,
    DataIssue.Kind.MISSING_PHOTO,
    DataIssue.Kind.SPEC_NOT_FOUND,
)

CSV_COLUMNS = ("Тип проблеми", "Лист", "Рядок", "Артикул", "Назва товару", "Що зробити")

#: Кодування файла звіту. utf-8-sig (= BOM): без нього Excel відкриває кирилицю кракозябрами,
#: і весь сенс «кинути файл замовнику» зникає.
CSV_ENCODING = "utf-8-sig"


def write_issues_csv(issues: Any, stream: IO[str]) -> int:
    """
    Записати звіт про проблеми у ТЕКСТОВИЙ потік. Повертає кількість рядків (без заголовка).

    Живе тут, а не в адмінці, бо це та сама таблиця, що й у `--csv`: файл, який замовник
    відкриває в Google Sheets. Кнопка «Експортувати у CSV» в адмінці (sync.admin.DataIssueAdmin)
    викликає РІВНО цю функцію — щоб дві вивантажки ніколи не розійшлись колонками.
    """
    writer = csv.writer(stream)
    writer.writerow(CSV_COLUMNS)
    count = 0
    for issue in issues.order_by("kind", "sheet", "row_number"):
        writer.writerow(
            [
                DataIssue.Kind(issue.kind).label,
                issue.sheet,
                issue.row_number or "",
                issue.sku,
                issue.name,
                issue.message,
            ]
        )
        count += 1
    return count


class Command(BaseCommand):
    help = "Звіт про проблеми в даних прайсу (для замовника). CSV: --csv issues.csv"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--run", default="", help="UUID прогону. Порожньо → останній.")
        parser.add_argument(
            "--type",
            default="",
            choices=["", *DataIssue.Kind.values],
            help="Показати лише проблеми цього типу.",
        )
        parser.add_argument("--csv", default="", help="Записати повний звіт у CSV-файл.")
        parser.add_argument(
            "--limit", type=int, default=20, help="Скільки рядків показувати на тип (типово 20)."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        run = self._get_run(options["run"])
        issues = DataIssue.objects.filter(run=run)
        if options["type"]:
            issues = issues.filter(kind=options["type"])

        self.stdout.write(
            f"\nПрогін {run.id} від {run.started_at:%d.%m.%Y %H:%M}"
            f"{' (ПРОБНИЙ)' if run.dry_run else ''} — статус {run.status}"
        )

        # Один SELECT на всі типи: групування в пам'яті. Проблем — сотні, не мільйони.
        by_kind: dict[str, list[DataIssue]] = {}
        for issue in issues.order_by("sheet", "row_number", "id"):
            by_kind.setdefault(issue.kind, []).append(issue)

        total = sum(len(g) for g in by_kind.values())
        if not total:
            self.stdout.write(self.style.SUCCESS("Проблем у даних не знайдено. 🎉"))
            return

        self.stdout.write(self.style.WARNING(f"Знайдено проблем: {total}\n"))

        for kind in KIND_ORDER:
            group = by_kind.get(kind, [])
            if not group:
                continue

            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"\n=== {DataIssue.Kind(kind).label.upper()} — {len(group)} ==="
                )
            )
            for issue in group[: options["limit"]]:
                where = f"{issue.sheet}, рядок {issue.row_number}" if issue.row_number else "—"
                title = issue.name or issue.sku or "—"
                self.stdout.write(f"  • [{where}] {title}")
                self.stdout.write(f"    {issue.message}")
            if len(group) > options["limit"]:
                self.stdout.write(
                    f"  … ще {len(group) - options['limit']}. Повний список — у --csv"
                )

        if options["csv"]:
            path = self._write_csv(issues, Path(options["csv"]))
            self.stdout.write(
                self.style.SUCCESS(f"\nCSV зі ВСІМА проблемами ({total}): {path.resolve()}")
            )
            self.stdout.write("Цей файл можна відкрити в Google Sheets і віддати замовнику.")

    def _get_run(self, run_id: str) -> SyncRun:
        if run_id:
            run = SyncRun.objects.filter(pk=run_id).first()
            if run is None:
                raise CommandError(f"Прогону {run_id} не існує.")
            return run

        run = (
            SyncRun.objects.filter(kind=SyncRun.Kind.SHEETS_PRICES).order_by("-started_at").first()
        )
        if run is None:
            raise CommandError("Жодного прогону прайсу ще не було. Запусти sync_prices --dry-run.")
        return run

    def _write_csv(self, issues: Any, path: Path) -> Path:
        with path.open("w", encoding=CSV_ENCODING, newline="") as fh:
            write_issues_csv(issues, fh)
        return path
