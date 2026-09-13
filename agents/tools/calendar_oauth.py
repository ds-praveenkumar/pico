"""Google Calendar integration tools with OAuth2 authentication.

Named ``calendar_oauth`` (not ``calendar``) so it never shadows the stdlib
``calendar`` package. When run directly it authenticates and lists upcoming
events:

    .venv/bin/python -m agents.tools.calendar_oauth
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from brain.logging_setup import get_logger

logger = get_logger(__name__)

# Google Calendar API scopes
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def _default_credentials_path() -> Path:
    """Pick the credentials directory for token.json and OAuth client secrets.

    Priority: an explicit ``CALENDAR_CREDENTIALS_PATH`` env dir, then the
    existing ``~/.personal-assistant/calendar`` used by earlier setups, then the
    default ``~/.agents/calendar``. Never raises; falls back to the last option.
    """
    explicit = os.getenv("CALENDAR_CREDENTIALS_PATH", "").strip()
    if explicit:
        return Path(unquote(explicit.removeprefix("file://")))
    for candidate in (Path.home() / ".personal-assistant" / "calendar", Path.home() / ".agents" / "calendar"):
        if candidate.exists():
            return candidate
    return Path.home() / ".agents" / "calendar"


class CalendarAuth:
    """Handles Google Calendar OAuth2 authentication."""

    def __init__(self, credentials_path: Optional[Path] = None):
        if credentials_path is None:
            credentials_path = _default_credentials_path()
        self.credentials_path = credentials_path
        self.credentials_path.mkdir(parents=True, exist_ok=True)
        self.token_path = self.credentials_path / "token.json"
        self.credentials_json_path = self.credentials_path / "credentials.json"

    @property
    def client_secrets_file(self) -> Optional[Path]:
        """Locate the OAuth client secrets file, honoring CALENDAR_CLIENT_SECRET_PATH.

        Resolution order: an explicit ``credentials.json`` next to the token,
        then the env-provided ``CALENDAR_CLIENT_SECRET_PATH`` (a ``file://`` URI
        is accepted), then a ``client_secret_*.json`` in the credentials
        directory.
        """
        if self.credentials_json_path.exists():
            return self.credentials_json_path
        raw = os.getenv("CALENDAR_CLIENT_SECRET_PATH", "").strip()
        if raw:
            path = Path(unquote(raw.removeprefix("file://")))
            if path.is_file():
                return path
        matches = sorted(self.credentials_path.glob("client_secret_*.json"))
        return matches[0] if matches else None

    def get_credentials(self) -> Optional[Credentials]:
        """Get valid credentials, refreshing or authorizing as needed."""
        creds = None

        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    creds = None

            if not creds:
                secrets_file = self.client_secrets_file
                if not secrets_file:
                    return None
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), SCOPES)
                    creds = flow.run_local_server(port=0)
                except Exception:
                    return None

            self.token_path.write_text(creds.to_json())

        return creds

    def is_configured(self) -> bool:
        """Check if Calendar is configured."""
        return self.token_path.exists() or self.client_secrets_file is not None

    def get_calendar_service(self):
        """Get the Calendar API service."""
        creds = self.get_credentials()
        if not creds:
            return None
        return build("calendar", "v3", credentials=creds)


class CalendarClient:
    """Calendar client using the Google Calendar API."""

    def __init__(self, auth: Optional[CalendarAuth] = None):
        if auth is None:
            auth = CalendarAuth()
        self.auth = auth
        self._service = None

    @property
    def service(self):
        """Lazy-load the Calendar service."""
        if self._service is None:
            self._service = self.auth.get_calendar_service()
        return self._service

    def is_available(self) -> bool:
        """Check if Calendar is available."""
        return self.service is not None

    def _event_dict(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Shrink a raw API event into a compact, LLM-friendly dict."""
        start = item.get("start", {})
        end = item.get("end", {})
        return {
            "id": item.get("id", ""),
            "summary": item.get("summary", ""),
            "start": start.get("dateTime") or start.get("date", ""),
            "end": end.get("dateTime") or end.get("date", ""),
            "location": item.get("location", ""),
            "description": (item.get("description") or "")[:500],
            "attendees": [a.get("email", "") for a in item.get("attendees", [])],
        }

    def list_events(self, max_results: int = 10, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """List upcoming events from the primary calendar in the next ``days_ahead`` days."""
        if not self.service:
            return []
        try:
            now = datetime.now(timezone.utc).isoformat()
            end = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).isoformat()
            results = (
                self.service.events()
                .list(
                    calendarId="primary",
                    timeMin=now,
                    timeMax=end,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            return [self._event_dict(item) for item in results.get("items", [])]
        except Exception as exc:  # noqa: BLE001 - one bad call must not break pico
            logger.warning(f"[bold yellow]Calendar listing failed[/bold yellow]: {exc}")
            return []

    def create_event(self, summary: str, start: str, end: str, description: str = "") -> Optional[str]:
        """Create an event on the primary calendar; return its ID or None."""
        if not self.service:
            return None
        try:
            body = {
                "summary": summary,
                "start": {"dateTime": start},
                "end": {"dateTime": end},
                "description": description,
            }
            result = self.service.events().insert(calendarId="primary", body=body).execute()
            return result.get("id")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[bold yellow]Event creation failed[/bold yellow]: {exc}")
            return None

    def respond_event(self, event_id: str, response: str) -> bool:
        """Set the master's attendance to accepted/declined/tentative on an event."""
        if not self.service:
            return False
        try:
            event = self.service.events().get(calendarId="primary", eventId=event_id).execute()
            attendees = event.get("attendees") or []
            attendee = next((a for a in attendees if a.get("self")), attendees[0] if attendees else None)
            if attendee is None:
                return False
            attendee["responseStatus"] = response
            self.service.events().patch(calendarId="primary", eventId=event_id, body={"attendees": attendees}).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[bold yellow]Event response failed[/bold yellow]: {exc}")
            return False


def get_calendar_client() -> CalendarClient:
    """Get a Calendar client instance."""
    return CalendarClient()


# Tool functions for registration

def calendar_list(max_results: int = 10, days_ahead: int = 7) -> str:
    """List upcoming events from the master's primary calendar.

    Args:
        max_results: Maximum number of events to return
        days_ahead: How many days of events to look ahead

    Returns:
        Formatted string with the upcoming events
    """
    client = get_calendar_client()
    if not client.is_available():
        return "Error: Google Calendar not configured. Please set up OAuth2 credentials."

    events = client.list_events(max_results=max_results, days_ahead=days_ahead)
    if not events:
        return f"No upcoming events in the next {days_ahead} day(s)."

    result = f"Upcoming events (next {days_ahead} day(s)):\n\n"
    for i, event in enumerate(events, 1):
        result += f"{i}. {event['summary']}\n"
        result += f"   Start: {event['start']}\n"
        if event["end"]:
            result += f"   End: {event['end']}\n"
        if event["location"]:
            result += f"   Location: {event['location']}\n"
        if event["description"]:
            result += f"   Description: {event['description']}\n"
        result += f"   ID: {event['id']}\n\n"

    return result


def calendar_create(summary: str, start: str, end: str, description: str = "") -> str:
    """Create an event on the master's primary calendar.

    Args:
        summary: Event title
        start: Start time in ISO 8601 format (e.g. "2026-09-14T09:00:00")
        end: End time in ISO 8601 format
        description: Optional event description

    Returns:
        Status message with the new event ID
    """
    client = get_calendar_client()
    if not client.is_available():
        return "Error: Google Calendar not configured. Please set up OAuth2 credentials."

    event_id = client.create_event(summary=summary, start=start, end=end, description=description)
    if event_id:
        return f"Event created successfully. Event ID: {event_id}"
    return "Error: Failed to create event"


def calendar_respond(event_id: str, response: str) -> str:
    """Set the master's attendance status on a calendar event.

    Args:
        event_id: Google Calendar event ID
        response: "accepted", "declined", or "tentative"

    Returns:
        Status message
    """
    if response.lower() not in ("accepted", "declined", "tentative"):
        return "Error: Unknown response. Use 'accepted', 'declined', or 'tentative'."

    client = get_calendar_client()
    if not client.is_available():
        return "Error: Google Calendar not configured. Please set up OAuth2 credentials."

    if client.respond_event(event_id=event_id, response=response.lower()):
        return f"Event response set to {response.lower()}"
    return "Error: Failed to update event response"


def main() -> int:
    """Authenticate with Google Calendar and print upcoming events (one-shot CLI)."""
    client = get_calendar_client()
    if not client.is_available():
        print("Google Calendar not configured. Provide OAuth client secrets or run the auth flow.")
        return 1
    print(calendar_list(max_results=5, days_ahead=7))
    return 0


if __name__ == "__main__":
    sys.exit(main())