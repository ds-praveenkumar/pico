"""Tool registry and dispatch.

Each tool is a pure function returning a plain dict. Agents talk to tools only
through :func:`dispatch`, never by importing modules directly.
"""

from typing import Any, Callable, Dict

from . import ask, bash, current_date, ego_lite_browse_use, file_read, file_write, gmail, latest_news, memory, skill_read

REGISTRY: Dict[str, Dict[str, Any]] = {
    "ask_master": {
        "callable": ask.ask_master,
        "description": "Ask the master (Praveen) a free-text question and return their answer. Use it when a page needs something only the master has or can do — solving a CAPTCHA, providing login/OTP details, uploading a file, or any form field pico may not guess. Never invent such details.",
        "parameters": {"question": str},
    },
    "bash": {
        "callable": bash.run_command,
        "description": "Run a single safe shell command with a timeout and return its output.",
        "parameters": {"command": str, "timeout": int},
    },
    "current_date": {
        "callable": current_date.current_date,
        "description": "Get today's date and the current time from the system clock. Use this for any 'what date/day/time is it' question.",
        "parameters": {},
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
        "description": "Drive the ego-lite browser on the current page. action='load' opens 'url' and snapshots it. action='click' clicks 'selector'. action='fill' types 'query' into 'selector'. action='select' picks the 'query' option in the dropdown 'selector'. action='claim' reclaims a tab handed off to the master (after they say they are done) and snapshots it. action='release' close the browser task space once the goal is done so the browser claim is freed for the next task. The page stays open between calls. For actions use the stable locators from the snapshot (loc=css:..., CSS, or text); snapshot refs like @5 only work in the very same call. Provide 'url' with the first load and keep it in later calls. When the page shows a CAPTCHA, login/OTP, or a required form the result includes 'need_human': stop guessing and ask the master through 'ask_master' — the browser window is open and handed to them until pico resumes this same page. When the result includes 'paused', the tab is parked under the master's control: ask through 'ask_master', then resume with action='claim'.",
        "parameters": {"url": str, "action": str, "selector": str, "query": str},
        "optional": ["url", "selector", "query"],
    },
    "latest_news": {
        "callable": latest_news.latest_news,
        "description": "Fetch today's top news headlines from curated public RSS feeds (Google News, BBC World, The Guardian). Use this for any 'today's news' or 'latest headlines' request.",
        "parameters": {"limit": int},
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