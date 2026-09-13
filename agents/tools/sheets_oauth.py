"""Google Sheets integration tools with OAuth2 authentication.

Mirrors :mod:`agents.tools.gmail_oauth` so the master's spreadsheets (expense
ledgers, workout logs, to-do lists) get a durable, human-editable home. When
run directly it authenticates and dumps the first rows of a spreadsheet:

    .venv/bin/python -m agents.tools.sheets_oauth <spreadsheet-id-or-url>
"""

import os
import re
import sys
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import unquote

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from brain.logging_setup import get_logger

logger = get_logger(__name__)

# Google Sheets API scopes
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

_SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


def _default_credentials_path() -> Path:
    """Pick the credentials directory for token.json and OAuth client secrets.

    Priority: an explicit ``SHEETS_CREDENTIALS_PATH`` env dir, then the existing
    ``~/.personal-assistant/sheets`` used by earlier setups, then the default
    ``~/.agents/sheets``. Never raises; falls back to the last option.
    """
    explicit = os.getenv("SHEETS_CREDENTIALS_PATH", "").strip()
    if explicit:
        return Path(unquote(explicit.removeprefix("file://")))
    for candidate in (Path.home() / ".personal-assistant" / "sheets", Path.home() / ".agents" / "sheets"):
        if candidate.exists():
            return candidate
    return Path.home() / ".agents" / "sheets"


def normalize_spreadsheet_id(spreadsheet_id: str) -> str:
    """Extract the raw spreadsheet ID from a URL, or pass the ID through."""
    match = _SPREADSHEET_ID_RE.search(spreadsheet_id)
    return match.group(1) if match else spreadsheet_id


class SheetsAuth:
    """Handles Google Sheets OAuth2 authentication."""

    def __init__(self, credentials_path: Optional[Path] = None):
        if credentials_path is None:
            credentials_path = _default_credentials_path()
        self.credentials_path = credentials_path
        self.credentials_path.mkdir(parents=True, exist_ok=True)
        self.token_path = self.credentials_path / "token.json"
        self.credentials_json_path = self.credentials_path / "credentials.json"

    @property
    def client_secrets_file(self) -> Optional[Path]:
        """Locate the OAuth client secrets file, honoring SHEETS_CLIENT_SECRET_PATH.

        Resolution order: an explicit ``credentials.json`` next to the token,
        then the env-provided ``SHEETS_CLIENT_SECRET_PATH`` (a ``file://`` URI
        is accepted), then a ``client_secret_*.json`` in the credentials
        directory.
        """
        if self.credentials_json_path.exists():
            return self.credentials_json_path
        raw = os.getenv("SHEETS_CLIENT_SECRET_PATH", "").strip()
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
        """Check if Sheets is configured."""
        return self.token_path.exists() or self.client_secrets_file is not None

    def get_sheets_service(self):
        """Get the Sheets API service."""
        creds = self.get_credentials()
        if not creds:
            return None
        return build("sheets", "v4", credentials=creds)


class SheetsClient:
    """Sheets client using the Google Sheets API."""

    def __init__(self, auth: Optional[SheetsAuth] = None):
        if auth is None:
            auth = SheetsAuth()
        self.auth = auth
        self._service = None

    @property
    def service(self):
        """Lazy-load the Sheets service."""
        if self._service is None:
            self._service = self.auth.get_sheets_service()
        return self._service

    def is_available(self) -> bool:
        """Check if Sheets is available."""
        return self.service is not None

    def read_range(self, spreadsheet_id: str, range_name: str) -> Optional[List[List[Any]]]:
        """Read a grid of cell values from a spreadsheet range."""
        if not self.service:
            return None
        try:
            result = (
                self.service.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=range_name)
                .execute()
            )
            return result.get("values", [])
        except Exception as exc:  # noqa: BLE001 - one bad call must not break pico
            logger.warning(f"[bold yellow]Sheets read failed[/bold yellow]: {exc}")
            return None

    def append_rows(self, spreadsheet_id: str, range_name: str, values: List[List[Any]]) -> bool:
        """Append rows of cell values to a spreadsheet range."""
        if not self.service:
            return False
        try:
            self.service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body={"values": values},
            ).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[bold yellow]Sheets append failed[/bold yellow]: {exc}")
            return False

    def update_range(self, spreadsheet_id: str, range_name: str, values: List[List[Any]]) -> bool:
        """Overwrite a spreadsheet range with cell values."""
        if not self.service:
            return False
        try:
            self.service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body={"values": values},
            ).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[bold yellow]Sheets update failed[/bold yellow]: {exc}")
            return False


