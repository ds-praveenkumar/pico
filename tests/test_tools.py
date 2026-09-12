"""Tests for the safe tools layer."""

import subprocess
import tempfile
from pathlib import Path

import pytest

from agents.tools import REGISTRY, TOOL_NAMES, dispatch
from agents.tools import ask as ask_tools
from agents.tools import ego_lite_browse_use, gmail as gmail_tools, memory as memory_tools
from agents.tools import sandbox
from agents.tools.bash import is_allowed_command, run_command
from agents.tools.ego_lite_browse_use import (
    browse,
    extract_action_error,
    extract_metadata,
    extract_nav_error,
    extract_need_human,
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
    assert "ask_master" in TOOL_NAMES
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


def _snapshot_proc() -> subprocess.CompletedProcess:
    stdout = (
        "TITLE: Page\n"
        "FINAL_URL: https://example.com/\n"
        "---SNAPSHOT-BEGIN---\n"
        "root snapshot\n"
        "---SNAPSHOT-END---\n"
    )
    return _fake_proc(stdout)


def test_browse_keeps_page_open_and_reuses_space(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)

    def fake_run(script, timeout):
        captured["script"] = script
        return _snapshot_proc()

    monkeypatch.setattr(ego_lite_browse_use, "_run_browser_script", fake_run)
    result = browse("https://example.com")
    assert result["ok"] is True
    script = captured["script"]
    assert 'const NAME = "pico: research"' in script
    assert "await listTaskSpaces()" in script
    assert "takeOverTaskSpace(existing.id)" in script
    assert "claimTaskSpace(existing.id)" in script
    assert "keep: ['p1']" in script
    assert 'page.goto("https://example.com"' in script


def test_browse_click_normalizes_refs(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)

    def fake_run(script, timeout):
        captured["script"] = script
        return _snapshot_proc()

    monkeypatch.setattr(ego_lite_browse_use, "_run_browser_script", fake_run)
    browse(url="https://example.com", action="click", selector="6")
    assert 'page.click("@6"' in captured["script"]
    browse(url="https://example.com", action="click", selector="ref=7")
    assert 'page.click("@7"' in captured["script"]
    browse(url="https://example.com", action="click", selector="loc=css:a")
    assert 'page.click("loc=css:a"' in captured["script"]
    browse(url="https://example.com", action="click", selector='list_item [ref=15, loc=css:a[aria-label="Services"], url=https://example.com/#]')
    assert 'page.click("loc=css:a[aria-label=\\"Services\\"]"' in captured["script"]
    browse(url="https://example.com", action="click", selector='anchor [ref=12, url=https://x/]')
    assert 'page.click("@12"' in captured["script"]


def test_browse_fill_and_select_build_script(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)

    def fake_run(script, timeout):
        captured["script"] = script
        return _snapshot_proc()

    monkeypatch.setattr(ego_lite_browse_use, "_run_browser_script", fake_run)
    browse(url="https://example.com", action="fill", selector="input[name=q]", query="Jamshedpur")
    assert 'page.fill("input[name=q]", "Jamshedpur"' in captured["script"]
    browse(url="https://example.com", action="select", selector="select[name=district]", query="East Singhbhum")
    assert 'page.selectOption("select[name=district]", "East Singhbhum"' in captured["script"]


def test_browse_survives_action_error(monkeypatch):
    stdout = (
        "TITLE: T\n"
        "ACTION_ERROR: no element found with ref=99\n"
        "---SNAPSHOT-BEGIN---\n"
        "still here\n"
        "---SNAPSHOT-END---\n"
    )
    fake = _fake_proc(stdout)
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)
    monkeypatch.setattr(ego_lite_browse_use, "_run_browser_script", lambda script, timeout: fake)
    result = browse(url="https://example.com", action="click", selector="99")
    assert result["ok"] is True
    assert result["action_error"] == "no element found with ref=99"
    assert result["action"] == "click"


def test_extract_action_error():
    assert extract_action_error("ACTION_ERROR: boom\n") == "boom"
    assert extract_action_error("") is None


def test_extract_need_human():
    assert extract_need_human("NEED_HUMAN:captcha\n") == "captcha"
    assert extract_need_human("NEED_HUMAN: login\n") == "login"
    assert extract_need_human("") is None


