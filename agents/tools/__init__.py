"""Tool registry and dispatch.

Each tool is a pure function returning a plain dict. Agents talk to tools only
through :func:`dispatch`, never by importing modules directly.
"""

from typing import Any, Callable, Dict

from . import bash, ego_lite_browse_use, file_read, file_write, gmail, memory, skill_read

REGISTRY: Dict[str, Dict[str, Any]] = {
    "bash": {
        "callable": bash.run_command,
        "description": "Run a single safe shell command with a timeout and return its output.",
        "parameters": {"command": str, "timeout": int},
    },
    "file_read": {
        "callable": file_read.read_file,
        "description": "Read a text file inside the project root and return its content.",
        "parameters": {"path": str, "limit": int},
    },
    "file_write": {
        "callable": file_write.write_file,
        "description": "Write text to a file inside the project root. Never deletes anything.",
        "parameters": {"path": str, "content": str},
    },
    "skill_read": {
        "callable": skill_read.read_skill,
        "description": "Read a skill definition (YAML) from the skills directory.",
        "parameters": {"skill_name": str},
    },
    "list_skills": {
        "callable": skill_read.list_skills,
        "description": "List available skill names.",
        "parameters": {},
    },
    "ego_lite_browse_use": {
        "callable": ego_lite_browse_use.browse,
        "description": "Visit a website via the ego-lite browser skill and return its page snapshot.",
        "parameters": {"url": str, "action": str},
    },
    "memory_remember": {
        "callable": memory.memory_remember,
        "description": "Store a durable fact or preference in long-term memory.",
        "parameters": {"key": str, "value": str},
    },
    "memory_recall": {
        "callable": memory.memory_recall,
        "description": "Recall a durable fact from long-term memory.",
        "parameters": {"key": str},
    },
    "memory_note": {
        "callable": memory.memory_note,
        "description": "Keep a quick note in working memory for the current session.",
        "parameters": {"key": str, "value": str},
    },
    "memory_episode": {
        "callable": memory.memory_episode,
        "description": "Record a completed event or task in episodic memory.",
        "parameters": {"title": str, "summary": str, "details": str},
    },
    "semantic_remember": {
        "callable": memory.semantic_remember,
        "description": "Store a sentence in semantic memory for later meaning-based recall.",
        "parameters": {"text": str, "metadata": str},
    },
    "semantic_search": {
        "callable": memory.semantic_search,
        "description": "Find past memories most similar to the query, ranked by meaning.",
        "parameters": {"query": str, "top_k": int},
    },
    "gmail_latest": {
        "callable": gmail.gmail_latest,
        "description": "Read recent emails from the master's Gmail inbox (read-only, IMAP).",
        "parameters": {"limit": int, "folder": str, "unread_only": bool},
    },
    "gmail_search": {
        "callable": gmail.gmail_search,
        "description": "Search the master's Gmail inbox for emails matching an IMAP query.",
        "parameters": {"query": str, "limit": int, "folder": str},
    },
}

TOOL_NAMES = list(REGISTRY.keys())


def dispatch(name: str, **kwargs) -> Dict[str, Any]:
    """Look up a tool by name, call it with kwargs, and return a dict result."""
    entry = REGISTRY.get(name)
    if entry is None:
        raise KeyError(f"unknown tool: {name!r} (available: {TOOL_NAMES})")
    return entry["callable"](**kwargs)