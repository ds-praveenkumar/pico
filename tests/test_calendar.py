"""Tests for the OAuth2 Google Calendar client (fully mocked, no network)."""

from pathlib import Path

from agents.tools import TOOL_NAMES, dispatch
from agents.tools.calendar_oauth import CalendarAuth, _default_credentials_path, get_calendar_client
import agents.tools.calendar_oauth as calendar_oauth


def test_default_path_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert _default_credentials_path() == tmp_path / ".agents" / "calendar"


def test_default_path_prefers_existing_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    existing = tmp_path / ".personal-assistant" / "calendar"
    existing.mkdir(parents=True)
    assert _default_credentials_path() == existing


def test_default_path_env_override(monkeypatch, tmp_path):
    monkeypatch.delenv("CALENDAR_CREDENTIALS_PATH", raising=False)
    chosen = tmp_path / "custom"
    chosen.mkdir(parents=True)
    monkeypatch.setenv("CALENDAR_CREDENTIALS_PATH", str(chosen))
    assert _default_credentials_path() == chosen


def test_client_secrets_file_from_env(monkeypatch, tmp_path):
    secret = tmp_path / "client_secret_app.json"
    secret.write_text("{}")
    monkeypatch.setenv("CALENDAR_CLIENT_SECRET_PATH", f"file://{secret}")
    auth = CalendarAuth(credentials_path=tmp_path / "standalone")
    assert auth.client_secrets_file == secret


def test_client_secrets_file_glob_fallback(tmp_path):
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "client_secret_x.json").write_text("{}")
    auth = CalendarAuth(credentials_path=auth_dir)
    assert auth.client_secrets_file == auth_dir / "client_secret_x.json"


def test_not_configured_without_anything(monkeypatch, tmp_path):
    monkeypatch.delenv("CALENDAR_CLIENT_SECRET_PATH", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    auth = CalendarAuth(credentials_path=empty)
    assert auth.is_configured() is False
    assert auth.get_credentials() is None


def test_is_configured_with_token(monkeypatch, tmp_path):
    monkeypatch.delenv("CALENDAR_CLIENT_SECRET_PATH", raising=False)
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "token.json").write_text("{}")
    auth = CalendarAuth(credentials_path=auth_dir)
    assert auth.is_configured() is True


def test_get_calendar_client_returns_instance():
    assert get_calendar_client() is not None


def test_calendar_tools_registered_in_registry():
    assert {"calendar_list", "calendar_create", "calendar_respond"}.issubset(TOOL_NAMES)


def test_calendar_list_dispatch(monkeypatch):
    fake_client = calendar_oauth.CalendarClient.__new__(calendar_oauth.CalendarClient)
    fake_client._service = object()
    fake_client.list_events = lambda max_results=10, days_ahead=7: [
        {
            "id": "evt1",
            "summary": "Team standup",
            "start": "2026-09-14T09:00:00+05:30",
            "end": "2026-09-14T09:15:00+05:30",
            "location": "Zoom",
            "description": "",
            "attendees": ["a@b.com"],
        }
    ]
    monkeypatch.setattr(calendar_oauth, "get_calendar_client", lambda: fake_client)
    result = dispatch("calendar_list", max_results=5)
    assert "Team standup" in result
    assert "evt1" in result


def test_calendar_create_dispatch_success(monkeypatch):
    fake_client = calendar_oauth.CalendarClient.__new__(calendar_oauth.CalendarClient)
    fake_client._service = object()
    fake_client.create_event = lambda summary="", start="", end="", description="": "evt2"
    monkeypatch.setattr(calendar_oauth, "get_calendar_client", lambda: fake_client)
    result = dispatch("calendar_create", summary="Lunch", start="2026-09-14T12:00:00", end="2026-09-14T13:00:00")
    assert "evt2" in result


def test_calendar_respond_rejects_unknown_status():
    result = calendar_oauth.calendar_respond("evt1", "maybe")
    assert "Unknown response" in result


def test_calendar_unavailable_reports_clear_error(monkeypatch):
    fake_client = calendar_oauth.CalendarClient.__new__(calendar_oauth.CalendarClient)
    fake_client._service = None
    fake_client.is_available = lambda: False
    monkeypatch.setattr(calendar_oauth, "get_calendar_client", lambda: fake_client)
    result = dispatch("calendar_list")
    assert "not configured" in result.lower()