"""Risk-aware tool approval policy shared by pico interfaces."""

import os
import shlex
from typing import Any, Callable, Dict

from agents.tools import bash as bash_tool

_AUTO_TOOLS = {
    "current_date",
    "file_read",
    "file_write",
    "skill_read",
    "list_skills",
    "memory_recall",
    "memory_note",
    "memory_episode",
    "memory_remember",
    "semantic_remember",
    "semantic_search",
    "gmail_list",
    "gmail_search",
    "gmail_read",
    "ego_lite_browse_use",
    "latest_news",
    "ask_master",
    "weather",
    "url_fetch",
    "voice_speak",
    "calendar_list",
    "sheets_read",
}

_BASH_AUTO_FIRST = {
    "awk",
    "basename",
    "cat",
    "date",
    "df",
    "dirname",
    "du",
    "echo",
    "env",
    "file",
    "find",
    "grep",
    "head",
    "hostname",
    "jq",
    "ls",
    "printf",
    "pwd",
    "readlink",
    "realpath",
    "rg",
    "tail",
    "true",
    "false",
    "uname",
    "wc",
    "which",
    "whoami",
}

_BASH_AUTO_ARGS: Dict[str, Callable[[list[str]], bool]] = {
    "python": lambda parts: len(parts) > 1 and parts[1] in {"-V", "--version"},
    "python3": lambda parts: len(parts) > 1 and parts[1] in {"-V", "--version"},
    "pip": lambda parts: len(parts) > 1 and parts[1] in {"list", "show", "freeze"},
    "pip3": lambda parts: len(parts) > 1 and parts[1] in {"list", "show", "freeze"},
}

_GIT_AUTO_SUBCOMMANDS = {"blame", "branch", "diff", "log", "ls-files", "remote", "rev-parse", "show", "status", "tag"}


def bash_needs_approval(command: str) -> bool:
    """Return whether a shell command requires master approval."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return True
    if not parts or ">" in parts:
        return True
    first = os.path.basename(parts[0])
    if first in bash_tool.DESTRUCTIVE_COMMANDS:
        return True
    if first in _BASH_AUTO_FIRST:
        return False
    if first in _BASH_AUTO_ARGS:
        return not _BASH_AUTO_ARGS[first](parts)
    if first == "git":
        return len(parts) < 2 or parts[1] not in _GIT_AUTO_SUBCOMMANDS
    return True


def auto_approve(name: str, args: Dict[str, Any]) -> bool:
    """Return whether a tool call is safe enough to run without approval."""
    if os.environ.get("PICO_AUTO_APPROVE") == "1":
        return True
    if name in _AUTO_TOOLS:
        return True
    if name == "bash":
        return not bash_needs_approval(str(args.get("command", "")))
    return False
