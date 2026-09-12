"""Current date and time tool.

Returns the machine's local clock so pico never guesses what day it is.
Runs in-process (no shell), so it always matches the master's timezone.
"""

from datetime import datetime
from typing import Any, Dict


def current_date() -> Dict[str, Any]:
    """Return today's date and the current local time from the system clock."""
    now = datetime.now().astimezone()
    return {
        "ok": True,
        "date": now.strftime("%A, %B %d, %Y"),
        "time": now.strftime("%H:%M"),
        "timezone": now.tzname(),
        "iso": now.isoformat(),
    }