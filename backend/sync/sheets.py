"""
Клієнт Google Sheets + читачі прайсу і таблиці характеристик (SYNC.md §1.5, §2 крок 2–3).

Три речі, заради яких цей модуль існує окремо від двигуна:

1. **ЖОРСТКА ЗВІРКА ПОВНОТИ.** `rowCount` з `spreadsheets.get` проти кількості прочитаних
   рядків. Будь-яка помилка будь-якого чанка ПІСЛЯ ретраїв = падіння всього читання.
   Ніякого «читаємо, що встигли»: недочитаний прайс тихо деактивує півкаталогу, і
   guard #1 (0.5) цього не спіймає (SYNC.md §2, крок 2).

   ⚠️ Sheets ОБРІЗАЄ хвостові порожні рядки у відповіді. Тому кожен чанк **добивається**
   до розміру запитаного діапазону (`_pad`) — інакше звірка не сходиться НІКОЛИ і
   кожен прогін падає у FAILED. Порожні рядки хвоста відсіює вже парсер (`classify_row`).

2. **ТРИ ДЖЕРЕЛА АВТОРИЗАЦІЇ** (`get_client()`, пріоритет саме такий):

   (а) **Сервісний акаунт** — ОСНОВНИЙ і бажаний шлях (`GOOGLE_SA_JSON` / `GOOGLE_SA_FILE` /
       `settings.GOOGLE_SA_JSON_PATH`).
   (б) **OAuth від імені користувача** — ЗАПАСНИЙ шлях, і ось чому він взагалі існує:
       політика Google Cloud організації замовника (`iam.disableServiceAccountKeyCreation`)
       ЗАБОРОНЯЄ створювати ключі сервісних акаунтів. Поки її не знімуть, SA-шлях фізично
       недоступний, тому ходимо в Sheets токеном живого користувача, якому таблиці
       розшарені на перегляд (`manage.py google_auth` — разова інтерактивна авторизація).
       ⚠️ Щойно політику знімуть і з'явиться ключ SA — код перемкнеться на нього САМ,
       без жодних правок: SA перевіряється ПЕРШИМ. OAuth-токен можна просто видалити.
   (в) **ФІКСТУРНИЙ РЕЖИМ.** Немає ні SA, ні OAuth-токена → читаємо CSV з `sync/fixtures/`.
       Увесь двигун (і всі тести) працює без жодного мережевого виклику і без доступу до
       чужих таблиць. Фікстури повторюють РЕАЛЬНУ структуру таблиць замовника
       (INPUTS.md §3.1–3.3), включно з усіма пастками.

3. **ГЕОМЕТРІЯ ЛИСТА** — редагується в адмінці, бо замовник може вставити рядок.

   ⚠️ ЗВІРЕНО З РЕАЛЬНИМИ ТАБЛИЦЯМИ (14.07.2026, OAuth). Попередня версія цього модуля була
   написана під структуру, РЕКОНСТРУЙОВАНУ ЗІ СКРІНШОТІВ, і не збігається з дійсністю:

       БУЛО (зі скрінів)                      СТАЛО (реальність)
       рядок 1–2  контакти/умови              рядок 1  ЗАГОЛОВКИ
       рядок 3    заголовки                   рядок 2  A2 = дата зрізу (Excel serial 46211)
       рядок 4    A4 = дата, E4 = курс USD    рядок 3  перша секція
       рядок 5    дані                        рядок 4  дані

   ⚠️ КУРСУ USD у прайсі НЕМАЄ ЗОВСІМ (він жив в іншому файлі, «для клієнтів») → курс
   береться виключно з адмінки (SiteSettings). `rate_cell` лишається порожнім.
   ⚠️ ЗАГОЛОВКИ ЛИСТІВ НЕ ЗБІГАЮТЬСЯ МІЖ СОБОЮ («Бренд» на UAH vs «Виробник» на USD) →
   мапінг іде ПО ЗАГОЛОВКУ з набором синонімів (`COLUMN_SYNONYMS`), а не по позиції.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import httpx
from django.conf import settings

from sync.parsing import Spec, clean_or_empty, collapse_spaces, normalize_model, parse_spec_triples

log = logging.getLogger(__name__)

__all__ = [
    "MODE_FIXTURES",
    "MODE_OAUTH",
    "MODE_SERVICE_ACCOUNT",
    "OAUTH_HINT",
    "SCOPES",
    "FixtureSheetsClient",
    "GoogleSheetsClient",
    "HeaderMismatch",
    "IncompleteRead",
    "SheetRead",
    "SheetsClient",
    "SheetsError",
    "SpecRecord",
    "build_column_index",
    "get_client",
    "is_fixture_mode",
    "norm_header",
    "oauth_client_file",
    "oauth_token_file",
    "parse_sheet_date",
    "read_cells",
    "read_price_sheet",
    "read_spec_sheet",
    "save_oauth_token",
    "sheets_mode",
]

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
#: ЛИШЕ читання. Нічого не пишемо в чужі таблиці — і не просимо на це прав.
SCOPES = ("https://www.googleapis.com/auth/spreadsheets.readonly",)

#: Режими авторизації, у порядку пріоритету (див. докстрінг модуля, §2).
MODE_SERVICE_ACCOUNT = "service_account"
MODE_OAUTH = "oauth"
MODE_FIXTURES = "fixtures"

#: Єдина підказка на всі OAuth-помилки: користувач має знати, ЩО саме запустити.
OAUTH_HINT = "Запусти: uv run python manage.py google_auth"

#: Чанк читання. Більше — ризик 10 МБ ліміту відповіді, менше — зайві RTT.
CHUNK_ROWS = 5000
#: Остання колонка діапазону. Трійки характеристик тягнуться далеко вправо.
LAST_COL = "ZZ"

MAX_ATTEMPTS = 6
BACKOFF_BASE = 0.5
BACKOFF_CAP = 30.0
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 60.0

#: Обов'язкові логічні колонки прайсу (SYNC.md §2, крок 3).
REQUIRED_PRICE_COLUMNS = ("sku", "name", "price")
#: Обов'язкові логічні колонки таблиці характеристик.
REQUIRED_SPEC_COLUMNS = ("sku",)


class SheetsError(RuntimeError):
    """Будь-який збій читання. Викликач → SyncRun.FAILED, каталог НЕ чіпаємо."""


class IncompleteRead(SheetsError):
    """Прочитано менше рядків, ніж є в сітці. НЕ PARTIAL — FAILED (SYNC.md §9)."""


class HeaderMismatch(SheetsError):
    """Обов'язкова колонка не знайдена / заголовок трійок не збігся. FAILED, голосно."""


