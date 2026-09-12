"""Safe shell execution tool.

Runs commands through the sandbox (scrubbed environment + resource limits,
see :mod:`agents.tools.sandbox`) behind a command allowlist and a
destructive-command blocklist. Never executes anything the allowlist rejects.
"""

import os
import shlex
from typing import Dict

from agents.tools import sandbox
from brain.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 30

COMMAND_ALLOWLIST: set[str] = {
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "pwd",
    "echo",
    "python",
    "python3",
    "git",
    "whoami",
    "uname",
    "date",
    "wc",
}

DESTRUCTIVE_COMMANDS: set[str] = {
    "rm",
    "unlink",
    "mv",
    "dd",
    "shred",
    "mkfs",
    "fsck",
    "sudo",
    "chmod",
    "chown",
    "kill",
    "pkill",
    "reboot",
    "shutdown",
}


def is_allowed_command(command: str) -> bool:
    """Return True when the command's first token is allowed and not destructive."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    first = os.path.basename(parts[0])
    return first in COMMAND_ALLOWLIST and first not in DESTRUCTIVE_COMMANDS


def run_command(command: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, object]:
    """Run a single command and return its output as a summary dict."""
    if not is_allowed_command(command):
        logger.warning(f"[bold red]Blocked command[/bold red]: {command!r}")
        return {
            "ok": False,
            "command": command,
            "error": "command is not in the allowlist",
            "output": "",
        }
    try:
        argv = shlex.split(command)
        result = sandbox.run(argv, timeout=timeout, cwd=os.getcwd())
        result["command"] = command
        return result
    except Exception as exc:  # noqa: BLE001 - never let the shell tool break the agent
        logger.error(f"[bold red]Command failed[/bold red]: {command} -> {exc}")
        return {"ok": False, "command": command, "error": str(exc), "output": ""}