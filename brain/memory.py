"""Memory for pico.

- **Working memory** — an ephemeral, in-session scratchpad (never persisted).
- **Episodic memory** — a durable, time-stamped log of past events and tasks.
- **Long-term memory** — durable facts and preferences that persist over time.
- **Semantic memory** — stored sentences searchable by meaning (vector search).

The :class:`Memory` facade composes all the layers and renders a combined
context string that is injected into agent system prompts, so pico reviews what
it remembers at the start of every task and can capture new memories as it works.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from brain.logging_setup import get_logger
from brain.semantic import SemanticMemory

logger = get_logger(__name__)

DEFAULT_DIR = Path.home() / ".pico"
DEFAULT_MAX_EPISODES = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class WorkingMemory:
    """Ephemeral in-session scratchpad; nothing here is persisted."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: Dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        """Store a working-memory note for the current session."""
        if key.strip():
            with self._lock:
                self._data[key.strip()] = value

    def get(self, key: str) -> Optional[str]:
        """Return a working-memory note, or None."""
        with self._lock:
            return self._data.get(key)

    def snapshot(self) -> Dict[str, str]:
        """Return a copy of every current working memory note."""
        with self._lock:
            return dict(self._data)


class EpisodicMemory:
    """Durable, time-stamped log of past events and completed tasks."""

    def __init__(self, path: Optional[Path] = None, max_episodes: int = DEFAULT_MAX_EPISODES) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DIR / "episodes.json"
        self.max_episodes = max_episodes
        self._lock = threading.RLock()
        self._episodes: List[Dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    self._episodes = [e for e in raw if isinstance(e, dict)]
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"[bold yellow]Could not load episodes[/bold yellow] {self.path}: {exc}")
            self._episodes = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._episodes, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def add_episode(self, title: str, summary: str, details: str = "") -> None:
        """Record one episode and persist it; old entries are pruned."""
        title, summary = title.strip(), summary.strip()
        if not title:
            return
        with self._lock:
            self._episodes.append(
                {"ts": _now_iso(), "title": title, "summary": summary, "details": details.strip()}
            )
            if self.max_episodes and len(self._episodes) > self.max_episodes:
                self._episodes = self._episodes[-self.max_episodes:]
            self._save()
        logger.info(f"[bold green]Episode recorded[/bold green]: {title}")

    def episodes(self, limit: Optional[int] = None) -> List[Dict[str, str]]:
        """Return recent episodes, most recent first."""
        with self._lock:
            recent = list(reversed(self._episodes))
        return recent[:limit] if limit else recent


class NotesStore:
    """A small, safe JSON store of key-value notes (long-term facts/preferences)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DIR / "memory.json"
        self._lock = threading.RLock()
        self._notes: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._notes = {str(k): str(v) for k, v in raw.items() if isinstance(raw, dict)}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"[bold yellow]Could not load notes[/bold yellow] {self.path}: {exc}")
            self._notes = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._notes, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def add_note(self, key: str, value: str) -> None:
        """Store or overwrite a note and persist immediately."""
        key, value = key.strip(), value.strip()
        if not key:
            return
        with self._lock:
            self._notes[key] = value
            self._save()
        logger.info(f"[bold green]Note saved[/bold green]: {key}")

    def get(self, key: str) -> Optional[str]:
        """Return the stored value for a key, or None."""
        with self._lock:
            return self._notes.get(key)

    def has(self, key: str) -> bool:
        """Return True when the key exists."""
        with self._lock:
            return key in self._notes

    def notes(self) -> Dict[str, str]:
        """Return a copy of all stored notes."""
        with self._lock:
            return dict(self._notes)


class Memory:
    """Unified access to all memory layers."""

    def __init__(
        self,
        dir_path: Optional[Path] = None,
        working: Optional[WorkingMemory] = None,
        episodic: Optional[EpisodicMemory] = None,
        longterm: Optional[NotesStore] = None,
        semantic: Optional[SemanticMemory] = None,
    ) -> None:
        directory = Path(dir_path) if dir_path is not None else DEFAULT_DIR
        self.working = working or WorkingMemory()
        self.longterm = longterm or NotesStore(path=directory / "memory.json")
        self.episodic = episodic or EpisodicMemory(path=directory / "episodes.json")
        self.semantic = semantic or SemanticMemory(path=directory / "semantic.json")

    def remember(self, key: str, value: str) -> None:
        """Store a durable fact or preference in long-term memory."""
        self.longterm.add_note(key, value)

    def recall(self, key: str) -> Optional[str]:
        """Recall a durable fact from long-term memory."""
        return self.longterm.get(key)

    def add_note(self, key: str, value: str) -> None:
        """Alias for :meth:`remember` (backwards-compatible)."""
        self.remember(key, value)

    def note_now(self, key: str, value: str) -> None:
        """Store a quick note in working memory for the current session."""
        self.working.set(key, value)

    def add_episode(self, title: str, summary: str, details: str = "") -> None:
        """Record an episode in episodic memory."""
        self.episodic.add_episode(title, summary, details)

    def remember_semantic(
        self, text: str, metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, object]:
        """Store a sentence in semantic memory for future vector search."""
        return self.semantic.add(text, metadata)

    def search_semantic(
        self, query: str, top_k: int = 5, threshold: float = 0.0
    ) -> List[Dict[str, object]]:
        """Find the semantic-memory entries most similar to ``query`` by meaning."""
        return self.semantic.search(query, top_k=top_k, threshold=threshold)

    def semantic_items(self) -> List[Dict[str, object]]:
        """Return every stored semantic-memory entry (no vectors)."""
        return self.semantic.items()

    def notes(self) -> Dict[str, str]:
        """Return all long-term facts and preferences."""
        return self.longterm.notes()

    def episodes(self, limit: Optional[int] = None) -> List[Dict[str, str]]:
        """Return recent episodes, most recent first."""
        return self.episodic.episodes(limit=limit)

    def load_context(self) -> str:
        """Render all non-empty memory layers as a context block for a system prompt."""
        sections: List[str] = []
        working_items = self.working.snapshot()
        if working_items:
            lines = [f"- {key}: {value}" for key, value in sorted(working_items.items())]
            sections.append("## Working memory (this session)\n" + "\n".join(lines))
        longterm = self.longterm.notes()
        if longterm:
            lines = [f"- {key}: {value}" for key, value in sorted(longterm.items())]
            sections.append("## What I know about the master (long-term)\n" + "\n".join(lines))
        recent_episodes = self.episodic.episodes(limit=5)
        if recent_episodes:
            lines = [
                f"- ({e.get('ts', '')}) {e.get('title', '')}: {e.get('summary', '')}"
                for e in recent_episodes
            ]
            sections.append("## Recent episodes\n" + "\n".join(lines))
        semantic_items = self.semantic.items()[-5:]
        if semantic_items:
            metadata = semantic_items[-1].get("metadata") or {}
            source = metadata.get("source", "note")
            lines = [f"- {item['text']}" for item in semantic_items]
            sections.append(f"## Recent semantic memories (last from {source})\n" + "\n".join(lines))
        return "\n\n".join(sections)