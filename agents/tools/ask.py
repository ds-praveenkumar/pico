"""Ask-the-master tool — the supervised channel for information pico must not guess.

CAPTCHAs, logins, OTPs, file uploads, and forms that need the master's own
details are never improvised: the agent stops and asks. ``ask_master`` routes
the question through the active prompt handler (the TUI dashboard in full-screen
mode, a plain console prompt otherwise) and returns an explicit
``NO_ANSWER_FROM_UNATTENDED_RUN`` sentinel in unattended (``-y``) runs so the
agent can stop and report instead of inventing an answer.
"""

import builtins
import os
import sys
from typing import Callable, Dict, Optional

from brain.logging_setup import get_logger

logger = get_logger(__name__)

UNATTENDED_ANSWER = "NO_ANSWER_FROM_UNATTENDED_RUN"

_master_prompt: Optional[Callable[[str], str]] = None


def set_master_prompt(handler: Optional[Callable[[str], str]]) -> None:
    """Install (or clear) the handler used to ask the master a free-text question.

    ``app.py`` wires this to the dashboard so questions render inside the live
    TUI instead of blocking invisibly on a bare ``input()``; the default is a
    plain console prompt.
    """
    global _master_prompt
    _master_prompt = handler


def ask_master(question: str) -> Dict[str, object]:
    """Ask the master a free-text question and return their answer."""
    if os.environ.get("PICO_AUTO_APPROVE") == "1" or not sys.stdin.isatty():
        logger.info("[dim]ask_master skipped (unattended or piped run)[/dim]")
        return {
            "ok": False,
            "question": question,
            "error": "unattended run: no one available to answer; stop rather than guess",
            "answer": UNATTENDED_ANSWER,
        }
    prompt_text = f"pico needs the master: {question}"
    try:
        if _master_prompt is not None:
            answer = _master_prompt(prompt_text)
        else:
            print(f"\n[dim]{prompt_text}[/dim]")
            answer = builtins.input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        return {
            "ok": False,
            "question": question,
            "error": "answer was cancelled",
            "answer": "",
        }
    if not answer:
        return {
            "ok": False,
            "question": question,
            "error": "no answer given",
            "answer": "",
        }
    logger.info("[dim]master answered[/dim]")
    return {"ok": True, "question": question, "answer": answer}