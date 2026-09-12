"""Tests for the Gmail IMAP tool (fully mocked, no network)."""

from unittest.mock import MagicMock

from agents.tools import gmail as gmail_module
from agents.tools.gmail import _build_message, gmail_latest, gmail_search

RAW_MESSAGE = (
    b"From: Boss <boss@example.com>\r\n"
    b"Subject: =?utf-8?q?Weekly_review_=E2=9C=93?=\r\n"
    b"Date: Mon, 10 Jan 2022 12:00:00 +0000\r\n"
    b"\r\n"
    b"Hello there, please send the summary."
)


class FakeIMAP:
    """A minimal IMAP4_SSL stand-in returning canned messages."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.logged_in = False

    def login(self, user, password):
        self.logged_in = True
        return "OK", []

    def select(self, folder, readonly=False):
        return "OK", []

    def search(self, query, *rest):
        return "OK", [b"1 2"]

    def fetch(self, uid, spec):
        return "OK", [(uid, RAW_MESSAGE)]

    def logout(self):
        return "OK"


class EmptyIMAP(FakeIMAP):
    """An IMAP stand-in whose inbox has no messages."""

    def search(self, query, *rest):
        return "OK", [b""]

    def fetch(self, uid, spec):
        raise AssertionError("fetch must not be called on an empty inbox")


def _configure(monkeypatch, factory=FakeIMAP):
    monkeypatch.setenv("GMAIL_IMAP_USER", "me@gmail.com")
    monkeypatch.setenv("GMAIL_IMAP_PASSWORD", "app-password")
    monkeypatch.setattr(gmail_module, "_IMAP_FACTORY", factory)


def test_build_message_parses_and_decodes():
    parsed = _build_message(b"1", RAW_MESSAGE)
    assert parsed["subject"] == "Weekly review ✓"
    assert parsed["from"] == "Boss <boss@example.com>"
    assert "send the summary" in parsed["snippet"]


def test_gmail_latest_requires_credentials(monkeypatch):
    monkeypatch.delenv("GMAIL_IMAP_USER", raising=False)
    monkeypatch.delenv("GMAIL_IMAP_PASSWORD", raising=False)
    result = gmail_latest()
    assert result["ok"] is False
    assert "GMAIL_IMAP_USER" in result["error"]


def test_gmail_latest_returns_messages(monkeypatch):
    _configure(monkeypatch)
    result = gmail_latest(limit=5)
    assert result["ok"] is True
    assert len(result["emails"]) == 2
    assert all(e["subject"] == "Weekly review ✓" for e in result["emails"])


def test_gmail_search_passes_query(monkeypatch):
    _configure(monkeypatch)
    imap = MagicMock()
    imap.select.return_value = ("OK", [])
    imap.search.return_value = ("OK", [b"42"])
    imap.fetch.return_value = ("OK", [(b"42", RAW_MESSAGE)])

    class Factory:
        def __new__(cls, host):
            return imap

    monkeypatch.setattr(gmail_module, "_IMAP_FACTORY", Factory)
    monkeypatch.setenv("GMAIL_IMAP_USER", "me@gmail.com")
    monkeypatch.setenv("GMAIL_IMAP_PASSWORD", "app-password")
    result = gmail_search("SUBJECT weekly")
    assert result["ok"] is True
    assert result["emails"][0]["uid"] == "42"
    imap.search.assert_called_once()
    args = imap.search.call_args.args
    assert args == (None, "SUBJECT weekly")


def test_gmail_empty_inbox(monkeypatch):
    _configure(monkeypatch, factory=EmptyIMAP)
    result = gmail_latest()
    assert result["ok"] is True
    assert result["emails"] == []


def test_gmail_connection_failure_reported(monkeypatch):
    monkeypatch.setenv("GMAIL_IMAP_USER", "me@gmail.com")
    monkeypatch.setenv("GMAIL_IMAP_PASSWORD", "app-password")

    class Broken:
        def __init__(self, host):
            pass

        def login(self, user, password):
            raise ConnectionError("network refused")

    monkeypatch.setattr(gmail_module, "_IMAP_FACTORY", Broken)
    result = gmail_latest()
    assert result["ok"] is False
    assert "network refused" in result["error"]