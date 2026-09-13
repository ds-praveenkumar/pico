"""Tests for redacted, opt-in task history capture backed by JSONL.

The history store is private-by-default: nothing is written until the master
enables capture in the Textual settings screen, and every record is scrubbed of
credentials, email addresses, and personal tool payloads before it touches disk.
"""

import json
import os
from pathlib import Path

from tui_history import HistoryStore, redact

_ZERO_USAGE = {"prompt": 0, "completion": 0, "total": 0}


def test_redact_keeps_structure_and_hides_credentials():
    value = {"command": "cat README.md", "password": "hunter2", "api_key": "sk-secret1234567890"}
    out = redact(value)
    assert out["command"] == "cat README.md"
    assert "<redacted>" in out["password"]
    assert "hunter2" not in out["password"]
    assert "sk-secret1234567890" not in json.dumps(out)


def test_redact_removes_email_addresses():
    out = redact("mail me at praveen@example.com or call")
    assert "praveen@example.com" not in out
    assert "[email redacted]" in out


def test_redact_scrubs_personal_tool_payloads():
    payload = {"ok": True, "subject": "Private meeting", "body": "reschedule please", "email": "boss@example.com"}
    out = redact(payload, tool_name="gmail_read")
    assert out["ok"] is True
    assert out["subject"] == "[personal payload redacted]"
    assert out["body"] == "[personal payload redacted]"
    assert out["email"] == "[personal payload redacted]"
    assert "boss@example.com" not in json.dumps(out)


def test_redact_treats_personal_lists_as_redacted():
    out = redact(["alpha", "beta"], tool_name="calendar_list")
    assert out == ["[personal payload redacted]", "[personal payload redacted]"]


def test_store_is_opt_in(tmp_path):
    store = HistoryStore(path=tmp_path)
    store.record("summarize README.md", "done", [], _ZERO_USAGE, [])
    assert not store.path.exists()


def test_store_records_and_persists_when_enabled(tmp_path):
    store = HistoryStore(path=tmp_path)
    store.enabled = True
    record = store.record(
        "hello world",
        "hi master",
        [{"agent": "executor", "task": "say hi"}],
        {"prompt": 1, "completion": 2, "total": 3},
        [{"agent": "executor", "text": "ok", "ts": "2026-01-01T00:00:00Z"}],
    )
    assert record["status"] == "completed"
    records = store.load()
    assert len(records) == 1
    assert records[0]["task"] == "hello world"
    assert records[0]["usage"]["total"] == 3
    assert records[0]["events"][0]["text"] == "ok"
    loaded = json.loads(store.path.read_text(encoding="utf-8").splitlines()[0])
    assert loaded["reply"] == "hi master"


def test_store_enforces_limit_newest_first(tmp_path):
    store = HistoryStore(path=tmp_path, limit=3)
    store.enabled = True
    for i in range(5):
        store.record(f"task {i}", f"reply {i}", [], _ZERO_USAGE, [])
    records = store.load()
    assert [r["task"] for r in records] == ["task 4", "task 3", "task 2"]


def test_export_writes_redacted_jsonl(tmp_path):
    store = HistoryStore(path=tmp_path)
    store.enabled = True
    store.record("login", "all good, token=abc123secret", [], _ZERO_USAGE, [])
    target = tmp_path / "sub" / "export.jsonl"
    count = store.export(target)
    assert count == 1
    text = target.read_text(encoding="utf-8")
    assert "abc123secret" not in text
    assert json.loads(text.splitlines()[0])["task"] == "login"


def test_read_tolerates_corrupt_file(tmp_path):
    store = HistoryStore(path=tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("this is not json\n", encoding="utf-8")
    assert store.load() == []


def test_default_base_uses_pico_memory_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PICO_MEMORY_PATH", str(tmp_path))
    store = HistoryStore()
    assert store.path == tmp_path / "tui_history.jsonl"