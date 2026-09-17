"""Ask-the-master tools — pico's supervised channels for information it must not guess.

CAPTCHAs, logins, OTPs, file uploads, and forms that need the master's own
details are never improvised: the agent stops and asks. ``ask_master`` routes
the question through the active prompt handler (the TUI dashboard in full-screen
mode, a plain console prompt otherwise) and returns an explicit
``NO_ANSWER_FROM_UNATTENDED_RUN`` sentinel in unattended (``-y``) runs so the
agent can stop and report instead of inventing an answer.

An interactive answer is a directive, not a finished result: the result carries
a ``continue_work`` hint telling the agent to act on the reply (navigate the
browser to the page the master names, click/fill what they point to) and keep
going until the original goal is done, instead of reporting the reply and
stopping.

``notify_master`` is the non-blocking sibling: it tells the master what to do
(for example "solve the CAPTCHA in the open browser window") without waiting for
an answer, so a long-running tool can keep working while the master acts.
"""

import builtins
import os
import sys
from typing import Callable, Dict, Optional

from brain.logging_setup import get_logger

logger = get_logger(__name__)

UNATTENDED_ANSWER = "NO_ANSWER_FROM_UNATTENDED_RUN"

_master_prompt: Optional[Callable[[str], str]] = None
_master_notice: Optional[Callable[[str], None]] = None


def set_master_prompt(handler: Optional[Callable[[str], str]]) -> None:
    """Install (or clear) the handler used to ask the master a free-text question.

    ``app.py`` wires this to the dashboard so questions render inside the live
    TUI instead of blocking invisibly on a bare ``input()``; the default is a
    plain console prompt.
    """
    global _master_prompt
    _master_prompt = handler


def set_master_notice(handler: Optional[Callable[[str], None]]) -> None:
    """Install (or clear) the handler used to notify the master without waiting.

    The notice channel is fire-and-forget: a long browser hand-off uses it to
    tell the master what to do (and later what was found) while the tool keeps
    running. Passing ``None`` clears it, so an unmounted UI never receives
    notices from a stale handler.
    """
    global _master_notice
    _master_notice = handler


def notify_master(text: str) -> bool:
    """Show ``text`` to the master without blocking; True when it was delivered.

    Safe from any thread and never raises: without a handler (plain console
    runs, tests, or an unmounted UI) the notice is only logged, and a handler
    that blows up cannot break the calling tool.
    """
    message = (text or "").strip()
    if not message:
        return False
    if _master_notice is None:
        logger.debug("master notice (no handler): %s", message)
        return False
    try:
        _master_notice(message)
    except Exception as exc:  # noqa: BLE001 - a notice must never break a tool
        logger.debug("master notice failed: %s", exc)
        return False
    return True


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
    return {
        "ok": True,
        "question": question,
        "answer": answer,
        "hint": (
            "Act on this answer and keep working with your tools until the "
            "task is actually done — do not treat it as the final result. If "
            "it names a page or button (e.g. 'click on recharge my account on "
            "rail wire page'), navigate the browser with ego_lite_browse_use "
            "to that site and follow the instruction from the snapshot, then "
            "report what was accomplished."
        ),
    }