"""Tool registry and dispatch.

Each tool is a pure function returning a plain dict. Agents talk to tools only
through :func:`dispatch`, never by importing modules directly.
"""

from typing import Any, Callable, Dict

from brain.config import master_name

from . import ask, bash, calendar_oauth, current_date, ego_lite_browse_use, file_read, file_write, gmail_oauth, latest_news, memory, sheets_oauth, skill_read, url_fetch, voice, weather


def _ask_master_description() -> str:
    """Build the ask_master description with the configured master's name."""
    return (
        f"Ask the master ({master_name()}) a free-text question and return their answer. "
        "Use it when a page needs something only the master has or can do — solving a "
        "CAPTCHA, providing login/OTP details, uploading a file, or any form field pico "
        "may not guess. Never invent such details."
    )


REGISTRY: Dict[str, Dict[str, Any]] = {
    "ask_master": {
        "callable": ask.ask_master,
        "description": _ask_master_description(),
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
        "description": "Drive the ego-lite browser on the current page. action='load' opens 'url' and snapshots it. action='click' clicks 'selector'. action='fill' types 'query' into 'selector'. action='select' picks the 'query' option in the dropdown 'selector'. action='press' sends the 'query' key (default Enter) to submit a form. action='claim' reclaims a tab handed off to the master (after they say they are done) and snapshots it. action='release' close the browser task space once the goal is done so the browser claim is freed for the next task. The page stays open between calls. For actions use the stable locators from the snapshot (loc=css:..., CSS, or text); snapshot refs like @5 only work in the very same call. Provide 'url' with the first load and keep it in later calls. When the page shows a CAPTCHA, login/OTP, or a required form the result includes 'need_human': stop guessing and ask the master through 'ask_master' so he types the CAPTCHA and submits the form in the open browser window himself — never fill a CAPTCHA answer. When he confirms he submitted the form, resume with action='claim' and read the result page. When the result includes 'paused', the tab is parked under the master's control: ask through 'ask_master', then resume with action='claim'.",
        "parameters": {"url": str, "action": str, "selector": str, "query": str},
        "optional": ["url", "selector", "query"],
    },
    "latest_news": {
        "callable": latest_news.latest_news,
        "description": "Fetch today's top news headlines from curated public RSS feeds (Google News, BBC World, The Guardian), or news about a 'topic' via Google News search. Use this for any 'today's news' or 'latest headlines' request.",
        "parameters": {"limit": int, "topic": str},
        "optional": ["topic"],
    },
    "weather": {
        "callable": weather.weather,
        "description": "Get the current weather and a short forecast for a location (Open-Meteo, no API key). Use for any 'what's the weather' or 'forecast' request; pass a city or place name in 'location'.",
        "parameters": {"location": str, "days": int},
        "optional": ["days"],
    },
    "url_fetch": {
        "callable": url_fetch.url_fetch,
        "description": "Fetch one public http(s) page and return its text, title, and metadata (read-only, no JavaScript). Use it to read plain pages and docs; use the ego-lite browser for pages that need interaction or JavaScript.",
        "parameters": {"url": str, "max_bytes": int, "timeout": int},
        "optional": ["max_bytes", "timeout"],
    },
    "voice_speak": {
        "callable": voice.voice_speak,
        "description": "Speak a short text out loud with the system voice (macOS 'say'). Use when the master asks pico to speak its answer aloud.",
        "parameters": {"text": str, "rate": int},
        "optional": ["rate"],
    },
    "calendar_list": {
        "callable": calendar_oauth.calendar_list,
        "description": "List upcoming events from the master's primary Google Calendar via OAuth (read-only). 'max_results' caps the count; 'days_ahead' looks that many days forward.",
        "parameters": {"max_results": int, "days_ahead": int},
        "optional": ["max_results", "days_ahead"],
    },
    "calendar_create": {
        "callable": calendar_oauth.calendar_create,
        "description": "Create an event on the master's primary Google Calendar via OAuth. 'start' and 'end' are ISO 8601 timestamps. Only acts after the master approves.",
        "parameters": {"summary": str, "start": str, "end": str, "description": str},
        "optional": ["description"],
    },
    "calendar_respond": {
        "callable": calendar_oauth.calendar_respond,
        "description": "Set the master's attendance on a Google Calendar event to accepted/declined/tentative via OAuth. Only acts after the master approves.",
        "parameters": {"event_id": str, "response": str},
    },
    "sheets_read": {
        "callable": sheets_oauth.sheets_read,
        "description": "Read a range from the master's Google Sheets spreadsheet via OAuth (read-only). 'spreadsheet_id' accepts a URL or raw ID; 'range_name' like 'Transactions!A1:D200'.",
        "parameters": {"spreadsheet_id": str, "range_name": str},
    },
    "sheets_append": {
        "callable": sheets_oauth.sheets_append,
        "description": "Append row(s) to the master's Google Sheets spreadsheet via OAuth. 'values' is one row (['2026-09-13', 'Food', 12.5, 'lunch']) or several. Only acts after the master approves.",
        "parameters": {"spreadsheet_id": str, "range_name": str, "values": list},
    },
    "sheets_update": {
        "callable": sheets_oauth.sheets_update,
        "description": "Overwrite a range in the master's Google Sheets spreadsheet via OAuth. 'values' is one row or several. Only acts after the master approves.",
        "parameters": {"spreadsheet_id": str, "range_name": str, "values": list},
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
    "gmail_list": {
        "callable": gmail_oauth.list_emails,
        "description": "List the master's Gmail inbox messages via OAuth (read-only). OAuth is already configured for the master — call this directly; never ask the master for credentials. Optional 'query' filters with Gmail search syntax (e.g. 'from:x@y.com', 'subject:meeting'); 'unread_only' limits to unseen; 'max_results' caps the count.",
        "parameters": {"query": str, "unread_only": bool, "max_results": int},
        "optional": ["query", "unread_only", "max_results"],
    },
    "gmail_search": {
        "callable": gmail_oauth.search_emails,
        "description": "Search the master's Gmail for messages matching a Gmail search query (read-only, OAuth, already configured — call directly; never ask the master for credentials). Returns matching subjects, senders, and dates.",
        "parameters": {"query": str, "max_results": int},
        "optional": ["max_results"],
    },
    "gmail_read": {
        "callable": gmail_oauth.read_email,
        "description": "Read one email's full content from the master's Gmail by message ID (read-only, OAuth, already configured — call directly; never ask the master for credentials).",
        "parameters": {"message_id": str},
    },
    "gmail_send": {
        "callable": gmail_oauth.send_email,
        "description": "Send an email from the master's Gmail account (OAuth). Only acts after the master approves.",
        "parameters": {"to": str, "subject": str, "body": str, "reply_to": str},
        "optional": ["reply_to"],
    },
    "gmail_mark": {
        "callable": gmail_oauth.mark_email,
        "description": "Mark a Gmail email as read, unread, or flagged (Starred) via OAuth. Only acts after the master approves.",
        "parameters": {"message_id": str, "status": str},
        "optional": ["status"],
    },
}

TOOL_NAMES = list(REGISTRY.keys())


def dispatch(name: str, **kwargs) -> Dict[str, Any]:
    """Look up a tool by name, call it with kwargs, and return a dict result."""
    entry = REGISTRY.get(name)
    if entry is None:
        raise KeyError(f"unknown tool: {name!r} (available: {TOOL_NAMES})")
    return entry["callable"](**kwargs)