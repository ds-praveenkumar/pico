"""Compatibility shim for the old ``agents.tools.email`` module name.

The real implementation lives in ``agents.tools.gmail_oauth``. This file only
re-exports its public symbols so older imports keep working. New code should
import from ``agents.tools.gmail_oauth``; running this file directly is not
supported because ``email.py`` shadows the Python standard library ``email``
package.
"""

from agents.tools.gmail_oauth import *  # noqa: F401,F403
from agents.tools.gmail_oauth import (  # noqa: F401
    EmailMessage,
    GmailAuth,
    GmailClient,
    get_gmail_client,
    list_emails,
    read_email,
    search_emails,
    send_email,
    mark_email,
)