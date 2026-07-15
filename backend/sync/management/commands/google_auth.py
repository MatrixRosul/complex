"""
manage.py google_auth — РАЗОВА інтерактивна авторизація в Google Sheets від імені користувача.

НАВІЩО. Політика Google Cloud організації (`iam.disableServiceAccountKeyCreation`) забороняє
створювати ключі сервісних акаунтів, тому стандартний шлях (SA + JSON-ключ) зараз недоступний.
Обхід: ходимо в Sheets токеном живого користувача, якому таблиці розшарені на перегляд.

Це ЗАПАСНИЙ шлях. Коли політику знімуть — просто покладіть ключ SA у GOOGLE_SA_FILE, і синк
перемкнеться на нього сам (sync/sheets.py::get_client, пріоритет SA → OAuth → фікстури).

Запускати РУКАМИ, з машини з браузером: команда підніме локальний сервер і відкриє браузер.
Токен зберігається у GOOGLE_OAUTH_TOKEN_FILE (дефолт backend/google_token.json, права 0600).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sync.sheets import (
    SCOPES,
    GoogleSheetsClient,
    SheetsError,
    oauth_client_file,
    oauth_token_file,
    save_oauth_token,
)


class Command(BaseCommand):
    help = "Разова OAuth-авторизація в Google Sheets (запасний шлях замість ключа SA)"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--no-browser",
            action="store_true",
            help="Не відкривати браузер — надрукувати посилання, відкрити вручну.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        client_file = oauth_client_file()
        token_file = oauth_token_file()

        if not client_file.is_file():
            raise CommandError(
                f"Немає файла OAuth-клієнта: {client_file}\n"
                "Це JSON типу «installed» (Desktop app) з Google Cloud Console →\n"
                "  APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app.\n"
                "Поклади його за цим шляхом або вкажи GOOGLE_OAUTH_CLIENT_FILE у .env."
            )

        self._intro(token_file)
        creds = self._run_flow(client_file, open_browser=not options["no_browser"])

        if not creds.refresh_token:
            # Без refresh_token токен помре за годину — і синк почне падати вночі.
            raise CommandError(
                "Google не видав refresh_token. Зайди на https://myaccount.google.com/permissions,\n"
                "прибери доступ додатка «Complex» і запусти команду ще раз."
            )

        path = save_oauth_token(creds, token_file)
        self.stdout.write(self.style.SUCCESS(f"\n✔ Токен збережено: {path} (права 0600)"))
        self.stdout.write(
            self.style.WARNING("  Це секрет: доступ до таблиць БЕЗ пароля. Не комітити.")
        )

        self._verify(creds)

    # -- кроки ---------------------------------------------------------------

    def _intro(self, token_file: Any) -> None:
        self.stdout.write(self.style.NOTICE("\n=== OAuth-авторизація Google Sheets ==="))
        self.stdout.write(
            "Зараз відкриється браузер. Що робити:\n"
            "  1. Увійди тим акаунтом Google, якому РОЗШАРЕНІ таблиці (на перегляд).\n"
            "  2. Google покаже екран «Google hasn't verified this app» / «Додаток не підтверджено».\n"
            "     Це НОРМАЛЬНО: додаток наш, внутрішній, верифікацію ми не проходили.\n"
            "     Тисни  Advanced (Додатково) → Go to Complex (unsafe) / Перейти на сторінку Complex.\n"
            "  3. Погодь доступ «Перегляд таблиць Google» (тільки читання — писати ми не просимо).\n"
            f"  4. Вкладка скаже, що можна закривати. Токен ляже у {token_file}\n"
        )

    def _run_flow(self, client_file: Any, *, open_browser: bool) -> Any:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:  # pragma: no cover
            raise CommandError("Немає google-auth-oauthlib. Запусти: uv sync") from exc

        flow = InstalledAppFlow.from_client_secrets_file(str(client_file), list(SCOPES))
        try:
            # access_type=offline + prompt=consent — ОБОВ'ЯЗКОВО: інакше Google не віддасть
            # refresh_token, і токен помре за годину. port=0 — вільний порт від ОС.
            creds = flow.run_local_server(
                port=0,
                open_browser=open_browser,
                access_type="offline",
                prompt="consent",
                authorization_prompt_message=(
                    "Відкрий у браузері, якщо він не відкрився сам:\n{url}"
                ),
                success_message=(
                    "Готово. Токен отримано — можна закрити вкладку й повернутись у термінал."
                ),
            )
        except Exception as exc:  # будь-який збій flow → зрозуміле повідомлення, не traceback
            raise CommandError(f"Авторизація не вдалася: {exc}") from exc
        return creds

    def _verify(self, creds: Any) -> None:
        """Одразу читаємо метадані ОБОХ таблиць — миттєвий доказ, що доступ реально працює."""
        targets = [
            ("Прайс", "SHEETS_PRICE_SPREADSHEET_ID", settings.SHEETS_PRICE_SPREADSHEET_ID),
            ("Характеристики", "SHEETS_SPEC_SPREADSHEET_ID", settings.SHEETS_SPEC_SPREADSHEET_ID),
        ]
        client = GoogleSheetsClient(creds)
        self.stdout.write(self.style.NOTICE("\n=== Перевірка доступу до таблиць ==="))

        failed = 0
        for label, env_name, sid in targets:
            if not sid:
                self.stdout.write(
                    self.style.WARNING(f"\n[{label}] {env_name} не заданий у .env — пропускаю.")
                )
                continue
            try:
                meta = client.spreadsheet_meta(sid)
            except SheetsError as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"\n[{label}] {sid} — ДОСТУПУ НЕМАЄ.\n  {exc}"))
                self.stdout.write(
                    self.style.ERROR(
                        f"  Відкрий таблицю {sid} → Share → додай акаунт, яким ти щойно\n"
                        "  авторизувався, з правами «Viewer» (Читач). Потім запусти:\n"
                        "    uv run python manage.py sheets_info"
                    )
                )
                continue

            self.stdout.write(self.style.SUCCESS(f"\n[{label}] {meta['title']}  ({sid})"))
            for sheet in meta["sheets"]:
                self.stdout.write(
                    f"    • {sheet['title']}  — {sheet['row_count']} рядків "
                    f"× {sheet['column_count']} колонок"
                )

        if failed:
            raise CommandError(
                f"\nТокен отримано, але до {failed} таблиц(і/ь) доступу немає — див. вище. "
                "Розшар їх і перевір: uv run python manage.py sheets_info"
            )
        self.stdout.write(
            self.style.SUCCESS("\n✔ Доступ працює. Синк тепер читає Sheets через OAuth.")
        )