def get_sheets_client() -> SheetsClient:
    """Get a Sheets client instance."""
    return SheetsClient()


# Tool functions for registration

def _as_rows(values: List[Any]) -> List[List[Any]]:
    """Normalize user-provided values into a list of rows (list of lists)."""
    if not values:
        return []
    if isinstance(values[0], list):
        return [list(row) for row in values]
    return [list(values)]


def sheets_read(spreadsheet_id: str, range_name: str) -> str:
    """Read a spreadsheet range and return the rows as formatted text.

    Args:
        spreadsheet_id: Spreadsheet URL or raw ID (URLs are normalized)
        range_name: Sheet range, e.g. "Transactions!A1:D200"

    Returns:
        Formatted string of the cell values
    """
    client = get_sheets_client()
    if not client.is_available():
        return "Error: Google Sheets not configured. Please set up OAuth2 credentials."

    sheet_id = normalize_spreadsheet_id(spreadsheet_id)
    rows = client.read_range(spreadsheet_id=sheet_id, range_name=range_name)
    if rows is None:
        return "Error: Failed to read the spreadsheet. Check the spreadsheet ID and range."
    if not rows:
        return "No rows found in the requested range."

    result = f"Spreadsheet data ({len(rows)} rows):\n\n"
    for i, row in enumerate(rows, 1):
        result += f"{i}. " + " | ".join(str(cell) for cell in row) + "\n"
    return result


def sheets_append(spreadsheet_id: str, range_name: str, values: List[Any]) -> str:
    """Append row(s) of values to a spreadsheet range.

    Args:
        spreadsheet_id: Spreadsheet URL or raw ID (URLs are normalized)
        range_name: Sheet range, e.g. "Transactions!A:D"
        values: One row ("a", "b", "c") or several rows [("a", 1), ("b", 2)]

    Returns:
        Status message
    """
    client = get_sheets_client()
    if not client.is_available():
        return "Error: Google Sheets not configured. Please set up OAuth2 credentials."

    rows = _as_rows(list(values))
    if not rows:
        return "Error: Nothing to append: values is empty."

    sheet_id = normalize_spreadsheet_id(spreadsheet_id)
    if client.append_rows(spreadsheet_id=sheet_id, range_name=range_name, values=rows):
        return f"Appended {len(rows)} row(s) to the spreadsheet."
    return "Error: Failed to append to the spreadsheet."


def sheets_update(spreadsheet_id: str, range_name: str, values: List[Any]) -> str:
    """Overwrite a spreadsheet range with new cell values.

    Args:
        spreadsheet_id: Spreadsheet URL or raw ID (URLs are normalized)
        range_name: Sheet range, e.g. "Transactions!B2:D2"
        values: One row ("a", "b", "c") or several rows [("a", 1), ("b", 2)]

    Returns:
        Status message
    """
    client = get_sheets_client()
    if not client.is_available():
        return "Error: Google Sheets not configured. Please set up OAuth2 credentials."

    rows = _as_rows(list(values))
    if not rows:
        return "Error: Nothing to update: values is empty."

    sheet_id = normalize_spreadsheet_id(spreadsheet_id)
    if client.update_range(spreadsheet_id=sheet_id, range_name=range_name, values=rows):
        return f"Updated {len(rows)} row(s) in the spreadsheet."
    return "Error: Failed to update the spreadsheet."


def main() -> int:
    """Authenticate with Google Sheets and print the top rows of a spreadsheet (one-shot CLI)."""
    client = get_sheets_client()
    if not client.is_available():
        print("Google Sheets not configured. Provide OAuth client secrets or run the auth flow.")
        return 1
    if len(sys.argv) < 2:
        print("Usage: python -m agents.tools.sheets_oauth <spreadsheet-id-or-url> [range]")
        return 1
    spreadsheet_id = sys.argv[1]
    range_name = sys.argv[2] if len(sys.argv) > 2 else "A1:Z100"
    print(sheets_read(spreadsheet_id=spreadsheet_id, range_name=range_name))
    return 0


if __name__ == "__main__":
    sys.exit(main())