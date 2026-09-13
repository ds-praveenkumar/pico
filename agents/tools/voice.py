"""Text-to-speech tool for pico.

Speaks the master's text out loud using the built-in macOS ``say`` command,
so pico can answer verbally without pulling in any audio dependencies. Runs
in-process (no shell tool), times out, and returns a clear error on platforms
without ``say`` instead of crashing.
"""

import shutil
import subprocess
import sys
from typing import Any, Dict

from brain.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_RATE = 175
TIMEOUT_SECONDS = 30


def voice_speak(text: str, rate: int = DEFAULT_RATE) -> Dict[str, Any]:
    """Speak ``text`` aloud with the system voice at ``rate`` words per minute."""
    if sys.platform != "darwin":
        return {"ok": False, "error": "voice output is only supported on macOS (the `say` command)"}
    if not shutil.which("say"):
        return {"ok": False, "error": "the `say` command is not available on PATH"}
    text = text.strip()
    if not text:
        return {"ok": False, "error": "nothing to speak: text is empty"}
    rate = max(50, min(int(rate), 400))
    try:
        proc = subprocess.run(
            ["say", "-r", str(rate), text],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning(f"[bold yellow]voice output failed[/bold yellow]: {exc}")
        return {"ok": False, "error": str(exc)}
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip() or "say exited with an error"}
    logger.info(f"[bold green]Spoke aloud[/bold green]: {len(text)} chars at {rate} wpm")
    return {"ok": True, "spoken": text, "rate": rate, "chars": len(text)}