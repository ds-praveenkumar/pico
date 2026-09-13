"""Gmail integration tools with OAuth2 authentication.

Named ``gmail_oauth`` (not ``email``) so it never shadows the stdlib ``email``
package. When run directly it authenticates and lists recent mail:

    .venv/bin/python -m agents.tools.gmail_oauth
"""

import base64
import os
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from urllib.parse import unquote

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


# Gmail API scopes
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


@dataclass
class EmailMessage:
    """Parsed email message."""

    id: str
    subject: str
    sender: str
    to: str
    date: str
    snippet: str
    body: str = ""
    labels: list[str] = None
    is_unread: bool = False

    def __post_init__(self):
        if self.labels is None:
            self.labels = []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subject": self.subject,
            "sender": self.sender,
            "to": self.to,
            "date": self.date,
            "snippet": self.snippet,
            "body": self.body[:2000],
            "labels": self.labels,
            "is_unread": self.is_unread,
        }


def _default_credentials_path() -> Path:
    """Pick the credentials directory for token.json and OAuth client secrets.

    Priority: an explicit ``GMAIL_CREDENTIALS_PATH`` env dir, then the existing
    ``~/.personal-assistant/gmail`` used by earlier setups, then the default
    ``~/.agents/gmail``. Never raises; falls back to the last option.
    """
    explicit = os.getenv("GMAIL_CREDENTIALS_PATH", "").strip()
    if explicit:
        return Path(unquote(explicit.removeprefix("file://")))
    for candidate in (Path.home() / ".personal-assistant" / "gmail", Path.home() / ".agents" / "gmail"):
        if candidate.exists():
            return candidate
    return Path.home() / ".agents" / "gmail"


class GmailAuth:
    """Handles Gmail OAuth2 authentication."""

    def __init__(self, credentials_path: Optional[Path] = None):
        if credentials_path is None:
            credentials_path = _default_credentials_path()
        self.credentials_path = credentials_path
        self.credentials_path.mkdir(parents=True, exist_ok=True)
        self.token_path = self.credentials_path / "token.json"
        self.credentials_json_path = self.credentials_path / "credentials.json"

    @property
    def client_secrets_file(self) -> Optional[Path]:
        """Locate the OAuth client secrets file, honoring GMAIL_CLIENT_SECRET_PATH.

        Resolution order: an explicit ``credentials.json`` next to the token,
        then the env-provided ``GMAIL_CLIENT_SECRET_PATH`` (a ``file://`` URI is
        accepted), then a ``client_secret_*.json`` in the credentials directory.
        """
        if self.credentials_json_path.exists():
            return self.credentials_json_path
        raw = os.getenv("GMAIL_CLIENT_SECRET_PATH", "").strip()
        if raw:
            path = Path(unquote(raw.removeprefix("file://")))
            if path.is_file():
                return path
        matches = sorted(self.credentials_path.glob("client_secret_*.json"))
        return matches[0] if matches else None

    def get_credentials(self) -> Optional[Credentials]:
        """Get valid credentials, refreshing or authorizing as needed."""
        creds = None

        # Load existing token
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            except Exception:
                creds = None

        # Refresh or get new credentials
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
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(secrets_file), SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                except Exception:
                    return None

            # Save the credentials
            self.token_path.write_text(creds.to_json())

        return creds

    def is_configured(self) -> bool:
        """Check if Gmail is configured."""
        return self.token_path.exists() or self.client_secrets_file is not None

    def get_gmail_service(self):
        """Get Gmail API service."""
        creds = self.get_credentials()
        if not creds:
            return None
        return build("gmail", "v1", credentials=creds)


