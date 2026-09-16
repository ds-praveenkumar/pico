"""Terminal font-size control for pico's UIs.

Modern terminals (macOS Terminal.app, iTerm2, Ghostty, kitty, Warp, VS Code)
honour the mintty ``OSC 7770`` sequence ``ESC ] 7770 ; SIZE BEL`` by resizing
the session font to ``SIZE`` points — the same operation as the Ctrl+/Ctrl-
zoom keys. pico reads ``PICO_FONT_SIZE`` at startup and emits the sequence
before entering a full-screen UI, so the master can pick a larger (or smaller)
font without touching terminal preferences. Unsupported terminals ignore the
sequence harmlessly.
"""

from __future__ import annotations

import os
import sys
from typing import Optional, TextIO

FONT_SIZES = [10, 12, 14, 16, 18, 20, 24, 28, 32]
"""Point sizes offered on the Settings screen."""

_OSC_FONT_SIZE = "\x1b]7770;{size}\x07"
_MIN_SIZE = 6
_MAX_SIZE = 200


def font_size_from_env() -> Optional[int]:
    """Return the point size configured via ``PICO_FONT_SIZE``, or None."""
    raw = os.getenv("PICO_FONT_SIZE", "").strip()
    if not raw:
        return None
    try:
        size = int(raw)
    except ValueError:
        return None
    return size if _MIN_SIZE <= size <= _MAX_SIZE else None


def font_size_sequence(size: int) -> str:
    """The OSC 7770 sequence that sets the terminal font to ``size`` points."""
    return _OSC_FONT_SIZE.format(size=int(size))


def apply_font_size(size: Optional[int] = None, stream: Optional[TextIO] = None) -> bool:
    """Emit the font-size sequence for ``size`` (env fallback; a TTY only).

    ``None``/unset sizes and non-terminal streams are left untouched so no
    garbage ever reaches pipes or the log. Returns True when a sequence was
    actually written.
    """
    if size is None:
        size = font_size_from_env()
    if size is None:
        return False
    stream = stream if stream is not None else sys.stdout
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    stream.write(font_size_sequence(size))
    stream.flush()
    return True