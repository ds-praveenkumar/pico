"""Redacted, session-opt-in task history for the Textual interface."""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer|token|api[_-]?key|password|secret|authorization)\b\s*[:=]\s*[^\s,;]+"),
)
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_KEY_TOKEN_PATTERN = re.compile(r"\b(?:sk|gsk|nvapi|aaR)[-_A-Za-z0-9]{16,}\b")
_SECRET_KEYS = {
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "client_secret",
    "authorization",
    "bearer",
    "auth",
    "credential",
}
_PERSONAL_TOOLS = {
    "gmail_list",
    "gmail_search",
    "gmail_read",
    "gmail_send",
    "gmail_mark",
    "ego_lite_browse_use",
    "calendar_list",
    "calendar_create",
    "calendar_respond",
    "sheets_read",
    "sheets_append",
    "sheets_update",
    "voice_speak",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _mask_secret(match: "re.Match[str]") -> str:
    """Mask a ``key: value`` secret, keeping only the key name.

    The captured run spans the whole ``key<sep>value``; the value (including any
    equal/colon inside quoted content) is dropped so nothing sensitive survives.
    """
    full = match.group(0)
    separator = "=" if "=" in full else ":"
    return f"{full.split(separator, 1)[0]}{separator}<redacted>"


def redact(value: Any, tool_name: Optional[str] = None) -> Any:
    """Return a copy of ``value`` with credentials and personal payloads removed."""
    if tool_name in _PERSONAL_TOOLS:
        if isinstance(value, dict):
            kept: Dict[str, Any] = {}
            for key, item in value.items():
                if str(key).lower() in {"ok", "error", "status", "message_id", "subject", "sender", "from", "date", "unread", "need_human", "paused"}:
                    kept[str(key)] = redact(item, tool_name)
                else:
                    kept[str(key)] = "[personal payload redacted]"
            return kept
        if isinstance(value, list):
            return ["[personal payload redacted]" for _ in value]
        if isinstance(value, str):
            return "[personal payload redacted]"
        return value
    if isinstance(value, dict):
        kept: Dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace(" ", "_")
            if normalized in _SECRET_KEYS and isinstance(item, str):
                kept[str(key)] = "<redacted>"
            else:
                kept[str(key)] = redact(item, tool_name)
        return kept
    if isinstance(value, list):
        return [redact(item, tool_name) for item in value]
    if isinstance(value, tuple):
        return [redact(item, tool_name) for item in value]
    if isinstance(value, str):
        cleaned = value
        for pattern in _SECRET_PATTERNS:
            cleaned = pattern.sub(_mask_secret, cleaned)
        cleaned = _EMAIL_PATTERN.sub("[email redacted]", cleaned)
        cleaned = _KEY_TOKEN_PATTERN.sub("[key redacted]", cleaned)
        return cleaned
    return value


class HistoryStore:
    """Persist up to 200 completed, redacted task records as JSONL."""

    def __init__(self, path: Optional[Path] = None, limit: int = 200) -> None:
        base = Path(path) if path is not None else Path(os.getenv("PICO_MEMORY_PATH", Path.home() / ".pico"))
        self.path = base / "tui_history.jsonl"
        self.limit = limit
        self.enabled = False

    def _read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        records: List[Dict[str, Any]] = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    if isinstance(record, dict):
                        records.append(record)
        except (OSError, json.JSONDecodeError):
            return []
        return records[-self.limit :]

    def _write(self, records: Iterable[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "".join(json.dumps(record, ensure_ascii=False, default=str) + "\n" for record in records),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def record(
        self,
        task: str,
        reply: str,
        plan: List[Dict[str, str]],
        usage: Dict[str, int],
        events: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Record a completed task when history capture is enabled."""
        if not self.enabled:
            return None
        record = {
            "id": uuid4().hex,
            "ts": _now(),
            "status": "completed",
            "task": redact(task.strip()),
            "reply": redact(reply.strip()),
            "plan": redact(plan),
            "usage": dict(usage),
            "events": redact(events),
        }
        records = self._read()
        records.append(record)
        self._write(records[-self.limit :])
        return record

    def load(self) -> List[Dict[str, Any]]:
        """Return recent records, newest first."""
        return list(reversed(self._read()))

    def export(self, path: Path) -> int:
        """Export redacted records to a user-approved JSONL path."""
        records = self._read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(record, ensure_ascii=False, default=str) + "\n" for record in records),
            encoding="utf-8",
        )
        return len(records)