# ---------------------------------------------------------------------------
# A1-нотація
# ---------------------------------------------------------------------------

_A1_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def a1_to_index(cell: str) -> tuple[int, int]:
    """`"E4"` → `(3, 4)` — 0-based індекс колонки і **1-based** номер рядка."""
    m = _A1_RE.match(cell.strip())
    if not m:
        raise ValueError(f"Некоректна A1-адреса: {cell!r}")
    letters, digits = m.group(1).upper(), m.group(2)
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - 64)
    return col - 1, int(digits)


def _quote_tab(tab: str) -> str:
    # Назви вкладок бувають з пробілами й кирилицею («Гривнева»), тому лапки — завжди.
    return "'" + tab.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Протокол клієнта
# ---------------------------------------------------------------------------


class SheetsClient(Protocol):
    """Мінімальний контракт. Двигун синку не знає, звідки прийшли рядки."""

    def row_count(self, spreadsheet_id: str, tab: str) -> int:
        """Місткість сітки (`gridProperties.rowCount`) — саме її вимагає звірка повноти."""
        ...

    def read_rows(
        self, spreadsheet_id: str, tab: str, first_row: int, last_row: int
    ) -> list[list[Any]]:
        """Рядки [first_row, last_row] (1-based, включно). ЗАВЖДИ рівно `last-first+1` рядків."""
        ...


def _pad(rows: list[list[Any]], expected: int) -> list[list[Any]]:
    """Добити відповідь до розміру запитаного діапазону.

    Sheets обрізає хвостові порожні рядки. Без цього `len(rows) + offset != rowCount`
    ЗАВЖДИ, і кожен прогін падає у FAILED (SYNC.md §2, крок 2).
    """
    if len(rows) > expected:  # захист від сюрпризів API
        raise IncompleteRead(f"API віддав {len(rows)} рядків замість {expected}")
    rows.extend([] for _ in range(expected - len(rows)))
    return rows


def read_cells(
    client: SheetsClient, spreadsheet_id: str, tab: str, cells: list[str]
) -> dict[str, Any]:
    """Кілька окремих комірок (`["E4", "A4"]`) → `{"E4": "41,65", "A4": "14 08 25"}`.

    Рядки метаданих — 1–2 штуки, тому читаємо їх цілком і індексуємо в пам'яті:
    окремий `batchGet` на комірку коштував би стільки ж, скільки на весь рядок.
    """
    out: dict[str, Any] = {}
    by_row: dict[int, list[tuple[str, int]]] = {}
    for cell in cells:
        if not cell:
            continue
        col, row = a1_to_index(cell)
        by_row.setdefault(row, []).append((cell, col))

    for row_no, wanted in by_row.items():
        try:
            values = client.read_rows(spreadsheet_id, tab, row_no, row_no)[0]
        except IndexError:  # pragma: no cover — _pad це гарантує
            values = []
        for cell, col in wanted:
            out[cell] = values[col] if col < len(values) else ""
    return out


