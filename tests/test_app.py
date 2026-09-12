"""Tests for the CLI's risk-aware approval policy."""

import io

import pytest
from rich.console import Console

from app import _auto_approve, _bash_needs_approval, PlanView, SessionStats, build_client, run_task
from brain.memory import Memory
from dashboard import Dashboard


def _console() -> Console:
    return Console(file=io.StringIO(), width=100, height=40, force_terminal=True)


def test_auto_approves_read_only_tools():
    for tool in ("current_date", "file_read", "skill_read", "memory_recall", "memory_note", "gmail_latest"):
        assert _auto_approve(tool, {}) is True


def test_auto_approves_project_confined_writes_and_browsing():
    assert _auto_approve("file_write", {}) is True
    assert _auto_approve("ego_lite_browse_use", {}) is True
    assert _auto_approve("ask_master", {}) is True


def test_bash_read_only_commands_auto_run():
    for command in ("date", "echo hello", "pwd", "ls -la", "cat README.md", "whoami"):
        assert _bash_needs_approval(command) is False
        assert _auto_approve("bash", {"command": command}) is True


def test_bash_git_read_only_subcommands_auto_run():
    for command in ("git status", "git log --oneline", "git diff", "git show HEAD", "git branch", "git tag", "git blame main -- app.py"):
        assert _bash_needs_approval(command) is False


def test_bash_version_and_package_queries_auto_run():
    for command in ("python --version", "python3 -V", "pip list", "pip freeze", "pip show rich"):
        assert _bash_needs_approval(command) is False
        assert _auto_approve("bash", {"command": command}) is True


def test_bash_write_and_unknown_commands_ask():
    for command in (
        "echo hi > /tmp/out.txt",
        "python -c 'import os'",
        "python -m flask run",
        "pip install rich",
        "git push origin main",
        "git commit -m x",
        "sudo apt install x",
        "rm -rf tmp/",
        "",
    ):
        assert _bash_needs_approval(command) is True
        assert _auto_approve("bash", {"command": command}) is False


def test_unknown_tool_asks():
    assert _auto_approve("mystery_tool", {}) is False
    assert _auto_approve("bash", {}) is False


def test_run_task_survives_provider_failure(tmp_path, capsys):
    class ExplodingPico:
        def run(self, _task: str) -> str:
            raise RuntimeError("provider exploded")

    dash = Dashboard(console=_console(), provider="fake", model="m", memory=Memory(dir_path=tmp_path))
    dash.enabled = False
    ok = run_task(ExplodingPico(), llm=None, task="hi", plan=PlanView(), stats=SessionStats(), dashboard=dash)
    assert ok is False
    assert "could not finish" in capsys.readouterr().out.lower()


def test_run_task_persistent_routes_reply_into_dashboard(tmp_path, capsys):
    class NoopPico:
        def run(self, _task: str) -> str:
            return "hello master"

    from types import SimpleNamespace

    llm = SimpleNamespace(usage={"prompt": 0, "completion": 0, "total": 0})
    dash = Dashboard(console=_console(), provider="fake", model="m", memory=Memory(dir_path=tmp_path))
    dash.enabled = False
    ok = run_task(
        NoopPico(), llm=llm, task="hi", plan=PlanView(), stats=SessionStats(), dashboard=dash, persistent=True
    )
    assert ok is True
    assert dash._reply == "hello master"
    assert capsys.readouterr().out == ""


def test_build_client_groq_reads_groq_keys(monkeypatch):
    monkeypatch.setenv("PROVIDER", "GROQ")
    monkeypatch.setenv("GROQ_MODEL_ID", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("GROK_MODEL_ID", raising=False)
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    client = build_client()
    assert client.provider == "groq"
    assert client.model_name == "openai/gpt-oss-120b"
    assert client.api_key == "gsk-test"
    assert client.base_url == "https://api.groq.com/openai/v1"


def test_build_client_groq_falls_back_to_grok_env_names(monkeypatch):
    monkeypatch.setenv("PROVIDER", "groq")
    monkeypatch.delenv("GROQ_MODEL_ID", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROK_MODEL_ID", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROK_API_KEY", "gsk-test")
    client = build_client()
    assert client.provider == "groq"
    assert client.model_name == "openai/gpt-oss-120b"
    assert client.api_key == "gsk-test"


def test_build_client_groq_missing_key_raises(monkeypatch):
    monkeypatch.setenv("PROVIDER", "groq")
    monkeypatch.setenv("GROQ_MODEL_ID", "m")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        build_client()