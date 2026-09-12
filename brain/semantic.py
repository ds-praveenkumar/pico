"""Semantic memory with vector search.

``SemanticMemory`` stores sentences as dense vectors and answers similarity
queries (cosine). The default ``HashingEmbedding`` is a local, deterministic,
dependency-free feature-hashing embedding (character trigrams + word tokens), so
semantic search works offline and never sends text to a network. The embedding
is pluggable: swap in any object with ``embed(text) -> list[float]``.
"""

import hashlib
import json
import math
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_DIR = Path.home() / ".pico"
DIM = 256


class HashingEmbedding:
    """Deterministic local embedding via feature hashing, L2-normalized."""

    name = "hash256"

    def embed(self, text: str) -> List[float]:
        """Return a normalized dense vector for ``text``."""
        vector = [0.0] * DIM
        for token in self._tokens(text):
            digest = hashlib.sha1(token.encode("utf-8")).digest()
            index_a = int.from_bytes(digest[0:2], "big") % DIM
            index_b = int.from_bytes(digest[2:4], "big") % DIM
            vector[index_a] += 1.0
            vector[index_b] -= 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0:
            return vector
        return [x / norm for x in vector]

    @staticmethod
    def _tokens(text: str) -> List[str]:
        """Yield word tokens and character trigrams over the cleaned text."""
        lowered = text.lower()
        words = re.findall(r"[a-z0-9]+", lowered)
        cleaned = re.sub(r"[^a-z0-9]", "", lowered)
        tokens = list(words)
        tokens.extend(cleaned[i : i + 3] for i in range(max(0, len(cleaned) - 2)))
        return tokens


def cosine_similarity(vector_a: List[float], vector_b: List[float]) -> float:
    """Return the cosine similarity between two equal-length vectors."""
    if not vector_a or len(vector_a) != len(vector_b):
        return 0.0
    return sum(a * b for a, b in zip(vector_a, vector_b))


class SemanticMemory:
    """A persisted store of text entries searchable by meaning."""

    def __init__(self, path: Optional[Path] = None, embedding: Optional[Any] = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DIR / "semantic.json"
        self.embedding = embedding or HashingEmbedding()
        self._lock = threading.RLock()
        self._entries: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    self._entries = [
                        e
                        for e in raw
                        if isinstance(e, dict) and isinstance(e.get("vector"), list)
                    ]
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"[bold yellow]Could not load semantic memory[/bold yellow] {self.path}: {exc}")
            self._entries = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._entries, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, text: str, metadata: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Embed, store, and persist one memory entry; returns the stored entry."""
        text = text.strip()
        if not text:
            return {"ok": False, "error": "cannot remember empty text"}
        vector = self.embedding.embed(text)
        entry = {
            "text": text,
            "vector": vector,
            "metadata": metadata or {},
        }
        with self._lock:
            self._entries.append(entry)
            self._save()
        logger.info(f"[bold green]Semantic memory stored[/bold green]: {text[:60]}")
        return {"ok": True, "text": text}

    def search(
        self, query: str, top_k: int = 5, threshold: float = 0.0
    ) -> List[Dict[str, Any]]:
        """Return the entries most similar to ``query`` by cosine, best first."""
        if top_k <= 0:
            return []
        qvector = self.embedding.embed(query)
        with self._lock:
            scored = []
            for entry in self._entries:
                score = cosine_similarity(qvector, entry["vector"])
                if score > threshold:
                    scored.append(
                        {
                            "text": entry["text"],
                            "score": round(score, 4),
                            "metadata": entry.get("metadata") or {},
                        }
                    )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]

    def items(self) -> List[Dict[str, Any]]:
        """Return every stored entry (no vectors)."""
        with self._lock:
            return [
                {"text": e["text"], "metadata": e.get("metadata") or {}} for e in self._entries
            ]

    def count(self) -> int:
        """Return the number of stored entries."""
        with self._lock:
            return len(self._entries)