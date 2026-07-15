"""
manage.py sync_categories [--source=complex-main] [--spreadsheet=<id>] [--tab=Категорії] [--dry-run]

Імпорт ДЕРЕВА КАТЕГОРІЙ з листа «Категорії» файла характеристик («База даних (Актуальна)»).

Навіщо: у колонці «Категорія» прайсу стоять ID виду `c50549829`. Поки цього ID немає в
дереві `catalog.Category`, товар осідає в службовій категорії «__unmapped__» і на сайт
НЕ ПОТРАПЛЯЄ (вона `is_active=False`). Довідник із 70 рядків закриває всі 37 категорій,
які реально вживаються в прайсі, — тобто знімає проблему «невідома категорія» цілком.

Команда ІДЕМПОТЕНТНА: повторний запуск нічого не змінює (`unchanged`).
Категорії, яких у листі більше немає, НЕ видаляються (на них є FK з товарів, PROTECT) —
вони просто лишаються; про такі випадки команда повідомляє.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from sync.models import PriceSource
from sync.services import CATEGORY_TAB, import_categories
from sync.sheets import SheetsError, is_fixture_mode


class Command(BaseCommand):
    help = "Імпорт довідника категорій з листа «Категорії» (SYNC.md §6.6)"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--source",
            default="",
            help="Код джерела — ID таблиці візьметься з його SpecSheet. "
            "Порожньо → перше активне джерело.",
        )
        parser.add_argument(
            "--spreadsheet", default="", help="ID Google-таблиці (перекриває --source)."
        )
        parser.add_argument(
            "--tab", default=CATEGORY_TAB, help=f"Назва листа (типово {CATEGORY_TAB})"
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Показати diff і відкотити транзакцію."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        spreadsheet_id = options["spreadsheet"] or self._spreadsheet_from_source(options["source"])

        if is_fixture_mode():
            self.stdout.write(
                self.style.WARNING("ФІКСТУРНИЙ РЕЖИМ: читаю sync/fixtures/Категорії.csv")
            )

        self.stdout.write(f"Довідник категорій: {spreadsheet_id} / лист «{options['tab']}»")

        try:
            stats = import_categories(spreadsheet_id, options["tab"], dry_run=options["dry_run"])
        except SheetsError as exc:
            raise CommandError(f"Не вдалося прочитати довідник: {exc}") from exc

        style = self.style.WARNING if options["dry_run"] else self.style.SUCCESS
        prefix = "DRY-RUN (нічого не збережено): " if options["dry_run"] else ""
        self.stdout.write(
            style(
                f"{prefix}рядків {stats.rows}, створено {stats.created}, "
                f"оновлено {stats.updated}, без змін {stats.unchanged}, "
                f"пропущено {stats.skipped}"
            )
        )
        for problem in stats.problems:
            self.stdout.write(self.style.WARNING(f"  ⚠ {problem}"))

    def _spreadsheet_from_source(self, code: str) -> str:
        """ID таблиці характеристик береться з конфігу джерела, а не з константи в коді."""
        sources = (
            PriceSource.objects.filter(code=code)
            if code
            else PriceSource.objects.filter(is_active=True)
        )
        source = sources.order_by("priority", "id").first()
        if source is None:
            raise CommandError(f"Джерело {code!r} не знайдено (або немає активних джерел).")

        spec_sheet = source.spec_sheets.filter(is_active=True).first()
        if spec_sheet is None or not spec_sheet.spreadsheet_id:
            raise CommandError(
                f"У джерела «{source.code}» немає активної таблиці характеристик. "
                f"Вкажіть --spreadsheet=<id> явно."
            )
        return str(spec_sheet.spreadsheet_id)
