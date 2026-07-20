"""Налаштування і перевірка Telegram-алертів (ADR-019).

Закриває найнуднішу частину підключення: `chat_id` ніде не показують — його треба
дістати з `getUpdates` ПІСЛЯ того, як боту написали. Без цієї команди setup виглядає
як серія ручних curl'ів із токеном у shell-історії.

    # 1. Хто ми? (перевірка токена)
    uv run python manage.py telegram_setup --whoami

    # 2. Кому можемо писати? (спершу напишіть боту або додайте його в групу)
    uv run python manage.py telegram_setup --chats

    # 3. Перевірка наскрізь — реальне повідомлення в налаштований чат
    uv run python manage.py telegram_setup --send

⚠️ ТОКЕН НЕ ПРИЙМАЄТЬСЯ АРГУМЕНТОМ — тільки з `TELEGRAM_BOT_TOKEN`. Аргумент осів би у
   `~/.zsh_history` і в логах CI. Це той самий принцип, що й у решті проєкту: секрети
   живуть в env, а не в БД, коді чи командному рядку.

⚠️ `--chats` бачить ЛИШЕ свіжі оновлення: Telegram зберігає їх ~24 год і віддає тільки
   доти, доки не увімкнено webhook. Порожній список майже завжди означає «боту ще ніхто
   не написав», а не «бот зламався».
"""

from __future__ import annotations

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.alerts import escape, send_telegram

API = "https://api.telegram.org/bot{token}/{method}"


class Command(BaseCommand):
    help = "Перевірити токен, знайти chat_id і надіслати тестове повідомлення"

    def add_arguments(self, parser):
        parser.add_argument("--whoami", action="store_true", help="Перевірити токен (getMe)")
        parser.add_argument("--chats", action="store_true", help="Показати доступні chat_id")
        parser.add_argument("--send", action="store_true", help="Надіслати тестове повідомлення")

    def handle(self, *args, **options):
        token = (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
        if not token:
            raise CommandError(
                "TELEGRAM_BOT_TOKEN порожній. Додайте його в backend/.env "
                "(файл у .gitignore) або в Docker secret."
            )

        if not any((options["whoami"], options["chats"], options["send"])):
            raise CommandError("Оберіть дію: --whoami | --chats | --send")

        if options["whoami"]:
            self._whoami(token)
        if options["chats"]:
            self._chats(token)
        if options["send"]:
            self._send()

    # -- дії -----------------------------------------------------------------
    def _whoami(self, token: str) -> None:
        data = self._call(token, "getMe")
        bot = data["result"]
        self.stdout.write(self.style.SUCCESS(f"Бот: @{bot['username']} (id={bot['id']})"))

    def _chats(self, token: str) -> None:
        data = self._call(token, "getUpdates")
        updates = data.get("result") or []

        seen: dict[int, str] = {}
        for update in updates:
            for key in ("message", "channel_post", "my_chat_member"):
                chat = (update.get(key) or {}).get("chat")
                if chat:
                    title = chat.get("title") or chat.get("username") or chat.get("first_name", "")
                    seen[chat["id"]] = f"{chat.get('type', '?')} — {title}"

        if not seen:
            self.stdout.write(
                self.style.WARNING(
                    "Оновлень немає. Напишіть боту в особисті або додайте його в групу "
                    "й надішліть туди будь-яке повідомлення, потім повторіть команду.\n"
                    "⚠️ Для групи вимкніть Privacy Mode у @BotFather (/setprivacy → Disable), "
                    "інакше бот не бачить звичайних повідомлень."
                )
            )
            return

        self.stdout.write("Доступні чати:")
        for chat_id, description in seen.items():
            self.stdout.write(f"  chat_id = {chat_id}   ({description})")
        self.stdout.write(
            "\nПокладіть потрібний у SiteSettings.telegram_chat_id (адмінка → Налаштування "
            "сайту → «Синхронізація і алерти») — тоді адресата можна міняти без деплою.\n"
            "TELEGRAM_CHAT_ID в .env — лише фолбек, коли БД ще порожня."
        )

    def _send(self) -> None:
        ok = send_telegram(
            "✅ <b>Перевірка зв'язку</b>\n\n"
            f"Complex → Telegram працює.\nСередовище: <code>{escape(settings.SETTINGS_MODULE)}</code>"
        )
        if ok:
            self.stdout.write(self.style.SUCCESS("Надіслано. Перевірте чат."))
            return

        raise CommandError(
            "Не надіслано. Найчастіші причини:\n"
            "  * SiteSettings.telegram_chat_id і TELEGRAM_CHAT_ID обидва порожні;\n"
            "  * бота не додали в групу (Telegram відповідає «chat not found»);\n"
            "  * для групи потрібен від'ємний chat_id (напр. -1001234567890).\n"
            "Точну причину видно в лозі: core.alerts пише тіло відповіді Telegram."
        )

    # -- транспорт -----------------------------------------------------------
    def _call(self, token: str, method: str) -> dict:
        try:
            response = httpx.get(API.format(token=token, method=method), timeout=10.0)
        except httpx.HTTPError as exc:
            raise CommandError(f"Мережева помилка: {exc}") from exc

        if response.status_code == 401:
            raise CommandError("Telegram: 401 — токен недійсний або відкликаний (/revoke).")

        data = response.json()
        if not data.get("ok"):
            raise CommandError(f"Telegram: {data.get('description', response.text[:300])}")
        return data
