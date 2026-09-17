"""Tests for the CLI's risk-aware approval policy."""

import io
from typing import List

import pytest
from rich.console import Console

from app import _auto_approve, _bash_needs_approval, PlanView, SessionStats, build_client, resolve_model, run_task
from brain.memory import Memory
from ui import Dashboard


def _console() -> Console:
    return Console(file=io.StringIO(), width=100, height=40, force_terminal=True)


def _render_console(dashboard: Dashboard) -> str:
    buffer = _console()
    buffer.print(dashboard._render())
    return buffer.file.getvalue()


def test_auto_approves_read_only_tools():
    for tool in ("current_date", "file_read", "skill_read", "memory_recall", "memory_note", "gmail_list"):
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


def test_tui_repl_continues_after_completed_task(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app import run_tui_repl

    class SequentialPico:
        def __init__(self) -> None:
            self.tasks: List[str] = []

        def run(self, task: str) -> str:
            self.tasks.append(task)
            return f"REPLY:{task}"

    llm = SimpleNamespace(usage={"prompt": 0, "completion": 0, "total": 0})
    dash = Dashboard(console=_console(), provider="fake", model="m", memory=Memory(dir_path=tmp_path))
    dash.enabled = False
    lines = iter(["first task", "second task", "exit"])
    monkeypatch.setattr(dash, "read_line", lambda prompt="": next(lines))

    pico = SequentialPico()
    run_tui_repl(pico, llm=llm, plan=PlanView(), stats=SessionStats(), dashboard=dash)

    assert pico.tasks == ["first task", "second task"]
    assert dash._reply == "REPLY:second task"


def test_tui_repl_survives_task_crash_and_runs_next(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app import run_tui_repl

    calls = []

    class CrashThenRun:
        def run(self, task: str) -> str:
            calls.append(task)
            if task == "boom":
                raise RuntimeError("executor died")
            return f"REPLY:{task}"

    llm = SimpleNamespace(usage={"prompt": 0, "completion": 0, "total": 0})
    dash = Dashboard(console=_console(), provider="fake", model="m", memory=Memory(dir_path=tmp_path))
    dash.enabled = False
    lines = iter(["boom", "second task", "exit"])
    monkeypatch.setattr(dash, "read_line", lambda prompt="": next(lines))

    run_tui_repl(CrashThenRun(), llm=llm, plan=PlanView(), stats=SessionStats(), dashboard=dash)

    assert calls == ["boom", "second task"]
    assert dash._reply == "REPLY:second task"


def test_wired_live_output_streams_tool_results_never_bare_none(tmp_path, fake_llm):
    from conftest import FakeLLM
    from agents.pico import Pico
    from app import PlanView, SessionStats, wire_handlers

    dash = Dashboard(console=_console(), provider="fake", model="m", memory=Memory(dir_path=tmp_path))
    llm = FakeLLM()
    pico = Pico(llm=llm, memory=Memory(dir_path=tmp_path))
    wire_handlers(pico, llm, dash, PlanView(), SessionStats())
    streamed: list = []
    orig_set_output = dash.set_output

    def capture(agent_name, text):
        streamed.append((agent_name, text))
        return orig_set_output(agent_name, text)

    dash.set_output = capture
    reply = pico.run("read the README.md file and tell me what it says")
    assert reply
    assert streamed
    assert any("[file_read]" in text for _, text in streamed)
    for _, text in streamed:
        if text.startswith("["):
            assert text.strip().lower() not in {"none", "null", "n/a", ""}
    rendered = _render_console(dash)
    assert " › none" not in rendered
    assert " › None" not in rendered


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


def test_resolve_model_follows_active_provider(monkeypatch):
    monkeypatch.setenv("NVIDIA_MODEL_ID", "nvidia/nemotron-3.5-lightning-30b-a3b")
    monkeypatch.setenv("GROQ_MODEL_ID", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROK_MODEL_ID", "openai/gpt-oss-120b")
    monkeypatch.setenv("CEREBRAS_MODEL_ID", "cerebras-model")
    monkeypatch.setenv("OPENROUTER_MODEL_ID", "openrouter-model")
    monkeypatch.setenv("MODEL_ID", "openai-model")
    assert resolve_model("groq") == "openai/gpt-oss-120b"
    assert resolve_model("groq").startswith("openai/") is True
    assert "nemotron" not in resolve_model("groq")
    assert resolve_model("nvidia") == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert resolve_model("cerebras") == "cerebras-model"
    assert resolve_model("openrouter") == "openrouter-model"
    assert resolve_model("openai") == "openai-model"
    assert resolve_model("mystery") == ""


def test_build_client_openrouter(monkeypatch):
    monkeypatch.setenv("PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL_ID", "anthropic/claude-sonnet-4")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client = build_client()
    assert client.provider == "openrouter"
    assert client.model_name == "anthropic/claude-sonnet-4"
    assert client.api_key == "sk-or-test"
    assert client.base_url == "https://openrouter.ai/api/v1"


def test_build_client_openrouter_missing_key_raises(monkeypatch):
    monkeypatch.setenv("PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL_ID", "m")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        build_client()


def test_master_notice_drives_dashboard_status(tmp_path):
    from agents.tools import ask as ask_tools

    dash = Dashboard(console=_console(), provider="fake", model="m", memory=Memory(dir_path=tmp_path))
    ask_tools.set_master_notice(dash.set_status)
    try:
        assert ask_tools.notify_master("solve the CAPTCHA in the open browser") is True
        assert "solve the CAPTCHA" in dash._status
        assert "solve the CAPTCHA" in _render_console(dash)
    finally:
        ask_tools.set_master_notice(None)