# ---------------------------------------------------------------------------
# Реальний клієнт
# ---------------------------------------------------------------------------


class GoogleSheetsClient:
    """Sheets API v4 через httpx + service-account.

    `values.batchGet` чанками по 5000 рядків, `UNFORMATTED_VALUE` (числа приходять числами —
    саме тому `_norm_sku` зобов'язаний зрізати `.0`).

    Ретраї: 429 і 5xx з експоненційним backoff + джитер, повага до `Retry-After`.
    Вичерпались — кидаємо `SheetsError`. Ніяких «читаємо, що встигли».
    """

    def __init__(self, credentials: Any, *, max_attempts: int = MAX_ATTEMPTS) -> None:
        self._creds = credentials
        self._max_attempts = max_attempts
        self._client = httpx.Client(
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            follow_redirects=False,
        )
        self._row_counts: dict[tuple[str, str], int] = {}

    # -- авторизація ---------------------------------------------------------
    def _token(self) -> str:
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport.requests import Request as GoogleRequest

        if not self._creds.valid:
            try:
                self._creds.refresh(GoogleRequest())
            except GoogleAuthError as exc:
                # Для OAuth це найчастіше «доступ відкликано / токен протух назавжди».
                # Не пускаємо RefreshError нагору голим traceback'ом: викликач чекає SheetsError.
                raise SheetsError(f"Не вдалося оновити токен доступу: {exc}. {OAUTH_HINT}") from exc
            if getattr(self._creds, "refresh_token", None):
                save_oauth_token(self._creds)  # оновлений access_token — на диск
        return str(self._creds.token)

    # -- HTTP ----------------------------------------------------------------
    def _get(self, url: str, params: list[tuple[str, str]]) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                resp = self._client.get(
                    url, params=params, headers={"Authorization": f"Bearer {self._token()}"}
                )
            except httpx.HTTPError as exc:  # таймаут, обрив, DNS
                last = exc
                self._sleep(attempt, None)
                continue

            if resp.status_code == 200:
                return dict(resp.json())

            if resp.status_code in (429, 500, 502, 503, 504):
                last = SheetsError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                self._sleep(attempt, resp.headers.get("Retry-After"))
                continue

            # 401/403 (зник шарінг), 400 (кривий діапазон) — ретраїти нічого.
            raise SheetsError(f"Sheets API HTTP {resp.status_code}: {resp.text[:300]}")

        raise SheetsError(f"Sheets API недоступний після {self._max_attempts} спроб: {last}")

    def _sleep(self, attempt: int, retry_after: str | None) -> None:
        if attempt >= self._max_attempts - 1:
            return
        if retry_after:
            try:
                time.sleep(min(float(retry_after), BACKOFF_CAP))
                return
            except ValueError:
                pass
        delay = min(BACKOFF_BASE * (2**attempt), BACKOFF_CAP)
        time.sleep(delay + random.uniform(0, delay / 2))  # noqa: S311 — джитер, не крипто

    # -- API -----------------------------------------------------------------
    def spreadsheet_meta(self, spreadsheet_id: str) -> dict[str, Any]:
        """Назва таблиці + її листи з кількістю рядків. Діагностика (`sheets_info`,
        `google_auth`): найдешевший спосіб довести, що доступ РЕАЛЬНО є, не читаючи дані.
        """
        data = self._get(
            f"{SHEETS_API}/{spreadsheet_id}",
            [
                (
                    "fields",
                    "properties(title),sheets(properties(title,gridProperties(rowCount,columnCount)))",
                )
            ],
        )
        sheets = []
        for sheet in data.get("sheets", []):
            props = sheet.get("properties", {})
            grid = props.get("gridProperties", {})
            sheets.append(
                {
                    "title": str(props.get("title", "")),
                    "row_count": int(grid.get("rowCount", 0)),
                    "column_count": int(grid.get("columnCount", 0)),
                }
            )
        return {"title": str(data.get("properties", {}).get("title", "")), "sheets": sheets}

    def row_count(self, spreadsheet_id: str, tab: str) -> int:
        key = (spreadsheet_id, tab)
        if key in self._row_counts:
            return self._row_counts[key]

        data = self._get(
            f"{SHEETS_API}/{spreadsheet_id}",
            [("fields", "sheets(properties(title,gridProperties(rowCount)))")],
        )
        for sheet in data.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("title") == tab:
                count = int(props.get("gridProperties", {}).get("rowCount", 0))
                self._row_counts[key] = count
                return count
        raise SheetsError(f"Вкладки {tab!r} немає в таблиці {spreadsheet_id}")

    def read_rows(
        self, spreadsheet_id: str, tab: str, first_row: int, last_row: int
    ) -> list[list[Any]]:
        if last_row < first_row:
            return []

        ranges: list[tuple[str, int]] = []
        start = first_row
        while start <= last_row:
            end = min(start + CHUNK_ROWS - 1, last_row)
            ranges.append((f"{_quote_tab(tab)}!A{start}:{LAST_COL}{end}", end - start + 1))
            start = end + 1

        out: list[list[Any]] = []
        # batchGet приймає кілька діапазонів за раз; по 10 — щоб відповідь не роздувалась.
        for i in range(0, len(ranges), 10):
            group = ranges[i : i + 10]
            params = [("valueRenderOption", "UNFORMATTED_VALUE"), ("majorDimension", "ROWS")]
            params += [("ranges", rng) for rng, _ in group]
            data = self._get(f"{SHEETS_API}/{spreadsheet_id}/values:batchGet", params)

            got = data.get("valueRanges", [])
            if len(got) != len(group):
                raise IncompleteRead(
                    f"batchGet повернув {len(got)} діапазонів замість {len(group)}"
                )
            for (_, size), value_range in zip(group, got, strict=True):
                out.extend(_pad(list(value_range.get("values", [])), size))

        return out