def test_browse_reports_need_human_on_captcha(monkeypatch):
    stdout = (
        "TITLE: T\n"
        "FINAL_URL: https://example.com/x\n"
        "---SNAPSHOT-BEGIN---\n"
        'textbox [ref=3, loc=css:input[name="captcha"]]\n'
        "text 'Enter the characters shown to prove you are human'\n"
        "---SNAPSHOT-END---\n"
        "NEED_HUMAN:captcha\n"
    )
    fake = _fake_proc(stdout)
    captured: dict = {}
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)
    monkeypatch.setattr(
        ego_lite_browse_use, "_run_browser_script",
        lambda script, timeout: (captured.update(script=script) or fake),
    )
    result = browse("https://example.com")
    assert result["ok"] is True
    assert result["need_human"]["kind"] == "captcha"
    assert "ask_master" in result["need_human"]["hint"]
    assert "await task.handOff()" in captured["script"]
    assert "kindFor" in captured["script"]


def test_browse_reports_need_human_on_login_form(monkeypatch):
    stdout = (
        "TITLE: T\n"
        "FINAL_URL: https://example.com/login\n"
        "---SNAPSHOT-BEGIN---\n"
        "textbox [ref=4, loc=css:input#email]\n"
        "text 'Password'\n"
        "---SNAPSHOT-END---\n"
        "NEED_HUMAN:login\n"
    )
    fake = _fake_proc(stdout)
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)
    monkeypatch.setattr(ego_lite_browse_use, "_run_browser_script", lambda script, timeout: fake)
    result = browse("https://example.com/login")
    assert result["ok"] is True
    assert result["need_human"]["kind"] == "login"


def test_browse_reports_paused_when_master_owns_space(monkeypatch):
    stdout = "SESSION_PAUSED: the master currently owns this task space; browser commands are paused.\n"
    fake = _fake_proc(stdout)
    captured: dict = {}
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)
    monkeypatch.setattr(
        ego_lite_browse_use, "_run_browser_script",
        lambda script, timeout: (captured.update(script=script) or fake),
    )
    result = browse("https://example.com")
    assert result["ok"] is False
    assert result.get("paused") is True
    assert "action='claim'" in result["hint"]
    assert "existing.ownership === 'user'" in captured["script"]
    assert "if (!paused)" in captured["script"]


def test_browse_claim_action_resumes_master_owned_space(monkeypatch):
    stdout = (
        "TITLE: T\n"
        "FINAL_URL: https://example.com/after\n"
        "---SNAPSHOT-BEGIN---\n"
        "heading\n  text 'After'\n"
        "---SNAPSHOT-END---\n"
        "CLAIMED:true\n"
    )
    fake = _fake_proc(stdout)
    captured: dict = {}
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)
    monkeypatch.setattr(
        ego_lite_browse_use, "_run_browser_script",
        lambda script, timeout: (captured.update(script=script) or fake),
    )
    result = browse("https://example.com/after", action="claim")
    assert result["ok"] is True
    assert result.get("claimed") is True
    assert "existing.ownership === 'user' || CLAIM_ROUND" in captured["script"]
    assert "claimTaskSpace(existing.id)" in captured["script"]
    assert "CLAIM_ROUND" in captured["script"]


def test_extract_session_paused():
    from agents.tools.ego_lite_browse_use import extract_session_paused

    assert "parked" in extract_session_paused("SESSION_PAUSED: tab parked under master\n")
    assert extract_session_paused("TITLE: x\n") is None


def test_raise_ego_lite_window_on_macos(monkeypatch):
    from agents.tools.ego_lite_browse_use import _raise_ego_lite_window

    ran: dict = {}
    monkeypatch.setattr(ego_lite_browse_use.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ego_lite_browse_use.shutil, "which", lambda _: "/usr/bin/osascript")
    monkeypatch.setattr(
        ego_lite_browse_use.subprocess, "run",
        lambda *a, **kw: ran.update(args=a, kwargs=kw) or None,
    )
    assert _raise_ego_lite_window() is True
    assert 'tell application "ego lite" to activate' in ran["args"][0][2]


def test_raise_ego_lite_window_noop_off_macos(monkeypatch):
    from agents.tools.ego_lite_browse_use import _raise_ego_lite_window

    monkeypatch.setattr(ego_lite_browse_use.platform, "system", lambda: "Linux")
    assert _raise_ego_lite_window() is False


