"""Memory tools for agents.

Agents capture and recall memories while working by calling these tools.
The current :class:`brain.memory.Memory` instance is bound per task by the
agent loop (:func:`bind_memory` / :func:`unbind_memory`); without a binding the
tools refuse rather than losing data silently.
"""

from typing import Any, Dict, Optional

from brain.logging_setup import get_logger

logger = get_logger(__name__)

_CURRENT: Optional[Any] = None


def bind_memory(memory: Any) -> None:
    """Bind the active Memory instance for the current task."""
    global _CURRENT
    _CURRENT = memory


def unbind_memory() -> None:
    """Clear the active Memory binding."""
    global _CURRENT
    _CURRENT = None


def _memory_or_error() -> tuple:
    """Return (memory, error-dict); at most one is set."""
    if _CURRENT is None:
        return None, {"ok": False, "error": "memory is not available for this task"}
    return _CURRENT, None


def memory_remember(key: str, value: str) -> Dict[str, Any]:
    """Store a durable fact or preference in long-term memory."""
    memory, error = _memory_or_error()
    if error is not None:
        return error
    memory.remember(key, value)
    logger.info(f"[bold green]Long-term memory[/bold green]: {key}")
    return {"ok": True, "key": key, "stored": "long-term"}


def memory_recall(key: str) -> Dict[str, Any]:
    """Recall a durable fact from long-term memory."""
    memory, error = _memory_or_error()
    if error is not None:
        return error
    return {"ok": True, "key": key, "value": memory.recall(key) or ""}


def memory_note(key: str, value: str) -> Dict[str, Any]:
    """Keep a quick note in working memory for the current session."""
    memory, error = _memory_or_error()
    if error is not None:
        return error
    memory.note_now(key, value)
    return {"ok": True, "key": key, "stored": "working-memory"}


def memory_episode(title: str, summary: str, details: str = "") -> Dict[str, Any]:
    """Record a completed event or task in episodic memory."""
    memory, error = _memory_or_error()
    if error is not None:
        return error
    memory.add_episode(title, summary, details)
    logger.info(f"[bold green]Episode recorded[/bold green]: {title}")
    return {"ok": True, "title": title, "stored": "episodic"}


def semantic_remember(text: str, metadata: str = "") -> Dict[str, Any]:
    """Store a sentence in semantic memory for later meaning-based recall."""
    memory, error = _memory_or_error()
    if error is not None:
        return error
    result = memory.remember_semantic(text, {"source": "tool", "note": metadata.strip()})
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "could not store")}
    return {"ok": True, "text": text, "stored": "semantic"}


def semantic_search(query: str, top_k: int = 5) -> Dict[str, Any]:
    """Find past memories most similar to the query, ranked by meaning."""
    memory, error = _memory_or_error()
    if error is not None:
        return error
    results = memory.search_semantic(query, top_k=top_k)
    if not results:
        return {"ok": True, "matches": []}
    return {
        "ok": True,
        "query": query,
        "matches": [
            {"text": r["text"], "score": r["score"], "metadata": r.get("metadata") or {}}
            for r in results
        ],
    }