# ---------------------------------------------------------------------------
# Фікстурний клієнт
# ---------------------------------------------------------------------------


def fixtures_dir() -> Path:
    raw = os.environ.get("SYNC_FIXTURES_DIR", "")
    return Path(raw) if raw else Path(settings.BASE_DIR) / "sync" / "fixtures"


class FixtureSheetsClient:
    """CSV-фікстури замість Google Sheets.

    Файл = ПОВНА сітка з рядка 1 (разом з шапкою-контактами і рядком метаданих), тому
    геометрія `header_row=3 / data_start_row=5` перевіряється по-справжньому, а не «на віру».

    Пошук файла: `{spreadsheet_id}__{tab}.csv` → `{tab}.csv`. Другий варіант дозволяє тим
    самим фікстурам обслуговувати будь-який `spreadsheet_id` (тести, демо, локальний прогін).
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or fixtures_dir()

    def _path(self, spreadsheet_id: str, tab: str) -> Path:
        for name in (f"{spreadsheet_id}__{tab}.csv", f"{tab}.csv"):
            path = self.base_dir / name
            if path.exists():
                return path
        raise SheetsError(
            f"Немає фікстури для {spreadsheet_id!r}/{tab!r} у {self.base_dir} "
            f"(очікував {spreadsheet_id}__{tab}.csv або {tab}.csv)"
        )

    def _grid(self, spreadsheet_id: str, tab: str) -> list[list[str]]:
        path = self._path(spreadsheet_id, tab)
        with path.open(encoding="utf-8", newline="") as fh:
            return list(csv.reader(fh))

    def row_count(self, spreadsheet_id: str, tab: str) -> int:
        return len(self._grid(spreadsheet_id, tab))

    def read_rows(
        self, spreadsheet_id: str, tab: str, first_row: int, last_row: int
    ) -> list[list[Any]]:
        if last_row < first_row:
            return []
        grid = self._grid(spreadsheet_id, tab)
        rows = [list(grid[i]) if i < len(grid) else [] for i in range(first_row - 1, last_row)]
        return _pad(rows, last_row - first_row + 1)


# ---------------------------------------------------------------------------
# Фабрика
# ---------------------------------------------------------------------------


def _service_account_info() -> dict[str, Any] | None:
    """JSON сервісного акаунта: інлайном (`GOOGLE_SA_JSON`) або файлом.

    Файл — `GOOGLE_SA_FILE` або `settings.GOOGLE_SA_JSON_PATH` (у проді це docker secret).
    Шлях, який НЕ ІСНУЄ, — це фікстурний режим, а не помилка: локально в `.env` шлях
    `/run/secrets/google_sa.json` прописаний завжди.
    """
    raw = os.environ.get("GOOGLE_SA_JSON", "").strip()
    if raw:
        try:
            return dict(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise SheetsError(f"GOOGLE_SA_JSON — не валідний JSON: {exc}") from exc

    path_str = os.environ.get("GOOGLE_SA_FILE", "") or getattr(settings, "GOOGLE_SA_JSON_PATH", "")
    if path_str and Path(path_str).is_file():
        return dict(json.loads(Path(path_str).read_text(encoding="utf-8")))
    return None


# -- OAuth (запасний шлях; чому він існує — див. докстрінг модуля, §2б) ------


def _under_base_dir(raw: str, default_name: str) -> Path:
    """Порожньо → `BASE_DIR/default_name`. Відносний шлях — теж від BASE_DIR, а НЕ від CWD:
    інакше `manage.py` з іншого каталогу (cron, systemd, docker exec) мовчки не знайшов би файл.
    """
    path = Path(raw) if raw else Path(default_name)
    return path if path.is_absolute() else Path(settings.BASE_DIR) / path


def oauth_client_file() -> Path:
    """Client secrets Desktop-клієнта (`{"installed": {...}}`). Секрет: у .gitignore."""
    return _under_base_dir(
        os.environ.get("GOOGLE_OAUTH_CLIENT_FILE", "").strip(), "google_oauth.json"
    )


def oauth_token_file() -> Path:
    """Збережений токен користувача (access + refresh). Це доступ до таблиць БЕЗ пароля —
    ніколи не логувати вміст і не комітити.
    """
    return _under_base_dir(
        os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "").strip(), "google_token.json"
    )


def save_oauth_token(creds: Any, path: Path | None = None) -> Path:
    """Записати токен з правами 0600 (файл-секрет, читає лише власник)."""
    path = path or oauth_token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_CREAT з режимом 0600 одразу: між write і chmod не має бути вікна, коли файл читає world.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    os.chmod(path, 0o600)  # якщо файл уже існував — O_CREAT його режим не змінює
    return path


def _oauth_credentials() -> Any | None:
    """Токен користувача, готовий до вжитку, або `None` — якщо файла токена просто немає.

    Протух → мовчки оновлюємо через `refresh_token` і ПЕРЕЗАПИСУЄМО файл.
    Відкликали / файл побитий → `SheetsError` з підказкою, а не traceback з надр google-auth.
    """
    token_file = oauth_token_file()
    if not token_file.is_file():
        return None

    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials

    try:
        creds = Credentials.from_authorized_user_file(str(token_file), list(SCOPES))
    except (ValueError, json.JSONDecodeError) as exc:
        raise SheetsError(
            f"Файл токена {token_file} пошкоджений або не є токеном OAuth: {exc}. {OAUTH_HINT}"
        ) from exc

    if creds.valid:
        return creds

    if not creds.refresh_token:
        raise SheetsError(
            f"У токені {token_file} немає refresh_token — він помре за годину і вже мертвий. "
            f"Схоже, авторизація йшла без access_type=offline / prompt=consent. {OAUTH_HINT}"
        )

    try:
        creds.refresh(GoogleRequest())
    except GoogleAuthError as exc:
        raise SheetsError(
            f"Токен OAuth недійсний — доступ відкликано або строк вичерпано ({exc}). {OAUTH_HINT}"
        ) from exc

    save_oauth_token(creds, token_file)
    log.info("OAuth-токен протух і був оновлений через refresh_token (%s)", token_file)
    return creds


# -- Вибір джерела -----------------------------------------------------------


def sheets_mode() -> str:
    """Який шлях авторизації активний ПРЯМО ЗАРАЗ: SA → OAuth → фікстури.

    Перевірка дешева й без мережі (наявність ключів / файла токена), тому її можна
    смикати з адмінки, логів і `manage.py sheets_info`.
    """
    if _service_account_info() is not None:
        return MODE_SERVICE_ACCOUNT
    if oauth_token_file().is_file():
        return MODE_OAUTH
    return MODE_FIXTURES


def is_fixture_mode() -> bool:
    return sheets_mode() == MODE_FIXTURES


def get_client() -> SheetsClient:
    """Клієнт за пріоритетом джерел (докстрінг модуля, §2): SA → OAuth → фікстури."""
    mode = sheets_mode()

    if mode == MODE_SERVICE_ACCOUNT:
        from google.oauth2 import service_account

        info = _service_account_info()
        creds = service_account.Credentials.from_service_account_info(info, scopes=list(SCOPES))
        return GoogleSheetsClient(creds)

    if mode == MODE_OAUTH:
        creds = _oauth_credentials()
        if creds is None:  # pragma: no cover — файл зник між sheets_mode() і сюди
            raise SheetsError(f"Файл токена OAuth зник: {oauth_token_file()}. {OAUTH_HINT}")
        log.info(
            "Google SA недоступний (політика організації забороняє ключі SA) → "
            "читаю Sheets через OAuth-токен користувача (%s).",
            oauth_token_file(),
        )
        return GoogleSheetsClient(creds)

    log.warning(
        "Ні Google SA, ні OAuth-токена → ФІКСТУРНИЙ РЕЖИМ (%s). "
        "Це нормально локально й у тестах; у проді означає, що зник секрет. %s",
        fixtures_dir(),
        OAUTH_HINT,
    )
    return FixtureSheetsClient()


# ---------------------------------------------------------------------------
# Заголовки
# ---------------------------------------------------------------------------

_HEADER_SEP_RE = re.compile(r"[-_\s]+")

#: Позиційне оголошення колонки в `column_map`: `"#8"` = 8-ма колонка (1-based).
_POSITIONAL_RE = re.compile(r"^#(\d+)$")

#: ВБУДОВАНІ СИНОНІМИ заголовків. Працюють ЗАВЖДИ, поверх `column_map`.
#:
#: ⚠️ Це не «на всяк випадок», а вимога реальних даних: два листи ОДНОГО файла називають ту
#: саму колонку по-різному, і жоден column_map не може бути правильним для обох одночасно:
#:     UAH: … | Бренд    | Країна виробництва | …
#:     USD: … | Виробник | Країна_виробник    | …
#: Мапінг ПО ЗАГОЛОВКУ з набором синонімів робить обидва листи читабельними одним конфігом,
#: а перейменування колонки замовником перестає бути аварією.
#
#: ⚠️ ДЛЯ ОБОВ'ЯЗКОВИХ КОЛОНОК (sku / name / price) СПИСОК НАВМИСНО КОРОТКИЙ — тільки те, що
#: реально стоїть у таблицях. Здогадні синоніми («Код», «Код товару») тут не просто зайві,
#: вони НЕБЕЗПЕЧНІ: лист із колонкою «Код» (внутрішній код, штрихкод, код постачальника)
#: мовчки під'їхав би в `sku` — і синк почав би зіставляти каталог не за тим ключем.
#: Краще гучний `HeaderMismatch` («колонку перейменували»), ніж тихо не той артикул.
COLUMN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "sku": ("Артикул",),
    "name": ("Найменування", "Назва"),
    "price": ("Ціна",),
    "old_price": ("Стара ціна", "Ціна до знижки"),
    "qty": ("К-сть", "Кількість", "Кол-во"),
    "currency": ("Валюта",),
    "category": ("Категорія", "Категория"),
    "brand": ("Бренд", "Виробник", "Производитель", "Торгова марка"),
    "country": (
        "Країна виробництва",  # UAH-лист прайсу + «Основна»
        "Країна_виробник",  # USD-лист прайсу
        "Країна-виробник",  # старі листи
        "Країна",
    ),
    "photo": ("Фото", "Зображення", "Посилання на фото"),
    "image": ("Зображення", "Фото"),
    "mpn": ("Артикул виробника", "MPN"),
    "package_dims": (
        "Розміри в упакуванні (см)",  # ← РЕАЛЬНИЙ заголовок «Основної» (упакуВАННІ!)
        "Розміри в упаковці (см)",  # ← так було в реконструкції зі скрінів
        "Розміри в упакуванні",
        "Розміри в упаковці",
        "Габарити упаковки",
    ),
    "weight": ("Вага", "Вага в упаковці", "Вага упаковки (кг)"),
}


@lru_cache(maxsize=512)
def norm_header(raw: str) -> str:
    """Нормалізація заголовка перед звіркою з `column_map` (SYNC.md §2, крок 3).

    NBSP → пробіл, схлопування, casefold, і — головне — дефіс / підкреслення / пробіл
    вважаються ОДНИМ символом: `«Країна_виробник»` і `«Країна-виробник»` (різні листи
    пишуть по-різному) не мають валити прогін на рівному місці.
    """
    return _HEADER_SEP_RE.sub(" ", collapse_spaces(raw).casefold()).strip()


def _title_candidates(logical: str, declared: Any) -> list[str]:
    """Кандидати на заголовок колонки: спершу з `column_map`, далі — вбудовані синоніми."""
    if isinstance(declared, list | tuple):
        titles = [str(t) for t in declared if str(t).strip()]
    elif declared is not None and str(declared).strip():
        titles = [str(declared)]
    else:
        titles = []

    seen = {norm_header(t) for t in titles}
    titles.extend(t for t in COLUMN_SYNONYMS.get(logical, ()) if norm_header(t) not in seen)
    return titles


def build_column_index(
    headers: list[Any], column_map: dict[str, Any], required: tuple[str, ...]
) -> dict[str, int]:
    """`{"sku": 8, "name": 0, ...}` — логічне ім'я → індекс колонки.

    Три способи оголосити колонку (значення `column_map`):
      * `"Артикул"`             — заголовок;
      * `["Бренд", "Виробник"]` — синоніми, перший знайдений виграє;
      * `"#8"`                  — ПОЗИЦІЙНО, 8-ма колонка (1-based).

    Позиційний варіант існує заради «Основної»: колонка з БРЕНДОМ має там заголовок-сміття
    (`2401579` — чийсь артикул, що заїхав у шапку). Шукати таку колонку за іменем неможливо,
    а мовчки її втратити означає лишити 1578 товарів без бренда.

    Пошук за іменем ідемо `column_map` → `COLUMN_SYNONYMS` (див. вище).
    Відсутня ОБОВ'ЯЗКОВА колонка → `HeaderMismatch` (FAILED, голосно). Ніколи не
    «тихо занулити ціни, бо колонку перейменували».
    Відсутня опційна (`qty`, `category`, `brand`) — не помилка.
    """
    positions: dict[str, int] = {}
    for idx, header in enumerate(headers):
        key = norm_header(str(header))
        if key and key not in positions:  # перший переможець: дублі заголовків бувають
            positions[key] = idx

    #: Логічні колонки, які шукаємо: оголошені в column_map + ті, для яких є синоніми.
    logicals = [k for k in column_map if k != "spec_triplet_start"]
    logicals += [k for k in COLUMN_SYNONYMS if k not in logicals and k in required]

    index: dict[str, int] = {}
    for logical in logicals:
        for title in _title_candidates(logical, column_map.get(logical)):
            pos = _POSITIONAL_RE.match(title.strip())
            if pos:
                idx = int(pos.group(1)) - 1
                if 0 <= idx < len(headers):
                    index[logical] = idx
                    break
                continue
            idx_by_name = positions.get(norm_header(title))
            if idx_by_name is not None:
                index[logical] = idx_by_name
                break

    missing = [c for c in required if c not in index]
    if missing:
        raise HeaderMismatch(
            "У таблиці немає обов'язкових колонок: "
            + ", ".join(f"{c} → {column_map.get(c, '?')!r}" for c in missing)
            + f". Знайдені заголовки: {[str(h) for h in headers if str(h).strip()]}"
        )
    return index


# ---------------------------------------------------------------------------
# Дата зрізу
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"(\d{1,2})\D+(\d{1,2})\D+(\d{2,4})")
_SERIAL_RE = re.compile(r"^\d{4,6}(?:[.,]\d+)?$")

#: Excel/Sheets рахують дні від 1899-12-30 (так, з відомим багом 1900-го року).
_EXCEL_EPOCH = date(1899, 12, 30)
#: Розумний діапазон serial-дат: ~1954 … ~2064. Поза ним це не дата, а просто число.
_SERIAL_MIN, _SERIAL_MAX = 20000, 60000


def _from_excel_serial(value: float) -> date | None:
    """`46211` → `date(2026, 7, 13)`. Поза розумним діапазоном → `None`."""
    days = int(value)
    if not _SERIAL_MIN <= days <= _SERIAL_MAX:
        return None
    return _EXCEL_EPOCH + timedelta(days=days)


def parse_sheet_date(raw: Any) -> date | None:
    """Дата зрізу прайсу. Не розпарсилось → `None` (це НЕ помилка).

        >>> parse_sheet_date(46211)        # A2 РЕАЛЬНОГО прайсу — Excel serial
        datetime.date(2026, 7, 13)
        >>> parse_sheet_date("14 08 25")   # старий формат «дд мм рр»
        datetime.date(2025, 8, 14)

    ⚠️ У реальній таблиці в `A2` лежить ЧИСЛО (`valueRenderOption=UNFORMATTED_VALUE` віддає
    серійну дату Excel, а не «13.07.2026»). Без гілки serial дата зрізу не парситься ніколи,
    і WARN `STALE_PRICE_SHEET` («прайс місячної давності») не спрацьовує теж — тихо.

    Дата потрібна лише для звіту прогону, тому будь-яка невдача — це `None`, а не помилка.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, int | float):
        return _from_excel_serial(float(raw))

    text = collapse_spaces(raw)
    if _SERIAL_RE.match(text):
        return _from_excel_serial(float(text.replace(",", ".")))

    m = _DATE_RE.search(text)
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Читання прайсу
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SheetRead:
    tab: str
    headers: list[Any]
    index: dict[str, int]
    rows: list[tuple[int, list[Any]]]  # (номер рядка в таблиці, клітинки)
    row_count: int
    meta: dict[str, Any] = field(default_factory=dict)

    def logical(self, cells: list[Any]) -> dict[str, Any]:
        """Клітинки → `{"sku": ..., "name": ...}` за картою колонок."""
        return {key: (cells[idx] if idx < len(cells) else "") for key, idx in self.index.items()}


