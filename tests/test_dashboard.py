"""Tests for the rich dashboard TUI (never touches the real terminal screen)."""

import io

from rich.console import Console

from brain.memory import Memory
from dashboard import Dashboard


def _console() -> Console:
    return Console(file=io.StringIO(), width=100, height=40, force_terminal=True)


def _render(dashboard: Dashboard) -> str:
    buffer = _console()
    buffer.print(dashboard._render())
    return buffer.file.getvalue()


def test_dashboard_renders_plan_and_usage():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    dash.set_plan([{"agent": "executor", "task": "Read README.md"}])
    dash.mark_step(0, "done", "finished")
    dash.accumulate_usage({"prompt": 10, "completion": 5, "total": 15})
    dash.note_request("pico")
    out = _render(dash)
    assert "Task plan" in out
    assert "Read README.md" in out
    assert "done" in out
    assert "10" in out and "5" in out and "15" in out
    assert "pico" in out


def test_dashboard_pending_status_and_run_transitions():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    dash.set_plan([{"agent": "executor", "task": "a"}, {"agent": "researcher", "task": "b"}])
    out_before = _render(dash)
    assert out_before.count("pending") == 2
    dash.mark_step(0, "running")
    dash.mark_step(0, "done")
    dash.mark_step(1, "failed")
    out_after = _render(dash)
    assert "done" in out_after and "failed" in out_after
    assert out_after.count("pending") == 0


def test_dashboard_log_tail_is_capped():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    for i in range(10):
        dash.add_log(f"line {i}")
    assert len(dash._log) == 6
    assert dash._log[0] == "line 4"


def test_dashboard_log_tail_rendered():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    dash.add_log("hello world")
    assert "hello world" in _render(dash)


def test_dashboard_usage_accumulates():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    dash.accumulate_usage({"prompt": 1, "completion": 1, "total": 2})
    dash.accumulate_usage({"prompt": 3, "completion": 2, "total": 5})
    assert dash._usage == {"prompt": 4, "completion": 3, "total": 7}


def test_dashboard_disabled_does_not_start_live():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    dash.enabled = False
    dash.start()
    assert dash._live is None
    dash.stop()
    assert dash._live is None


def test_dashboard_shows_memory_counts(tmp_path):
    memory = Memory(dir_path=tmp_path)
    memory.remember("pref", "x")
    memory.add_episode("e", "s")
    memory.remember_semantic("note text", {})
    dash = Dashboard(console=_console(), provider="nvidia", model="m", memory=memory)
    out = _render(dash)
    assert "Memory" in out
    assert "long-term: 1" in out
    assert "episodes: 1" in out
    assert "semantic: 1" in out


def test_dashboard_without_memory_notes_it():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    assert "memory not wired" in _render(dash)


def test_dashboard_streams_live_output():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    dash.set_output("executor", "found 42 results in the index")
    assert "Live output" in _render(dash)
    assert "found 42" in _render(dash)


def test_dashboard_output_keeps_latest_two_and_caps_len():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    for i in range(5):
        dash.set_output("agent", f"chunk {i}")
    assert len(dash._output) == 2
    assert "chunk 3" in dash._output[0]
    long = "x" * 1000
    dash.set_output("agent", long)
    assert "…" in dash._output[-1]


def test_dashboard_set_output_ignores_empty():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    dash.set_output("agent", "")
    assert dash._output == []


def test_dashboard_footer_shows_input_line():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    dash._input_active = True
    dash._input_prompt = "pico> "
    dash._input_buffer = "hello"
    out = _render(dash)
    assert "pico> hello" in out


def test_dashboard_shows_reply_and_meta():
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    dash.show_reply("The answer is **42**.", "summary · tokens line")
    out = _render(dash)
    assert "42" in out
    assert "summary" in out


def test_dashboard_read_line_falls_back_when_not_a_tty(monkeypatch):
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "hello world")
    assert dash.read_line("pico> ") == "hello world"
    assert dash._input_active is False


def test_dashboard_ask_yes_no_non_tty(monkeypatch):
    dash = Dashboard(console=_console(), provider="nvidia", model="m")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert dash.ask_yes_no("Approve?") is True