class GmailClient:
    """Gmail client using Gmail API."""

    def __init__(self, auth: Optional[GmailAuth] = None):
        if auth is None:
            auth = GmailAuth()
        self.auth = auth
        self._service = None

    @property
    def service(self):
        """Lazy-load Gmail service."""
        if self._service is None:
            self._service = self.auth.get_gmail_service()
        return self._service

    def is_available(self) -> bool:
        """Check if Gmail is available."""
        return self.service is not None

    def list_messages(
        self,
        query: str = "",
        label_ids: Optional[list[str]] = None,
        max_results: int = 10,
    ) -> list[EmailMessage]:
        """List messages matching a query."""
        if not self.service:
            return []

        try:
            params = {"userId": "me", "maxResults": max_results}
            if query:
                params["q"] = query
            if label_ids:
                params["labelIds"] = label_ids

            results = self.service.users().messages().list(**params).execute()
            messages = results.get("messages", [])

            email_messages = []
            for msg in messages:
                email_msg = self._get_message(msg["id"])
                if email_msg:
                    email_messages.append(email_msg)

            return email_messages
        except Exception as e:
            print(f"Error listing messages: {e}")
            return []

    def get_message(self, message_id: str) -> Optional[EmailMessage]:
        """Get a specific message by ID."""
        return self._get_message(message_id)

    def _get_message(self, message_id: str) -> Optional[EmailMessage]:
        """Internal method to get message details."""
        if not self.service:
            return None

        try:
            message = (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )

            headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}

            # Extract body
            body = self._extract_body(message.get("payload", {}))

            # Check if unread
            labels = message.get("labelIds", [])
            is_unread = "UNREAD" in labels

            return EmailMessage(
                id=message["id"],
                subject=headers.get("subject", "No Subject"),
                sender=headers.get("from", "Unknown"),
                to=headers.get("to", "Unknown"),
                date=headers.get("date", "Unknown"),
                snippet=message.get("snippet", ""),
                body=body,
                labels=labels,
                is_unread=is_unread,
            )
        except Exception as e:
            print(f"Error getting message: {e}")
            return None

    def _extract_body(self, payload: dict) -> str:
        """Extract body from message payload."""
        body = ""

        if "parts" in payload:
            for part in payload["parts"]:
                if part["mimeType"] == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                        break
                elif part["mimeType"] == "text/html" and not body:
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        else:
            data = payload.get("body", {}).get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

        return body[:5000]  # Limit body size

    def search_messages(self, query: str, max_results: int = 10) -> list[EmailMessage]:
        """Search messages using Gmail search syntax."""
        return self.list_messages(query=query, max_results=max_results)

    def send_message(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to: Optional[str] = None,
        is_html: bool = False,
    ) -> Optional[str]:
        """Send an email message."""
        if not self.service:
            return None

        try:
            message = MIMEMultipart()
            message["to"] = to
            message["subject"] = subject
            if reply_to:
                message["In-Reply-To"] = reply_to
                message["References"] = reply_to

            content_type = "html" if is_html else "plain"
            message.attach(MIMEText(body, content_type))

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

            result = (
                self.service.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )

            return result.get("id")
        except Exception as e:
            print(f"Error sending message: {e}")
            return None

    def mark_as_read(self, message_id: str) -> bool:
        """Mark a message as read."""
        return self._modify_labels(message_id, remove_labels=["UNREAD"])

    def mark_as_unread(self, message_id: str) -> bool:
        """Mark a message as unread."""
        return self._modify_labels(message_id, add_labels=["UNREAD"])

    def add_label(self, message_id: str, label_name: str) -> bool:
        """Add a label to a message."""
        label_id = self._get_or_create_label(label_name)
        if label_id:
            return self._modify_labels(message_id, add_labels=[label_id])
        return False

    def _modify_labels(
        self,
        message_id: str,
        add_labels: Optional[list[str]] = None,
        remove_labels: Optional[list[str]] = None,
    ) -> bool:
        """Modify labels on a message."""
        if not self.service:
            return False

        try:
            body = {}
            if add_labels:
                body["addLabelIds"] = add_labels
            if remove_labels:
                body["removeLabelIds"] = remove_labels

            self.service.users().messages().modify(
                userId="me", id=message_id, body=body
            ).execute()
            return True
        except Exception as e:
            print(f"Error modifying labels: {e}")
            return False

    def _get_or_create_label(self, label_name: str) -> Optional[str]:
        """Get or create a Gmail label."""
        if not self.service:
            return None

        try:
            # List existing labels
            results = self.service.users().labels().list(userId="me").execute()
            for label in results.get("labels", []):
                if label["name"].lower() == label_name.lower():
                    return label["id"]

            # Create new label
            label = (
                self.service.users()
                .labels()
                .create(
                    userId="me",
                    body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
                )
                .execute()
            )
            return label["id"]
        except Exception as e:
            print(f"Error getting/creating label: {e}")
            return None


def get_gmail_client() -> GmailClient:
    """Get a Gmail client instance."""
    return GmailClient()