def read_price_sheet(client: SheetsClient, spreadsheet_id: str, sheet: Any) -> SheetRead:
    """Один лист прайсу: заголовки (рядок 3), метадані (E4/A4), дані (з рядка 5).

    :raises IncompleteRead: прочитано менше, ніж є в сітці → FAILED, каталог недоторканий.
    :raises HeaderMismatch: немає обов'язкової колонки → FAILED, голосно.
    """
    tab = sheet.tab_name
    total = client.row_count(spreadsheet_id, tab)

    if total < sheet.header_row:
        raise SheetsError(
            f"{tab}: у сітці {total} рядків, а заголовки очікуються в рядку {sheet.header_row}"
        )

    headers = client.read_rows(spreadsheet_id, tab, sheet.header_row, sheet.header_row)[0]
    index = build_column_index(headers, sheet.column_map or {}, REQUIRED_PRICE_COLUMNS)

    meta = read_cells(
        client,
        spreadsheet_id,
        tab,
        [c for c in (sheet.rate_cell, sheet.date_cell) if c],
    )

    raw_rows = client.read_rows(spreadsheet_id, tab, sheet.data_start_row, total)

    offset = sheet.data_start_row - 1  # скільки «технічних» рядків над даними
    if len(raw_rows) + offset != total:
        raise IncompleteRead(f"{tab}: неповне читання — {len(raw_rows) + offset} з {total} рядків")

    rows = [(sheet.data_start_row + i, cells) for i, cells in enumerate(raw_rows)]
    return SheetRead(
        tab=tab, headers=list(headers), index=index, rows=rows, row_count=total, meta=meta
    )


