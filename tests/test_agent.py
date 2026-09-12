"""Tests for the multi-agent layer using the fake LLM fixture."""

from types import SimpleNamespace

from conftest import FakeLLM
from brain.memory import Memory

from agents.executor import Executor
from agents.pico import Pico, parse_plan
from agents.researcher import Researcher
from agents.tools import memory as memory_tools
from agents.tools import dispatch


def _reject_all(name, args):
    return False


def test_parse_plan_with_fences():
    text = '```json\n[{"agent": "executor", "task": "summarize README"}]```'
    assert parse_plan(text) == [{"agent": "executor", "task": "summarize README"}]


def test_parse_plan_plain_json():
    text = '[{"agent": "researcher", "task": "find a website"}]'
    assert parse_plan(text) == [{"agent": "researcher", "task": "find a website"}]


def test_parse_plan_rejects_noise():
    assert parse_plan("no steps here") == []
    assert parse_plan("") == []


def test_executor_runs_tool_loop(fake_llm):
    executor = Executor(name="executor", llm=fake_llm)
    out = executor.run("Read README.md")
    assert "tool task done" in out


def test_executor_loop_knows_tool_result(fake_llm):
    executor = Executor(name="executor", llm=fake_llm)
    executor.run("Read README.md")
    assert any(
        "tool task done" in str(msg) or "file_read" in str(msg) for msg in executor.history
    )


def test_supervision_approves(fake_llm):
    executor = Executor(name="executor", llm=fake_llm, approve=None)
    result = executor.execute_tool("file_read", path="README.md")
    assert result["ok"] is True


def test_supervision_rejects(fake_llm):
    executor = Executor(name="executor", llm=fake_llm, approve=_reject_all)
    result = executor.execute_tool("file_read", path="README.md")
    assert result["ok"] is False
    assert "rejected" in result["error"]


def test_bash_tool_rejected_by_supervisor(fake_llm):
    executor = Executor(name="executor", llm=fake_llm, approve=_reject_all)
    result = executor.execute_tool("bash", command="echo hi")
    assert result["ok"] is False


def test_pico_delegates_and_summarizes(fake_llm):
    pico = Pico(llm=fake_llm)
    out = pico.run("read the README.md file and tell me what it says")
    assert "Report:" in out
    assert any("Read README.md and summarize" == c for c in fake_llm.calls)


def test_pico_unknown_subagent_skipped():
    pico = Pico(llm=SimpleNamespace(tools=None))
    assert pico._sub_agent("nope") is None


def test_researcher_detects_browsable_skill():
    researcher = Researcher(name="researcher", llm=SimpleNamespace(tools=None))
    assert researcher.browsable() is True


def test_executor_captures_episodic_and_working(fake_llm, tmp_path):
    memory = Memory(dir_path=tmp_path)
    executor = Executor(name="executor", llm=fake_llm, memory=memory)
    executor.run("Read README.md")
    episodes = memory.episodes()
    assert len(episodes) >= 1
    assert episodes[0]["title"] == "Read README.md"
    assert any(key.startswith("task:") for key in memory.working.snapshot())


def test_long_term_context_injected_into_system_prompt(fake_llm, tmp_path):
    memory = Memory(dir_path=tmp_path)
    memory.remember("preference", "strong coffee")
    executor = Executor(name="executor", llm=fake_llm, memory=memory)
    executor.run("Read README.md")
    assert any("strong coffee" in str(msg) for msg in executor.history)


def test_memory_bound_during_run(fake_llm, tmp_path):
    memory = Memory(dir_path=tmp_path)

    class ProbeLLM(FakeLLM):
        def __init__(self):
            super().__init__()
            self.bound = False

        def generate(self, messages=None, **kwargs):
            self.bound = memory_tools._CURRENT is not None
            return super().generate(messages=messages, **kwargs)

    probe = ProbeLLM()
    executor = Executor(name="executor", llm=probe, memory=memory)
    executor.run("Read README.md")
    assert probe.bound is True
    assert memory_tools._CURRENT is None


def test_pico_records_whole_task_episode(fake_llm, tmp_path):
    memory = Memory(dir_path=tmp_path)
    pico = Pico(llm=fake_llm, memory=memory)
    pico.run("read the README.md file and tell me what it says")
    assert any("read the README.md file" in e["title"] for e in memory.episodes())