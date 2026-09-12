"""Tests for the safe tools layer."""

import subprocess
import tempfile
from pathlib import Path

import pytest

from agents.tools import REGISTRY, TOOL_NAMES, dispatch
from agents.tools import ego_lite_browse_use, gmail as gmail_tools, memory as memory_tools
from agents.tools import sandbox
from agents.tools.bash import is_allowed_command, run_command
from agents.tools.ego_lite_browse_use import (
    browse,
    extract_metadata,
    extract_nav_error,
    extract_snapshot,
)
from agents.tools.file_read import read_file
from agents.tools.file_write import write_file
from agents.tools.skill_read import list_skills, read_skill
from brain.memory import Memory


def test_dispatch_unknown_tool_raises():
    with pytest.raises(KeyError):
        dispatch("does_not_exist")


def test_registry_has_expected_tools():
    assert "bash" in TOOL_NAMES
    assert "file_read" in TOOL_NAMES
    assert "file_write" in TOOL_NAMES
    assert "skill_read" in TOOL_NAMES
    assert "ego_lite_browse_use" in TOOL_NAMES
    for name in TOOL_NAMES:
        assert callable(REGISTRY[name]["callable"])


def test_bash_allows_safe_commands():
    assert is_allowed_command("echo hello")
    assert is_allowed_command("ls")


def test_bash_blocks_destructive_commands():
    assert not is_allowed_command("rm -rf /")
    assert not is_allowed_command("unlink file")
    assert not is_allowed_command("sudo mv a b")
    assert not is_allowed_command("")


def test_bash_run_reports_output():
    result = run_command("echo hello")
    assert result["ok"] is True
    assert "hello" in result["output"]


def test_bash_runs_in_sandbox():
    result = run_command("echo hi")
    assert result.get("sandboxed") is True


def test_bash_run_refuses_blocked():
    result = run_command("rm -rf /")
    assert result["ok"] is False
    assert "allowlist" in result["error"]


def test_file_read_within_root(project_root):
    result = read_file("README.md", root=project_root)
    assert result["ok"] is True
    assert "# pico" in result["content"]


def test_file_read_missing_file(project_root):
    result = read_file("no-such-file.txt", root=project_root)
    assert result["ok"] is False


def test_file_read_escapes_root():
    result = read_file("/etc/passwd")
    assert result["ok"] is False


def test_file_write_within_root_and_escape(tmp_path, project_root):
    ok = write_file("notes/x.txt", "hi", root=tmp_path)
    assert ok["ok"] is True
    assert (tmp_path / "notes" / "x.txt").read_text() == "hi"

    escaped = write_file("/etc/evil.txt", "x", root=tmp_path)
    assert escaped["ok"] is False


def test_skill_read_lists_and_loads():
    assert "ego-lite-browser-use" in list_skills()
    result = read_skill("ego-lite-browser-use")
    assert result["ok"] is True
    assert "ego-lite-browser-use" in result["content"]


def test_skill_read_has_new_skills():
    for name in ("gym-routine", "expense-planner", "gmail"):
        assert name in list_skills()
        result = read_skill(name)
        assert result["ok"] is True
        assert "## Purpose" in result["content"]


def test_skill_read_missing():
    result = read_skill("missing-skill")
    assert result["ok"] is False


def _fake_proc(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_browse_refuses_bad_urls(monkeypatch):
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)
    assert browse("not a url")["ok"] is False
    assert browse("file:///etc/passwd")["ok"] is False


def test_browse_requires_cli(monkeypatch):
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: False)
    result = browse("https://example.com")
    assert result["ok"] is False
    assert "not installed" in result["error"]


def test_browse_returns_snapshot(monkeypatch):
    stdout = (
        "TITLE: Example Domain\n"
        "FINAL_URL: https://example.com/\n"
        "---SNAPSHOT-BEGIN---\n"
        "page snapshot text\n"
        "---SNAPSHOT-END---\n"
    )
    fake = _fake_proc(stdout)
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)
    monkeypatch.setattr(ego_lite_browse_use, "_run_browser_script", lambda script, timeout: fake)
    result = browse("https://example.com")
    assert result["ok"] is True
    assert result["title"] == "Example Domain"
    assert result["final_url"] == "https://example.com/"
    assert "page snapshot text" in result["result"]


def test_browse_reports_nav_error(monkeypatch):
    stdout = "NAV_ERROR: timeout reaching host\n---SNAPSHOT-BEGIN---\n\n---SNAPSHOT-END---\n"
    fake = _fake_proc(stdout)
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)
    monkeypatch.setattr(ego_lite_browse_use, "_run_browser_script", lambda script, timeout: fake)
    result = browse("https://example.com")
    assert result["nav_error"] == "timeout reaching host"


