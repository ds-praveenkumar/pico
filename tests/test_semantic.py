"""Tests for semantic memory and vector search."""

import math
from pathlib import Path

from brain.memory import Memory
from brain.semantic import (
    HashingEmbedding,
    SemanticMemory,
    cosine_similarity,
)


def test_embedding_is_deterministic():
    emb = HashingEmbedding()
    assert emb.embed("grocery shopping") == emb.embed("grocery shopping")
    vector = emb.embed("hello world")
    assert len(vector) == 256
    norm = math.sqrt(sum(x * x for x in vector))
    assert abs(norm - 1.0) < 1e-9


def test_cosine_similarity():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert abs(cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9
    assert cosine_similarity([], [1.0]) == 0.0


def test_semantic_round_trip_persistence(tmp_path):
    path = tmp_path / "nested" / "semantic.json"
    first = SemanticMemory(path=path)
    first.add("Praveen prefers strong coffee", {"source": "test"})
    second = SemanticMemory(path=path)
    assert second.count() == 1
    assert second.items()[0]["text"] == "Praveen prefers strong coffee"


def test_semantic_search_ranks_by_meaning(tmp_path):
    store = SemanticMemory(path=tmp_path / "semantic.json")
    store.add("grocery list: milk, eggs, bread", {"source": "test"})
    store.add("the moon landing happened in 1969", {"source": "test"})
    store.add("remember to buy groceries this weekend", {"source": "test"})
    results = store.search("I need to buy groceries soon", top_k=2)
    assert results[0]["text"].startswith("remember to buy groceries")
    assert results[1]["text"].startswith("grocery list")
    assert results[0]["score"] > results[1]["score"]


def test_semantic_search_respects_threshold_and_top_k(tmp_path):
    store = SemanticMemory(path=tmp_path / "semantic.json")
    store.add("cooking pasta with tomato sauce", {"source": "test"})
    store.add("fixing the bicycle tire", {"source": "test"})
    high = store.search("spaghetti with tomato sauce", top_k=5, threshold=0.2)
    best = high[0]
    assert best["text"].startswith("cooking pasta")
    limited = store.search("spaghetti with tomato sauce", top_k=1)
    assert len(limited) == 1
    assert store.search("spaghetti with tomato sauce", top_k=0) == []
    assert store.search("zzz qqq vvv", top_k=5, threshold=0.9) == []


def test_semantic_search_finds_related_topic(tmp_path):
    store = SemanticMemory(path=tmp_path / "semantic.json")
    store.add("schedule a dentist appointment", {"source": "test"})
    store.add("move the server to the new datacenter", {"source": "test"})
    results = store.search("I need to see my doctor", top_k=1)
    assert results[0]["text"].startswith("schedule a dentist")


def test_empty_text_refused(tmp_path):
    store = SemanticMemory(path=tmp_path / "semantic.json")
    assert store.add("   ")["ok"] is False
    assert store.count() == 0


def test_corrupt_json_falls_back_to_empty(tmp_path):
    path = tmp_path / "semantic.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = SemanticMemory(path=path)
    assert store.count() == 0


def test_memory_facade_semantic_layer(tmp_path):
    memory = Memory(dir_path=tmp_path)
    memory.remember_semantic("Praveen loves strong coffee", {"source": "test"})
    matches = memory.search_semantic("his favorite coffee drink")
    assert matches[0]["text"] == "Praveen loves strong coffee"
    reloaded = Memory(dir_path=tmp_path)
    assert reloaded.semantic_items()[0]["text"] == "Praveen loves strong coffee"


def test_context_includes_recent_semantic(tmp_path):
    memory = Memory(dir_path=tmp_path)
    memory.remember_semantic("research notes about goal planning", {"source": "tool"})
    context = memory.load_context()
    assert "## Recent semantic memories" in context
    assert "research notes about goal planning" in context