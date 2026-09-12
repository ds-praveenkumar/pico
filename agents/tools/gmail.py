"""Gmail access tool using the stdlib IMAP client.

Reads recent emails from the master's Gmail account over IMAP. Credentials come
from the environment (an app password, never the account password):

- ``GMAIL_IMAP_USER`` — the Gmail address
- ``GMAIL_IMAP_PASSWORD`` — an app password or IMAP-enabled password
- ``GMAIL_IMAP_HOST`` (optional) — defaults to ``imap.gmail.com``

Only headers and message snippets are read; emails are never deleted or
modified. All network access happens in-process via the stdlib only.
"""

import email
import os
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from imaplib import IMAP4_SSL
from typing import Any, Dict, List, Optional

from brain.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_HOST = "imap.gmail.com"

_IMAP_FACTORY = IMAP4_SSL


def _credentials() -> Optional[tuple]:
    user = os.getenv("GMAIL_IMAP_USER", "").strip()
    password = os.getenv("GMAIL_IMAP_PASSWORD", "").strip()
    if not user or not password:
        return None
    return user, password


def _header(value: Optional[str]) -> str:
    """Decode an (already folded) RFC2047 header into a plain string."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 - a malformed header must not break the listing
        return value


def _snippet(body_payload: bytes) -> str:
    """Extract a short plain-text preview from an RFC822 payload."""
    text = body_payload.decode("utf-8", errors="replace")
    return " ".join(text.split())[:200]


def _build_message(uid: bytes, raw: bytes) -> Dict[str, Any]:
    """Parse one raw RFC822 message into a summary dict."""
    message = email.message_from_bytes(raw)
    date = message.get("Date", "")
    try:
        when = parsedate_to_datetime(date)
        date = when.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001 - keep the raw date if parsing fails
        pass
    snippet = ""
    for part in message.walk():
        if part.get_content_type() == "text/plain":
            snippet = _snippet(part.get_payload(decode=True) or b"")
            break
    return {
        "uid": uid.decode("ascii", errors="ignore"),
        "subject": _header(message.get("Subject", "")),
        "from": _header(message.get("From", "")),
        "date": date,
        "snippet": snippet,
    }


def _read_messages(connection: IMAP4_SSL, query: str, limit: int, folder: str) -> Dict[str, Any]:
    """Run a search + fetch cycle and return parsed message summaries."""
    try:
        status, _ = connection.select(f'"{folder}"', readonly=True)
        if status != "OK":
            return {"ok": False, "error": f"could not select folder {folder!r}"}
        status, data = connection.search(None, query)
        if status != "OK" or not data or not data[0]:
            return {"ok": True, "emails": [], "readonly": True}
        uids = data[0].split()
        wanted = uids[-limit:]
        messages: List[Dict[str, Any]] = []
        for uid in reversed(wanted):  # newest first
            status, fetched = connection.fetch(uid, "(BODY.PEEK[TEXT] BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
            if status != "OK" or not fetched:
                continue
            raw = b"".join(part[1] for part in fetched if isinstance(part, tuple) and part[1])
            messages.append(_build_message(uid, raw))
        return {"ok": True, "emails": messages, "readonly": True}
    except Exception as exc:  # noqa: BLE001 - surface connection errors to the agent
        logger.warning(f"[bold yellow]Gmail read failed[/bold yellow]: {exc}")
        return {"ok": False, "error": str(exc)}


def gmail_latest(limit: int = 5, folder: str = "INBOX", unread_only: bool = False) -> Dict[str, Any]:
    """Return the most recent emails (optionally only unread) as summaries."""
    creds = _credentials()
    if creds is None:
        return {
            "ok": False,
            "error": "GMAIL_IMAP_USER / GMAIL_IMAP_PASSWORD are not set; ask the master to configure them",
        }
    try:
        limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        limit = 5
    host = os.getenv("GMAIL_IMAP_HOST", DEFAULT_HOST).strip()
    query = "UNSEEN" if unread_only else "ALL"
    connection = _IMAP_FACTORY(host)
    try:
        connection.login(*creds)
        logger.info(f"[bold green]Gmail connected[/bold green]: {creds[0]}")
        result = _read_messages(connection, query, limit, folder)
        connection.logout()
        return result
    except Exception as exc:  # noqa: BLE001 - connection failures are reported, never fatal
        try:
            connection.logout()
        except Exception:  # noqa: BLE001 - the connection may already be broken
            pass
        logger.warning(f"[bold yellow]Gmail connection failed[/bold yellow]: {exc}")
        return {"ok": False, "error": f"could not connect to Gmail: {exc}"}


def gmail_search(query: str, limit: int = 5, folder: str = "INBOX") -> Dict[str, Any]:
    """Search email headers/body for a query string (IMAP SEARCH syntax)."""
    creds = _credentials()
    if creds is None:
        return {
            "ok": False,
            "error": "GMAIL_IMAP_USER / GMAIL_IMAP_PASSWORD are not set; ask the master to configure them",
        }
    try:
        limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        limit = 5
    host = os.getenv("GMAIL_IMAP_HOST", DEFAULT_HOST).strip()
    imap_query = query if query and query.strip() else "ALL"
    connection = _IMAP_FACTORY(host)
    try:
        connection.login(*creds)
        logger.info(f"[bold green]Gmail search[/bold green]: {imap_query}")
        result = _read_messages(connection, imap_query, limit, folder)
        connection.logout()
        return result
    except Exception as exc:  # noqa: BLE001
        try:
            connection.logout()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(f"[bold yellow]Gmail search failed[/bold yellow]: {exc}")
        return {"ok": False, "error": f"could not connect to Gmail: {exc}"}