"""Tests for the three-layer memory system."""

from brain.memory import EpisodicMemory, Memory, NotesStore, WorkingMemory


def test_add_and_get(tmp_path):
    store = NotesStore(path=tmp_path / "memory.json")
    store.add_note("name", "Praveen")
    assert store.get("name") == "Praveen"
    assert store.has("name")
    assert not store.has("missing")


def test_round_trip_persistence(tmp_path):
    path = tmp_path / "mem" / "memory.json"
    first = NotesStore(path=path)
    first.add_note("preference", "strong coffee")
    second = NotesStore(path=path)
    assert second.get("preference") == "strong coffee"


def test_overwrite_same_key(tmp_path):
    store = NotesStore(path=tmp_path / "memory.json")
    store.add_note("k", "one")
    store.add_note("k", "two")
    assert store.get("k") == "two"


def test_blank_key_ignored(tmp_path):
    store = NotesStore(path=tmp_path / "memory.json")
    store.add_note("   ", "x")
    assert store.notes() == {}


def test_load_context_formats_long_term(tmp_path):
    store = Memory(dir_path=tmp_path)
    store.add_note("preference", "coffee")
    context = store.load_context()
    assert "- preference: coffee" in context


def test_load_context_empty_when_no_notes(tmp_path):
    store = Memory(dir_path=tmp_path)
    assert store.load_context() == ""


def test_corrupt_json_falls_back_to_empty(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = NotesStore(path=path)
    assert store.notes() == {}


def test_working_memory_is_ephemeral():
    working = WorkingMemory()
    working.set("scratch", "in progress")
    assert working.get("scratch") == "in progress"
    assert working.snapshot() == {"scratch": "in progress"}
    working.set("", "ignored")
    assert "ignored" not in working.snapshot().values()


def test_episodic_memory_round_trip(tmp_path):
    path = tmp_path / "sub" / "episodes.json"
    first = EpisodicMemory(path=path)
    first.add_episode("Lookup", "found the answer", details="visited example.com")
    second = EpisodicMemory(path=path)
    episodes = second.episodes()
    assert len(episodes) == 1
    entry = episodes[0]
    assert entry["title"] == "Lookup"
    assert entry["summary"] == "found the answer"
    assert entry["details"] == "visited example.com"
    assert "ts" in entry


def test_episodic_memory_orders_recent_first(tmp_path):
    episodic = EpisodicMemory(path=tmp_path / "ep.json")
    episodic.add_episode("one", "first")
    episodic.add_episode("two", "second")
    assert [e["title"] for e in episodic.episodes()] == ["two", "one"]


def test_episodic_memory_prunes_old(tmp_path):
    episodic = EpisodicMemory(path=tmp_path / "ep.json", max_episodes=2)
    for i in range(3):
        episodic.add_episode(f"task-{i}", "done")
    assert [e["title"] for e in episodic.episodes()] == ["task-2", "task-1"]


def test_memory_facade_combines_layers(tmp_path):
    memory = Memory(dir_path=tmp_path)
    memory.remember("preference", "strong coffee")
    memory.note_now("scratch", "browsing topic X")
    memory.add_episode("Research", "compiled the findings")
    context = memory.load_context()
    assert "strong coffee" in context
    assert "browsing topic X" in context
    assert "Research" in context


def test_memory_facade_recall_and_compat(tmp_path):
    memory = Memory(dir_path=tmp_path)
    memory.add_note("fact", "nvidia for inference")
    assert memory.recall("fact") == "nvidia for inference"
    assert memory.notes() == {"fact": "nvidia for inference"}


def test_memory_layers_persist_across_facades(tmp_path):
    first = Memory(dir_path=tmp_path)
    first.remember("preference", "short replies")
    first.add_episode("Review", "refined the prompt")
    second = Memory(dir_path=tmp_path)
    assert second.recall("preference") == "short replies"
    assert any(e["title"] == "Review" for e in second.episodes())