# Tool functions for registration

def list_emails(
    query: str = "",
    unread_only: bool = False,
    max_results: int = 10,
) -> str:
    """List emails matching criteria.

    Args:
        query: Gmail search query (e.g., "from:example@gmail.com", "subject:meeting")
        unread_only: If True, only show unread emails
        max_results: Maximum number of emails to return

    Returns:
        Formatted string with email list
    """
    client = get_gmail_client()
    if not client.is_available():
        return "Error: Gmail not configured. Please set up OAuth2 credentials."

    label_ids = ["UNREAD"] if unread_only else None
    messages = client.list_messages(query=query, label_ids=label_ids, max_results=max_results)

    if not messages:
        return "No emails found matching the criteria."

    result = f"Found {len(messages)} emails:\n\n"
    for i, msg in enumerate(messages, 1):
        unread_marker = " [UNREAD]" if msg.is_unread else ""
        result += f"{i}. {msg.subject}{unread_marker}\n"
        result += f"   From: {msg.sender}\n"
        result += f"   Date: {msg.date}\n"
        result += f"   Snippet: {msg.snippet[:100]}...\n"
        result += f"   ID: {msg.id}\n\n"

    return result


def read_email(message_id: str) -> str:
    """Read a specific email by ID.

    Args:
        message_id: Gmail message ID

    Returns:
        Full email content
    """
    client = get_gmail_client()
    if not client.is_available():
        return "Error: Gmail not configured."

    msg = client.get_message(message_id)
    if not msg:
        return f"Error: Email not found with ID {message_id}"

    result = f"Subject: {msg.subject}\n"
    result += f"From: {msg.sender}\n"
    result += f"To: {msg.to}\n"
    result += f"Date: {msg.date}\n"
    result += f"Labels: {', '.join(msg.labels)}\n"
    result += f"\n{msg.body}\n"

    return result


def search_emails(query: str, max_results: int = 10) -> str:
    """Search emails using Gmail search syntax.

    Args:
        query: Gmail search query
        max_results: Maximum number of results

    Returns:
        Formatted search results
    """
    client = get_gmail_client()
    if not client.is_available():
        return "Error: Gmail not configured."

    messages = client.search_messages(query, max_results)

    if not messages:
        return f"No emails found for query: {query}"

    result = f"Search results for: {query}\n\n"
    for i, msg in enumerate(messages, 1):
        result += f"{i}. {msg.subject}\n"
        result += f"   From: {msg.sender}\n"
        result += f"   Date: {msg.date}\n"
        result += f"   ID: {msg.id}\n\n"

    return result


def send_email(
    to: str,
    subject: str,
    body: str,
    reply_to: Optional[str] = None,
) -> str:
    """Send an email.

    Args:
        to: Recipient email address
        subject: Email subject
        body: Email body content
        reply_to: Optional message ID to reply to

    Returns:
        Status message
    """
    client = get_gmail_client()
    if not client.is_available():
        return "Error: Gmail not configured."

    message_id = client.send_message(to=to, subject=subject, body=body, reply_to=reply_to)

    if message_id:
        return f"Email sent successfully. Message ID: {message_id}"
    else:
        return "Error: Failed to send email"


def mark_email(message_id: str, status: str = "read") -> str:
    """Mark an email as read, unread, or flagged.

    Args:
        message_id: Gmail message ID
        status: "read", "unread", or "flagged"

    Returns:
        Status message
    """
    client = get_gmail_client()
    if not client.is_available():
        return "Error: Gmail not configured."

    if status == "read":
        success = client.mark_as_read(message_id)
    elif status == "unread":
        success = client.mark_as_unread(message_id)
    elif status == "flagged":
        success = client.add_label(message_id, "Starred")
    else:
        return f"Error: Unknown status '{status}'. Use 'read', 'unread', or 'flagged'."

    if success:
        return f"Email marked as {status}"
    else:
        return f"Error: Failed to mark email as {status}"


def main() -> int:
    """Authenticate with Gmail and print recent mail (one-shot CLI)."""
    client = get_gmail_client()
    if not client.is_available():
        print("Gmail not configured. Provide OAuth client secrets or run the auth flow.")
        return 1
    print(list_emails(query="", unread_only=False, max_results=5))
    return 0


if __name__ == "__main__":
    sys.exit(main())