def test_browse_handles_timeout(monkeypatch):
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)

    def boom(script, timeout):
        raise subprocess.TimeoutExpired(script, timeout)

    monkeypatch.setattr(ego_lite_browse_use, "_run_browser_script", boom)
    result = browse("https://example.com")
    assert result["ok"] is False
    assert "timeout" in result["error"]


def test_browse_reads_output_from_stderr(monkeypatch):
    stderr = (
        "TITLE: Example Domain\n"
        "---SNAPSHOT-BEGIN---\n"
        "snapshot on stderr\n"
        "---SNAPSHOT-END---\n"
    )
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=stderr)
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)
    monkeypatch.setattr(ego_lite_browse_use, "_run_browser_script", lambda script, timeout: fake)
    result = browse("https://example.com")
    assert result["ok"] is True
    assert result["title"] == "Example Domain"
    assert "snapshot on stderr" in result["result"]


def test_extract_snapshot_truncates():
    stdout = "---SNAPSHOT-BEGIN---\n" + "a" * 100 + "\n---SNAPSHOT-END---\n"
    snapshot = extract_snapshot(stdout, max_chars=20)
    assert "[truncated by pico]" in snapshot


def test_extract_helpers():
    stdout = "TITLE: T\nFINAL_URL: https://x/\nNAV_ERROR: boo\n"
    assert extract_metadata(stdout) == ("T", "https://x/")
    assert extract_nav_error(stdout) == "boo"


def test_registry_has_memory_tools():
    assert {"memory_remember", "memory_recall", "memory_note", "memory_episode"}.issubset(TOOL_NAMES)
    assert {"semantic_remember", "semantic_search"}.issubset(TOOL_NAMES)


def test_memory_tools_refuse_without_binding():
    memory_tools.unbind_memory()
    assert memory_tools.memory_remember("a", "b")["ok"] is False
    assert memory_tools.memory_note("a", "b")["ok"] is False
    assert memory_tools.memory_episode("t", "s")["ok"] is False
    assert memory_tools.semantic_remember("a note", "")["ok"] is False
    assert memory_tools.semantic_search("anything")["ok"] is False


def test_memory_tools_with_binding(tmp_path):
    memory = Memory(dir_path=tmp_path)
    memory_tools.bind_memory(memory)
    try:
        memory_tools.memory_note("scratch", "abc")
        memory_tools.memory_remember("fact", "xyz")
        memory_tools.memory_episode("Done", "finished", "details")
        memory_tools.semantic_remember("the release went smoothly", "deploy notes")
        assert memory.working.get("scratch") == "abc"
        assert memory.recall("fact") == "xyz"
        assert memory.episodes()[0]["title"] == "Done"
        assert memory_tools.memory_recall("fact")["value"] == "xyz"
        matches = memory_tools.semantic_search("how did the release go?")
        assert matches["ok"] is True
        assert matches["matches"][0]["text"] == "the release went smoothly"
        assert matches["matches"][0]["metadata"]["note"] == "deploy notes"
    finally:
        memory_tools.unbind_memory()
    assert memory_tools.memory_remember("a", "b")["ok"] is False


def test_sandbox_env_scrubs_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "sk-live-secret")
    monkeypatch.setenv("GMAIL_IMAP_PASSWORD", "app-password")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = sandbox.sandboxed_env(str(tmp_path))
    assert "sk-live-secret" not in str(env)
    assert "app-password" not in str(env)
    assert "/usr/bin" in env["PATH"]
    assert env["HOME"] == str(tmp_path)


def test_sandbox_runs_command(tmp_path):
    result = sandbox.run(["echo", "sandboxed"], cwd=str(tmp_path))
    assert result["ok"] is True
    assert "sandboxed" in result["output"]
    assert result["sandboxed"] is True


def test_sandbox_reports_timeout(monkeypatch, tmp_path):
    def raiser(_command, **_kwargs):
        raise subprocess.TimeoutExpired(["sleep"], 1)

    monkeypatch.setattr(sandbox.subprocess, "run", raiser)
    result = sandbox.run(["sleep", "100"], timeout=1, cwd=str(tmp_path))
    assert result["ok"] is False
    assert "timed out" in result["error"]


def test_registry_has_gmail_tools():
    assert {"gmail_latest", "gmail_search"}.issubset(TOOL_NAMES)