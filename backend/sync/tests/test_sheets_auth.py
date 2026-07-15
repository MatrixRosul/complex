"""
Вибір джерела авторизації Sheets: service account → OAuth → фікстури (sync/sheets.py §2).

Жодної мережі: `google.oauth2.*` мокається цілком. Тести стережуть ГОЛОВНЕ — що поява ключа
сервісного акаунта автоматично перекриває OAuth-обхідник, і його не доведеться викошувати руками.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest
from google.auth.exceptions import RefreshError

from sync.sheets import (
    MODE_FIXTURES,
    MODE_OAUTH,
    MODE_SERVICE_ACCOUNT,
    FixtureSheetsClient,
    GoogleSheetsClient,
    SheetsError,
    get_client,
    is_fixture_mode,
    save_oauth_token,
    sheets_mode,
)

SA_SENTINEL = object()  # «креденшли сервісного акаунта» — важливо лише, що саме ВОНИ доїхали


class FakeOAuthCreds:
    """Мінімальний двійник `google.oauth2.credentials.Credentials`."""

    def __init__(self, *, valid: bool = True, refresh_token: str | None = "rt-1") -> None:  # noqa: S107
        self.valid = valid
        self.refresh_token = refresh_token
        self.token = "access-old"  # noqa: S105 — не секрет, а фікстура
        self.refreshed = False
        self.raise_on_refresh: Exception | None = None

    def refresh(self, request: Any) -> None:
        if self.raise_on_refresh:
            raise self.raise_on_refresh
        self.refreshed = True
        self.valid = True
        self.token = "access-new"  # noqa: S105

    def to_json(self) -> str:
        return json.dumps({"token": self.token, "refresh_token": self.refresh_token})


@pytest.fixture(autouse=True)
def _no_ambient_creds(settings: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Чистий старт: жодних SA/OAuth з реального оточення розробника.

    Токен за замовчуванням лежить у BASE_DIR — а там може бути СПРАВЖНІЙ google_token.json
    (розробник запустив `manage.py google_auth`). Без підміни BASE_DIR тест «немає нічого →
    фікстури» падав би саме на робочій машині, де все налаштовано.
    """
    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SA_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_FILE", raising=False)
    settings.GOOGLE_SA_JSON_PATH = ""
    settings.BASE_DIR = tmp_path


@pytest.fixture
def sa_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Ключ сервісного акаунта (вміст фіктивний — `from_service_account_info` замоканий)."""
    path = tmp_path / "sa.json"
    path.write_text(json.dumps({"type": "service_account", "client_email": "x@y.iam"}))
    monkeypatch.setenv("GOOGLE_SA_FILE", str(path))
    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        lambda info, scopes=None: SA_SENTINEL,
    )
    return path


@pytest.fixture
def token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "token.json"
    path.write_text(json.dumps({"token": "access-old", "refresh_token": "rt-1"}))
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_FILE", str(path))
    return path


def _patch_loader(monkeypatch: pytest.MonkeyPatch, creds: FakeOAuthCreds) -> None:
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_file",
        lambda filename, scopes=None: creds,
    )


# ---------------------------------------------------------------------------
# Пріоритет джерел
# ---------------------------------------------------------------------------


def test_service_account_wins_over_oauth(
    sa_file: Path, token_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SA є → беремо SA, навіть якщо поруч лежить валідний OAuth-токен.

    ЦЕ ГОЛОВНИЙ ТЕСТ ФАЙЛА: коли політику організації знімуть і з'явиться ключ, синк має
    перемкнутись на нього САМ. OAuth-токен на диску не повинен цьому заважати.
    """
    _patch_loader(monkeypatch, FakeOAuthCreds())  # токен валідний — і все одно програє

    assert sheets_mode() == MODE_SERVICE_ACCOUNT
    assert is_fixture_mode() is False

    client = get_client()
    assert isinstance(client, GoogleSheetsClient)
    assert client._creds is SA_SENTINEL


def test_oauth_used_when_no_service_account(
    token_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Немає SA, є валідний токен → OAuth."""
    creds = FakeOAuthCreds()
    _patch_loader(monkeypatch, creds)

    assert sheets_mode() == MODE_OAUTH
    assert is_fixture_mode() is False

    client = get_client()
    assert isinstance(client, GoogleSheetsClient)
    assert client._creds is creds
    assert creds.refreshed is False  # валідний токен не чіпаємо


def test_no_credentials_falls_back_to_fixtures() -> None:
    """Немає нічого → фікстури (як і було). Синк працює без мережі."""
    assert sheets_mode() == MODE_FIXTURES
    assert is_fixture_mode() is True
    assert isinstance(get_client(), FixtureSheetsClient)


# ---------------------------------------------------------------------------
# Життєвий цикл токена
# ---------------------------------------------------------------------------


def test_expired_token_is_refreshed_and_rewritten(
    token_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Протух → тихо оновлюємо через refresh_token і ПЕРЕЗАПИСУЄМО файл."""
    creds = FakeOAuthCreds(valid=False)
    _patch_loader(monkeypatch, creds)

    client = get_client()

    assert isinstance(client, GoogleSheetsClient)
    assert creds.refreshed is True
    saved = json.loads(token_file.read_text())
    assert saved["token"] == "access-new"  # noqa: S105 — старий "access-old" затерто
    assert saved["refresh_token"] == "rt-1"  # noqa: S105 — refresh_token уцілів
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_revoked_token_raises_sheets_error(
    token_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Доступ відкликано → SheetsError з підказкою, а НЕ RefreshError з traceback."""
    creds = FakeOAuthCreds(valid=False)
    creds.raise_on_refresh = RefreshError("invalid_grant: Token has been expired or revoked.")
    _patch_loader(monkeypatch, creds)

    with pytest.raises(SheetsError) as exc:
        get_client()

    assert "manage.py google_auth" in str(exc.value)  # сказано, ЩО запустити
    assert json.loads(token_file.read_text())["token"] == "access-old"  # noqa: S105 — файл цілий


def test_token_without_refresh_token_raises(
    token_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Токен без refresh_token — мертвий через годину. Кажемо про це прямо, а не мовчки падаємо."""
    _patch_loader(monkeypatch, FakeOAuthCreds(valid=False, refresh_token=None))

    with pytest.raises(SheetsError, match="refresh_token"):
        get_client()


def test_corrupt_token_file_raises_sheets_error(
    token_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file.write_text("це не JSON")
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_file",
        lambda filename, scopes=None: (_ for _ in ()).throw(ValueError("no client_id")),
    )
    with pytest.raises(SheetsError, match=re.escape("manage.py google_auth")):
        get_client()


def test_save_oauth_token_is_0600(tmp_path: Path) -> None:
    """Токен — секрет: файл має бути доступний ЛИШЕ власнику, навіть якщо вже існував як 0644."""
    path = tmp_path / "nested" / "token.json"
    path.parent.mkdir()
    path.write_text("{}")
    os.chmod(path, 0o644)

    save_oauth_token(FakeOAuthCreds(), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["refresh_token"] == "rt-1"  # noqa: S105
