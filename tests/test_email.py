"""Tests for the OAuth2 Gmail client (fully mocked, no network)."""

from pathlib import Path

import pytest

from agents.tools import TOOL_NAMES, dispatch
from agents.tools.gmail_oauth import GmailAuth, _default_credentials_path, get_gmail_client
import agents.tools.gmail_oauth as gmail_oauth


def test_default_path_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert _default_credentials_path() == tmp_path / ".agents" / "gmail"


def test_default_path_prefers_existing_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    existing = tmp_path / ".personal-assistant" / "gmail"
    existing.mkdir(parents=True)
    assert _default_credentials_path() == existing


def test_default_path_env_override(monkeypatch, tmp_path):
    monkeypatch.delenv("GMAIL_CREDENTIALS_PATH", raising=False)
    chosen = tmp_path / "custom"
    chosen.mkdir(parents=True)
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", str(chosen))
    assert _default_credentials_path() == chosen


def test_auth_picks_credentials_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("GMAIL_CREDENTIALS_PATH", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    existing = tmp_path / ".personal-assistant" / "gmail"
    existing.mkdir(parents=True)
    auth = GmailAuth()
    assert auth.credentials_path == existing


def test_client_secrets_file_from_env(monkeypatch, tmp_path):
    secret = tmp_path / "client_secret_app.json"
    secret.write_text("{}")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET_PATH", f"file://{secret}")
    auth = GmailAuth(credentials_path=tmp_path / "standalone")
    assert auth.client_secrets_file == secret


def test_client_secrets_file_local_credentials_json(monkeypatch, tmp_path):
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "credentials.json").write_text("{}")
    (tmp_path / "secret.json").write_text("{}")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET_PATH", f"file://{tmp_path / 'secret.json'}")
    auth = GmailAuth(credentials_path=auth_dir)
    assert auth.client_secrets_file == auth_dir / "credentials.json"


def test_client_secrets_file_glob_fallback(tmp_path):
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "client_secret_x.json").write_text("{}")
    auth = GmailAuth(credentials_path=auth_dir)
    assert auth.client_secrets_file == auth_dir / "client_secret_x.json"


def test_not_configured_without_anything(monkeypatch, tmp_path):
    monkeypatch.delenv("GMAIL_CLIENT_SECRET_PATH", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    auth = GmailAuth(credentials_path=empty)
    assert auth.is_configured() is False
    assert auth.get_credentials() is None


def test_is_configured_with_token(monkeypatch, tmp_path):
    monkeypatch.delenv("GMAIL_CLIENT_SECRET_PATH", raising=False)
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "token.json").write_text("{}")
    auth = GmailAuth(credentials_path=auth_dir)
    assert auth.is_configured() is True


def test_get_credentials_refresh(monkeypatch, tmp_path):
    import json

    from google.oauth2.credentials import Credentials as GoogleCredentials

    monkeypatch.delenv("GMAIL_CLIENT_SECRET_PATH", raising=False)
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    token = {
        "token": "ya29.expired",
        "refresh_token": "1//refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "113627451144-apps.googleusercontent.com",
        "client_secret": "secret",
        "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
    }
    patched = GoogleCredentials(
        token="ya29.expired",
        refresh_token="1//refresh",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="113627451144-apps.googleusercontent.com",
        client_secret="secret",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    from datetime import datetime, timedelta, timezone

    patched.expiry = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    (auth_dir / "token.json").write_text(json.dumps(token))

    def fake_refresh(request):
        patched.token = "ya29.fresh"
        patched.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    patched.refresh = fake_refresh

    patched.refresh = fake_refresh
    monkeypatch.setattr(
        "agents.tools.gmail_oauth.Credentials.from_authorized_user_file",
        lambda *a, **k: patched,
    )
    monkeypatch.setattr(
        "agents.tools.gmail_oauth.Request",
        lambda *a, **k: "request-stub",
    )

    auth = GmailAuth(credentials_path=auth_dir)
    creds = auth.get_credentials()
    assert creds and creds.token == "ya29.fresh"


def test_get_gmail_client_returns_instance():
    assert get_gmail_client() is not None


def test_oauth_tools_registered_in_registry():
    assert {"gmail_list", "gmail_search", "gmail_read", "gmail_send", "gmail_mark"}.issubset(TOOL_NAMES)
    assert "gmail_latest" not in TOOL_NAMES


def test_gmail_list_dispatch(monkeypatch):
    fake_client = gmail_oauth.GmailClient.__new__(gmail_oauth.GmailClient)
    fake_client._service = object()
    from agents.tools.gmail_oauth import EmailMessage

    fake_client.list_messages = lambda query="", label_ids=None, max_results=10: [
        EmailMessage(
            id="msg1",
            subject="Hello",
            sender="a@b.com",
            to="me",
            date="12 Sep",
            snippet="short snippet",
        )
    ]
    monkeypatch.setattr(gmail_oauth, "get_gmail_client", lambda: fake_client)
    result = dispatch("gmail_list", max_results=5)
    assert "Hello" in result
    assert "a@b.com" in result


def test_gmail_list_unavailable_reports_clear_error(monkeypatch):
    fake_client = gmail_oauth.GmailClient.__new__(gmail_oauth.GmailClient)
    fake_client._service = None
    fake_client.is_available = lambda: False
    monkeypatch.setattr(gmail_oauth, "get_gmail_client", lambda: fake_client)
    result = dispatch("gmail_list")
    assert "not configured" in result.lower()