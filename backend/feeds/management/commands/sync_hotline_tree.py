"""manage.py sync_hotline_tree

Тягне рубрикатор Hotline (hotline_tree_uk.csv, windows-1251, дерево з відступами через `;`)
і робить upsert у HotlineCategory. Без нього фід порожній: товар без прив'язки до рубрикатора
у фід не потрапляє.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from feeds import rubricator


class Command(BaseCommand):
    help = "Імпортує рубрикатор Hotline у HotlineCategory"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--file",
            help="Локальний файл рубрикатора (cp1251) замість завантаження з hotline.ua.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["file"]:
            text = Path(options["file"]).read_bytes().decode(rubricator.ENCODING)
        else:
            self.stdout.write(f"Завантажую {rubricator.TREE_URL} …")
            text = rubricator.fetch_tree()

        nodes = rubricator.parse_tree(text)
        result = rubricator.import_tree(nodes)

        leaves = sum(1 for n in nodes if n.is_leaf)
        self.stdout.write(self.style.MIGRATE_HEADING("Рубрикатор Hotline"))
        self.stdout.write(f"  Рядків у файлі:  {result['total']}")
        self.stdout.write(f"  Листових:        {leaves}")
        self.stdout.write(self.style.SUCCESS(f"  Створено:        {result['created']}"))
        self.stdout.write(f"  Оновлено:        {result['updated']}")
        self.stdout.write(f"  Деактивовано:    {result['deactivated']}")
