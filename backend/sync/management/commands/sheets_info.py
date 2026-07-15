"""
manage.py sheets_info — «а що ти взагалі зараз бачиш?»

Друкує АКТИВНИЙ режим авторизації (service account / oauth / фікстури) і, якщо режим не
фікстурний, — назви таблиць та їхні листи з кількістю рядків. Мережа тільки на читання
метаданих: ніяких даних, нічого не змінює. Безпечно смикати будь-коли, зокрема в проді.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from sync.sheets import (
    MODE_FIXTURES,
    MODE_OAUTH,
    MODE_SERVICE_ACCOUNT,
    OAUTH_HINT,
    SheetsError,
    fixtures_dir,
    get_client,
    oauth_client_file,
    oauth_token_file,
    sheets_mode,
)


class Command(BaseCommand):
    help = "Діагностика Google Sheets: активний режим авторизації + що видно в таблицях"

    def handle(self, *args: Any, **options: Any) -> None:
        mode = sheets_mode()
        self.stdout.write(self.style.NOTICE("=== Google Sheets ==="))

        if mode == MODE_FIXTURES:
            self._fixtures()
            return

        if mode == MODE_SERVICE_ACCOUNT:
            self.stdout.write(
                self.style.SUCCESS("Режим: SERVICE ACCOUNT — основний, штатний шлях.")
            )
        elif mode == MODE_OAUTH:
            self.stdout.write(self.style.WARNING("Режим: OAUTH (токен користувача) — ЗАПАСНИЙ."))
            self.stdout.write(
                "  Існує тому, що політика організації (iam.disableServiceAccountKeyCreation)\n"
                "  забороняє створювати ключі сервісних акаунтів.\n"
                f"  client secrets: {oauth_client_file()}\n"
                f"  токен:          {oauth_token_file()}\n"
                "  Щойно з'явиться ключ SA — синк перемкнеться на нього автоматично."
            )

        self._spreadsheets(mode)

    # -- гілки ---------------------------------------------------------------

    def _fixtures(self) -> None:
        self.stdout.write(self.style.WARNING("Режим: ФІКСТУРИ (CSV, без мережі)."))
        self.stdout.write(
            "  Ні ключа сервісного акаунта, ні OAuth-токена — синк читає локальні CSV.\n"
            "  Локально це норма. У ПРОДІ це означає, що зник секрет.\n"
            f"  Каталог фікстур: {fixtures_dir()}"
        )
        files = sorted(p.name for p in fixtures_dir().glob("*.csv"))
        for name in files:
            self.stdout.write(f"    • {name}")
        if not files:
            self.stdout.write(self.style.ERROR("    (порожньо — синку нічого читати!)"))
        self.stdout.write(
            "\n  Щоб читати РЕАЛЬНІ таблиці:\n"
            f"    • ключ сервісного акаунта → GOOGLE_SA_FILE / GOOGLE_SA_JSON, або\n"
            f"    • OAuth від імені користувача → {OAUTH_HINT}"
        )

    def _spreadsheets(self, mode: str) -> None:
        targets = [
            ("Прайс", "SHEETS_PRICE_SPREADSHEET_ID", settings.SHEETS_PRICE_SPREADSHEET_ID),
            ("Характеристики", "SHEETS_SPEC_SPREADSHEET_ID", settings.SHEETS_SPEC_SPREADSHEET_ID),
        ]
        try:
            client = get_client()
        except SheetsError as exc:
            self.stdout.write(self.style.ERROR(f"\nАвторизація зламана: {exc}"))
            return

        for label, env_name, sid in targets:
            self.stdout.write("")
            if not sid:
                self.stdout.write(self.style.WARNING(f"[{label}] {env_name} не заданий у .env."))
                continue
            try:
                meta = client.spreadsheet_meta(sid)  # type: ignore[union-attr]
            except SheetsError as exc:
                self.stdout.write(self.style.ERROR(f"[{label}] {sid} — НЕДОСТУПНА.\n  {exc}"))
                who = "сервісному акаунту" if mode == MODE_SERVICE_ACCOUNT else "твоєму акаунту"
                self.stdout.write(
                    self.style.ERROR(f"  Розшар таблицю {who} з правами «Читач» (Viewer).")
                )
                continue

            self.stdout.write(self.style.SUCCESS(f"[{label}] {meta['title']}  ({sid})"))
            for sheet in meta["sheets"]:
                self.stdout.write(
                    f"    • {sheet['title']}  — {sheet['row_count']} рядків "
                    f"× {sheet['column_count']} колонок"
                )
