"""Async agent-layer tests: run_async, compaction, and cooperative cancellation."""

import asyncio
from types import SimpleNamespace

import pytest

from agents.cancellation import CancellationToken, TaskCancelled
from agents.executor import Executor
from agents.pico import Pico
from brain.memory import Memory
from conftest import FakeLLM, fake_message

_ZERO_USAGE = {"prompt": 0, "completion": 0, "total": 0}


async def test_executor_run_async_returns_final_text():
    executor = Executor(name="executor", llm=FakeLLM())
    out = await executor.run_async("Read README.md")
    assert "tool task done" in out


async def test_executor_async_messages_contain_tool_result():
    executor = Executor(name="executor", llm=FakeLLM())
    await executor.run_async("Read README.md")
    roles = [m.get("role") for m in executor.history]
    assert "assistant" in roles
    assert "tool" in roles
    assert executor.llm.tools


async def test_pico_async_delegates_and_summarizes(tmp_path):
    llm = FakeLLM()
    pico = Pico(llm=llm, memory=Memory(dir_path=tmp_path))
    plans, steps = [], []
    pico.on_plan = lambda p: plans.append(p)
    pico.on_step = lambda *a: steps.append(a)
    reply = await pico.run_async("read the README.md file and tell me what it says")
    assert "Report:" in reply
    assert plans[0] == [{"agent": "executor", "task": "Read README.md and summarize"}]
    assert any(s[1] == "running" for s in steps)
    assert any(s[1] == "done" for s in steps)


async def test_run_async_honours_cancellation_token_before_start():
    token = CancellationToken()
    token.cancel()
    executor = Executor(name="executor", llm=FakeLLM(), cancel_token=token)
    with pytest.raises(TaskCancelled):
        await executor.run_async("Read README.md")


async def test_pico_async_cancelled_between_steps(tmp_path):
    llm = FakeLLM()
    pico = Pico(llm=llm, memory=Memory(dir_path=tmp_path))
    token = CancellationToken()
    for obj in (pico, pico.executor, pico.researcher):
        obj.cancel_token = token

    original = pico.executor.run_async

    async def cancel_after_first_step(task: str) -> str:
        result = await original(task)
        token.cancel()
        return result

    pico.executor.run_async = cancel_after_first_step  # type: ignore[assignment]
    with pytest.raises(TaskCancelled):
        await pico.run_async("read the README.md file and tell me what it says")


class _RecordingLLM(FakeLLM):
    """FakeLLM that snapshots every message list it receives."""

    def __init__(self) -> None:
        super().__init__()
        self.all_messages: list[list[dict]] = []
        self._prompt = 10
        self._completion = 5

    def generate(self, messages=None, **kwargs):  # noqa: D401
        if messages:
            self.all_messages.append([dict(m) for m in messages])
        self.remember_usage(prompt=self._prompt, completion=self._completion)
        return super().generate(messages=messages, **kwargs)


def _has_compaction_summary(llm: _RecordingLLM) -> bool:
    for messages in llm.all_messages:
        if any("## Compaction summary" in str(m.get("content", "")) for m in messages):
            return True
    return False


async def test_async_compaction_folds_history(tmp_path):
    llm = _RecordingLLM()
    executor = Executor(name="executor", llm=llm, memory=Memory(dir_path=tmp_path))
    executor.compaction_threshold = 5
    out = await executor.run_async("Read README.md")
    assert "tool task done" in out
    assert _has_compaction_summary(llm)


async def test_async_run_resets_compaction_state(tmp_path):
    llm = _RecordingLLM()
    executor = Executor(name="executor", llm=llm, memory=Memory(dir_path=tmp_path))
    executor.compaction_threshold = 5
    await executor.run_async("Read README.md")
    llm.all_messages.clear()
    await executor.run_async("Read README.md")
    assert _has_compaction_summary(llm)


class _ScriptedLLM(FakeLLM):
    """FakeLLM that replays a fixed list of chat replies in order."""

    def __init__(self, replies):
        super().__init__()
        self._replies = list(replies)

    def generate(self, messages=None, **kwargs):
        self.calls.append(messages[-1]["content"])
        if self._replies:
            return self._replies.pop(0)
        return fake_message("no more scripted replies")


def _browse_call():
    return [
        SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="ego_lite_browse_use", arguments='{"url": "https://example.com"}'
            ),
        )
    ]


async def test_async_loop_mirrors_handoff_guard(monkeypatch):
    import agents.base_agent as base_agent_module
    from agents.base_agent import HANDOFF_REMINDER_LIMIT

    monkeypatch.setattr(
        base_agent_module, "dispatch", lambda name, **kw: {"ok": False, "paused": True}
    )
    llm = _ScriptedLLM(
        [
            fake_message(None, _browse_call()),
            fake_message("all done"),
            fake_message("all done"),
            fake_message("all done"),
        ]
    )
    executor = Executor(name="executor", llm=llm)
    out = await executor.run_async("browse example.com")
    assert out.startswith("WARNING")
    assert "parked" in out
    assert "all done" in out
    assert executor._handoff_reminders == HANDOFF_REMINDER_LIMIT
    reminders = [c for c in llm.calls if "still handed off" in c]
    assert len(reminders) == HANDOFF_REMINDER_LIMIT


async def test_async_loop_resumes_after_claim(monkeypatch):
    import agents.base_agent as base_agent_module

    results = [{"ok": False, "paused": True}, {"ok": True, "claimed": True}]
    monkeypatch.setattr(base_agent_module, "dispatch", lambda name, **kw: results.pop(0))
    llm = _ScriptedLLM(
        [
            fake_message(None, _browse_call()),
            fake_message("done"),
            fake_message(None, _browse_call()),
            fake_message("done for real"),
        ]
    )
    executor = Executor(name="executor", llm=llm)
    out = await executor.run_async("browse example.com")
    assert out == "done for real"
    assert executor._handoff_reminders == 1
    assert not out.startswith("WARNING")