# ---------------------------------------------------------------------------
# Читання таблиці характеристик
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SpecRecord:
    sku: str
    name: str
    name_normalized: str
    specs: list[Spec]
    country: str = ""
    brand: str = ""
    mpn: str = ""
    package_dims_raw: str = ""
    row_number: int = 0


def read_spec_sheet(client: SheetsClient, spec_sheet: Any) -> list[SpecRecord]:
    """Таблиця характеристик: фіксовані колонки A..I + позиційні трійки з колонки J.

    Заголовок колонки `spec_triplet_start_col` звіряється з `column_map["spec_triplet_start"]`
    — це ЄДИНА перевірка трійок (самі вони читаються позиційно, бо їхні заголовки
    повторюються). Не збігся → `HeaderMismatch`, голосно: зсув на одну колонку означає, що
    в «Назву» поїдуть значення, і 10 000 товарів отримають характеристику «Слонова кістка».
    """
    tab = spec_sheet.tab_name
    sid = spec_sheet.spreadsheet_id
    total = client.row_count(sid, tab)
    if total < spec_sheet.header_row:
        raise SheetsError(f"{tab}: у сітці {total} рядків — заголовків немає")

    column_map = spec_sheet.column_map or {}
    headers = client.read_rows(sid, tab, spec_sheet.header_row, spec_sheet.header_row)[0]
    index = build_column_index(headers, column_map, REQUIRED_SPEC_COLUMNS)

    expected_triplet = column_map.get("spec_triplet_start", "")
    if expected_triplet:
        col = spec_sheet.spec_triplet_start_col - 1
        actual = str(headers[col]) if col < len(headers) else ""
        if norm_header(actual) != norm_header(expected_triplet):
            raise HeaderMismatch(
                f"{tab}: колонка трійок #{spec_sheet.spec_triplet_start_col} має заголовок "
                f"{actual!r}, а очікувався {expected_triplet!r} — таблицю зсунули"
            )

    raw_rows = client.read_rows(sid, tab, spec_sheet.data_start_row, total)
    offset = spec_sheet.data_start_row - 1
    if len(raw_rows) + offset != total:
        raise IncompleteRead(
            f"{tab}: неповне читання характеристик — {len(raw_rows) + offset} з {total}"
        )

    def cell(cells: list[Any], key: str) -> str:
        idx = index.get(key)
        if idx is None or idx >= len(cells):
            return ""
        return clean_or_empty(cells[idx])

    out: list[SpecRecord] = []
    for i, cells in enumerate(raw_rows):
        from sync.services import norm_sku  # локально: services імпортує sheets

        sku = norm_sku(cells[index["sku"]] if index["sku"] < len(cells) else "")
        name = cell(cells, "name")
        if not sku and not name:
            continue

        specs = parse_spec_triples(cells, spec_sheet.spec_triplet_start_col)
        out.append(
            SpecRecord(
                sku=sku,
                name=name,
                name_normalized=normalize_model(name),
                specs=specs,
                country=cell(cells, "country"),
                brand=cell(cells, "brand"),
                mpn=cell(cells, "mpn"),
                package_dims_raw=cell(cells, "package_dims"),
                row_number=spec_sheet.data_start_row + i,
            )
        )
    return out
