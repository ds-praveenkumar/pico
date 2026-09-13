"""Tests for the OAuth2 Google Sheets client (fully mocked, no network)."""

from pathlib import Path

from agents.tools import TOOL_NAMES, dispatch
from agents.tools.sheets_oauth import (
    SheetsAuth,
    _as_rows,
    _default_credentials_path,
    get_sheets_client,
    normalize_spreadsheet_id,
)
import agents.tools.sheets_oauth as sheets_oauth


def test_default_path_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert _default_credentials_path() == tmp_path / ".agents" / "sheets"


def test_default_path_prefers_existing_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    existing = tmp_path / ".personal-assistant" / "sheets"
    existing.mkdir(parents=True)
    assert _default_credentials_path() == existing


def test_default_path_env_override(monkeypatch, tmp_path):
    monkeypatch.delenv("SHEETS_CREDENTIALS_PATH", raising=False)
    chosen = tmp_path / "custom"
    chosen.mkdir(parents=True)
    monkeypatch.setenv("SHEETS_CREDENTIALS_PATH", str(chosen))
    assert _default_credentials_path() == chosen


def test_client_secrets_file_from_env(monkeypatch, tmp_path):
    secret = tmp_path / "client_secret_app.json"
    secret.write_text("{}")
    monkeypatch.setenv("SHEETS_CLIENT_SECRET_PATH", f"file://{secret}")
    auth = SheetsAuth(credentials_path=tmp_path / "standalone")
    assert auth.client_secrets_file == secret


def test_client_secrets_file_glob_fallback(tmp_path):
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "client_secret_x.json").write_text("{}")
    auth = SheetsAuth(credentials_path=auth_dir)
    assert auth.client_secrets_file == auth_dir / "client_secret_x.json"


def test_not_configured_without_anything(monkeypatch, tmp_path):
    monkeypatch.delenv("SHEETS_CLIENT_SECRET_PATH", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    auth = SheetsAuth(credentials_path=empty)
    assert auth.is_configured() is False
    assert auth.get_credentials() is None


def test_is_configured_with_token(monkeypatch, tmp_path):
    monkeypatch.delenv("SHEETS_CLIENT_SECRET_PATH", raising=False)
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "token.json").write_text("{}")
    auth = SheetsAuth(credentials_path=auth_dir)
    assert auth.is_configured() is True


def test_normalize_spreadsheet_id_from_url():
    url = "https://docs.google.com/spreadsheets/d/1AbC-9xY/edit#gid=0"
    assert normalize_spreadsheet_id(url) == "1AbC-9xY"


def test_normalize_spreadsheet_id_passes_raw_through():
    assert normalize_spreadsheet_id("1AbC-9xY") == "1AbC-9xY"


def test_as_rows_normalizes_flat_and_nested():
    assert _as_rows(["a", "b"]) == [["a", "b"]]
    assert _as_rows([["a", 1], ["b", 2]]) == [["a", 1], ["b", 2]]
    assert _as_rows([]) == []


def test_get_sheets_client_returns_instance():
    assert get_sheets_client() is not None


def test_sheets_tools_registered_in_registry():
    assert {"sheets_read", "sheets_append", "sheets_update"}.issubset(TOOL_NAMES)


def test_sheets_read_dispatch(monkeypatch):
    fake_client = sheets_oauth.SheetsClient.__new__(sheets_oauth.SheetsClient)
    fake_client._service = object()
    fake_client.read_range = lambda spreadsheet_id="", range_name="": [
        ["Date", "Category", "Amount"],
        ["2026-09-13", "Food", 12.5],
    ]
    monkeypatch.setattr(sheets_oauth, "get_sheets_client", lambda: fake_client)
    result = dispatch("sheets_read", spreadsheet_id="1AbC", range_name="Transactions!A1:D200")
    assert "2026-09-13" in result
    assert "Food" in result


def test_sheets_append_dispatch_success(monkeypatch):
    fake_client = sheets_oauth.SheetsClient.__new__(sheets_oauth.SheetsClient)
    fake_client._service = object()
    fake_client.append_rows = lambda spreadsheet_id="", range_name="", values=None: True
    monkeypatch.setattr(sheets_oauth, "get_sheets_client", lambda: fake_client)
    result = dispatch(
        "sheets_append",
        spreadsheet_id="https://docs.google.com/spreadsheets/d/1AbC-9xY/edit",
        range_name="Transactions!A:D",
        values=["2026-09-13", "Food", 12.5, "lunch"],
    )
    assert "Appended 1 row(s)" in result


def test_sheets_update_dispatch_success(monkeypatch):
    fake_client = sheets_oauth.SheetsClient.__new__(sheets_oauth.SheetsClient)
    fake_client._service = object()
    fake_client.update_range = lambda spreadsheet_id="", range_name="", values=None: True
    monkeypatch.setattr(sheets_oauth, "get_sheets_client", lambda: fake_client)
    result = dispatch(
        "sheets_update",
        spreadsheet_id="1AbC",
        range_name="Transactions!B2",
        values=["Food", 14.0],
    )
    assert "Updated 1 row(s)" in result


def test_sheets_append_rejects_empty_values(monkeypatch):
    fake_client = sheets_oauth.SheetsClient.__new__(sheets_oauth.SheetsClient)
    fake_client._service = object()
    fake_client.is_available = lambda: True
    monkeypatch.setattr(sheets_oauth, "get_sheets_client", lambda: fake_client)
    result = dispatch("sheets_append", spreadsheet_id="1AbC", range_name="A1:D1", values=[])
    assert "empty" in result.lower()


def test_sheets_unavailable_reports_clear_error(monkeypatch):
    fake_client = sheets_oauth.SheetsClient.__new__(sheets_oauth.SheetsClient)
    fake_client._service = None
    fake_client.is_available = lambda: False
    monkeypatch.setattr(sheets_oauth, "get_sheets_client", lambda: fake_client)
    result = dispatch("sheets_read", spreadsheet_id="1AbC", range_name="A1:D1")
    assert "not configured" in result.lower()