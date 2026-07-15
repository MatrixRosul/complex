"""
manage.py sync_np_refs [--dry-run]

Ручний запуск нічного синку довідників НП. Той самий код, що і в Celery-beat (03:00) —
жодного другого шляху виконання.

`--dry-run` тягне довідники, ганяє guard і smoke-тест, друкує, що ЗМІНИЛОСЬ БИ, і
відкочує транзакцію. Безпечно запускати на проді.

Без ключа НП (`NP_API_KEY` порожній) працює на фікстурах `delivery/fixtures/np/` —
саме так це і перевіряється в CI.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from delivery.client import NovaPoshtaError, NovaPoshtaUnavailable, get_api_key, get_sync_client
from delivery.tasks import sync_np_refs


class Command(BaseCommand):
    help = "Синхронізує довідники Нової Пошти (області, міста, НП, відділення) в нашу БД."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Порахувати зміни й відкотити транзакцію (БД не змінюється).",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        dry_run = bool(opts["dry_run"])
        mode = "ФІКСТУРИ (немає NP_API_KEY)" if not get_api_key() else "ЖИВЕ API НП"
        self.stdout.write(f"Джерело даних: {mode}")
        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: БД НЕ буде змінено"))

        try:
            result = sync_np_refs(dry_run=dry_run, client=get_sync_client())
        except (NovaPoshtaUnavailable, NovaPoshtaError) as exc:
            raise CommandError(f"Нова Пошта не віддала довідники: {exc}") from exc
        except AssertionError as exc:
            raise CommandError(f"Smoke-тест не пройдено, усе відкочено: {exc}") from exc

        status = result["status"]
        if status == "aborted":
            self.stdout.write(self.style.ERROR("ABORTED — спрацював guard, довідник НЕ ЧІПАЛИ:"))
            for failure in result["guard"]["failures"]:
                self.stdout.write(self.style.ERROR(f"  • {failure}"))
            raise CommandError("Синк зупинено запобіжником.")

        if status == "skipped":
            self.stdout.write(self.style.WARNING("SKIPPED — інший прогін уже виконується."))
            return

        self.stdout.write(self.style.SUCCESS(f"Статус: {status}  (run_id={result['run_id']})"))
        for key, value in sorted(result.get("stats", {}).items()):
            self.stdout.write(f"  {key:<32} {value}")
        if dry_run:
            self.stdout.write(self.style.WARNING("Транзакцію відкочено — БД не змінено."))