def test_browse_raises_window_on_need_human(monkeypatch):
    stdout = (
        "TITLE: T\n"
        "FINAL_URL: https://example.com/captcha\n"
        "---SNAPSHOT-BEGIN---\n"
        "text 'Verify you are human'\n"
        "---SNAPSHOT-END---\n"
        "NEED_HUMAN:captcha\n"
    )
    fake = _fake_proc(stdout)
    raised: dict = {"calls": 0}
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)
    monkeypatch.setattr(ego_lite_browse_use, "_run_browser_script", lambda script, timeout: fake)
    monkeypatch.setattr(ego_lite_browse_use, "_raise_ego_lite_window", lambda: raised.update(calls=raised["calls"] + 1) or True)
    result = browse("https://example.com/captcha")
    assert result["need_human"]["kind"] == "captcha"
    assert raised["calls"] == 1


def test_browse_raises_window_on_loaded_page(monkeypatch):
    stdout = (
        "TITLE: T\n"
        "FINAL_URL: https://example.com/\n"
        "---SNAPSHOT-BEGIN---\n"
        "text 'Hello'\n"
        "---SNAPSHOT-END---\n"
    )
    fake = _fake_proc(stdout)
    raised: dict = {"calls": 0}
    monkeypatch.setattr(ego_lite_browse_use, "browser_available", lambda: True)
    monkeypatch.setattr(ego_lite_browse_use, "_run_browser_script", lambda script, timeout: fake)
    monkeypatch.setattr(ego_lite_browse_use, "_raise_ego_lite_window", lambda: raised.update(calls=raised["calls"] + 1) or True)
    result = browse("https://example.com")
    assert result["ok"] is True
    assert raised["calls"] == 1


def test_ask_master_returns_answer(monkeypatch):
    monkeypatch.delenv("PICO_AUTO_APPROVE", raising=False)
    monkeypatch.setattr(ask_tools.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "I solved it")
    result = ask_tools.ask_master("Please solve the CAPTCHA")
    assert result["ok"] is True
    assert result["answer"] == "I solved it"


def test_ask_master_unattended_does_not_prompt(monkeypatch):
    monkeypatch.delenv("PICO_AUTO_APPROVE", raising=False)
    monkeypatch.setattr(ask_tools.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(AssertionError("must not prompt")))
    result = ask_tools.ask_master("Please solve the CAPTCHA")
    assert result["ok"] is False
    assert result["answer"] == ask_tools.UNATTENDED_ANSWER


def test_ask_master_auto_approve_returns_sentinel(monkeypatch):
    monkeypatch.setenv("PICO_AUTO_APPROVE", "1")
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(AssertionError("must not prompt")))
    result = ask_tools.ask_master("Please solve the CAPTCHA")
    assert result["ok"] is False
    assert result["answer"] == ask_tools.UNATTENDED_ANSWER


def test_ask_master_uses_installed_handler(monkeypatch):
    monkeypatch.delenv("PICO_AUTO_APPROVE", raising=False)
    monkeypatch.setattr(ask_tools.sys.stdin, "isatty", lambda: True)
    seen: dict = {}
    ask_tools.set_master_prompt(lambda question: (seen.update(question=question) or "done in the browser"))
    try:
        result = ask_tools.ask_master("Please solve the CAPTCHA then confirm")
        assert seen["question"] == "pico needs the master: Please solve the CAPTCHA then confirm"
        assert result["ok"] is True
        assert result["answer"] == "done in the browser"
    finally:
        ask_tools.set_master_prompt(None)


def test_extract_session_error():
    from agents.tools.ego_lite_browse_use import extract_session_error

    assert extract_session_error("SESSION_ERROR: could not claim\n") == "could not claim"
    assert extract_session_error("") is None


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


def test_current_date_returns_readable_date():
    from datetime import datetime
    from agents.tools.current_date import current_date

    result = current_date()
    assert result["ok"] is True
    parsed = datetime.fromisoformat(result["iso"])
    today = datetime.now().astimezone()
    assert parsed.date() == today.date()
    assert "2026" in result["date"] or "2026" in result["iso"]


def test_registry_has_current_date():
    assert "current_date" in TOOL_NAMES
    result = dispatch("current_date")
    assert result["ok"] is True
    assert result["date"]