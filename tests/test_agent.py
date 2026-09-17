"""Tests for the multi-agent layer using the fake LLM fixture."""

from types import SimpleNamespace

import agents.base_agent as base_agent_module
from agents.base_agent import HANDOFF_REMINDER_LIMIT
from conftest import FakeLLM, fake_message
from brain.memory import Memory

from agents.executor import Executor
from agents.pico import Pico, _PLAN_INSTRUCTION, parse_plan
from agents.researcher import Researcher
from agents.tools import memory as memory_tools
from agents.tools import dispatch


def _reject_all(name, args):
    return False


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


def _browse_call(args='{"url": "https://example.com"}'):
    return [
        SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="ego_lite_browse_use", arguments=args),
        )
    ]


_PAUSED = {"ok": False, "paused": True, "handoff_timeout": True, "awaiting_human": True}
_CLAIMED = {"ok": True, "claimed": True, "result": "after the master"}
_RESUMED = {"ok": True, "resumed": True, "control_returned": True, "result": "result page"}


def test_parse_plan_with_fences():
    text = '```json\n[{"agent": "executor", "task": "summarize README"}]```'
    assert parse_plan(text) == [{"agent": "executor", "task": "summarize README"}]


def test_parse_plan_plain_json():
    text = '[{"agent": "researcher", "task": "find a website"}]'
    assert parse_plan(text) == [{"agent": "researcher", "task": "find a website"}]


def test_parse_plan_rejects_noise():
    assert parse_plan("no steps here") == []
    assert parse_plan("") == []


def test_plan_instruction_forbids_bare_ask_step():
    assert "Never plan a bare 'ask the master' step" in _PLAN_INSTRUCTION
    assert "asks mid-task and keeps going from the answer" in _PLAN_INSTRUCTION


def test_executor_prompt_continues_after_master_answer():
    executor = Executor(name="executor", llm=None)
    instructions = executor.system_instructions()
    assert "DIRECTIVE to keep working" in instructions
    assert "never end by reporting" in instructions
    assert "ego_lite_browse_use" in instructions


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


def test_researcher_gets_generous_turn_budget():
    pico = Pico(llm=SimpleNamespace(tools=None), max_turns=1)
    assert pico.researcher.max_turns == 10


def test_researcher_toolset_is_browser_focused():
    researcher = Researcher(name="researcher", llm=SimpleNamespace(tools=None))
    names = {t["function"]["name"] for t in researcher.tools}
    assert "ego_lite_browse_use" in names
    assert "bash" not in names
    assert "latest_news" not in names


def test_shared_llm_gets_each_agents_own_toolset(fake_llm):
    llm = fake_llm
    executor = Executor(name="executor", llm=llm)
    researcher = Researcher(name="researcher", llm=llm)
    researcher.run("browse a site")
    researcher_names = {t["function"]["name"] for t in llm.tools}
    assert "gmail_list" not in researcher_names
    assert "ego_lite_browse_use" in researcher_names
    executor.run("Read README.md")
    executor_names = {t["function"]["name"] for t in llm.tools}
    assert "gmail_list" in executor_names
    assert "file_read" in executor_names
    assert "ego_lite_browse_use" in executor_names


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


class CountingLLM(FakeLLM):
    """FakeLLM that reports fixed token usage per generated response."""

    def __init__(self, prompt=10, completion=5):
        super().__init__()
        self.prompt = prompt
        self.completion = completion

    def generate(self, messages=None, **kwargs):
        response = super().generate(messages=messages, **kwargs)
        self.remember_usage(prompt=self.prompt, completion=self.completion)
        return response


def test_agent_accumulates_usage_and_emits_hook(tmp_path):
    llm = CountingLLM(prompt=10, completion=5)
    executor = Executor(name="executor", llm=llm, memory=Memory(dir_path=tmp_path))
    hooks = []
    executor.on_generate = lambda name, content="": hooks.append((name, content))
    executor.run("Read README.md")
    assert len(hooks) == len(llm.calls)
    assert all(h[0] == "executor" for h in hooks)
    assert all(h[1] for h in hooks)
    assert llm.usage["total"] > 0
    assert executor.usage["total"] == llm.usage["total"]


def test_usage_reset_isolates_agents():
    executor = Executor(name="executor", llm=CountingLLM())
    researcher = Researcher(name="researcher", llm=CountingLLM())
    assert executor.usage == {"prompt": 0, "completion": 0, "total": 0}
    assert researcher.usage["total"] == 0


def test_pico_reports_plan_and_step_hooks(fake_llm, tmp_path):
    pico = Pico(llm=fake_llm, memory=Memory(dir_path=tmp_path))
    plans, steps = [], []
    pico.on_plan = lambda steps_plan: plans.append(steps_plan)
    pico.on_step = lambda *args: steps.append(args)
    pico.run("read the README.md file and tell me what it says")
    assert len(plans) == 1 and len(plans[0]) == 1
    assert steps[0][1] == "running"
    assert steps[-1][1] == "done"
    assert any(isinstance(s, tuple) and s[2] for s in steps)


def test_pico_step_failure_reported(fake_llm):
    class FailingAgent:
        name = "failing"

        def run(self, _task):
            raise RuntimeError("boom")

    pico = Pico(llm=fake_llm)
    pico._sub_agent = lambda name: FailingAgent() if name == "executor" else None
    steps = []
    pico.on_plan = lambda _p: None
    pico.on_step = lambda *a: steps.append(a)
    out = pico.run("read the README.md file and tell me what it says")
    assert steps[-1][1] == "failed"
    assert "Report:" in out


def test_pico_usage_sum_includes_subagents(tmp_path):
    llm = CountingLLM()
    pico = Pico(llm=llm, memory=Memory(dir_path=tmp_path))
    pico.run("read the README.md file and tell me what it says")
    assert pico.usage["total"] == llm.usage["total"]
    assert pico.usage["total"] > 0


def test_pico_forwards_generate_hook(fake_llm, tmp_path):
    pico = Pico(llm=fake_llm, memory=Memory(dir_path=tmp_path))
    seen = []
    contents = []
    pico.on_generate = lambda name, content="": (seen.append(name), contents.append(content))
    pico.run("read the README.md file and tell me what it says")
    assert "pico" in seen
    assert "executor" in seen
    assert any(text for text in contents)


def test_agent_forwards_tool_hook(tmp_path):
    llm = CountingLLM(prompt=10, completion=5)
    executor = Executor(name="executor", llm=llm, memory=Memory(dir_path=tmp_path))
    hooks = []
    executor.on_tool = lambda name, content="": hooks.append((name, content))
    executor.run("Read README.md")
    assert hooks
    assert all(h[0] == "executor" for h in hooks)
    assert any("[file_read]" in h[1] for h in hooks)


def test_pico_forwards_tool_hook(fake_llm, tmp_path):
    pico = Pico(llm=fake_llm, memory=Memory(dir_path=tmp_path))
    hooks = []
    pico.on_tool = lambda name, content="": hooks.append((name, content))
    pico.run("read the README.md file and tell me what it says")
    assert any(name == "executor" and "[file_read]" in content for name, content in hooks)


def test_every_streamed_tool_output_is_prefixed_nonempty(fake_llm, tmp_path):
    """End-to-end: every tool result that reaches the UI must be a prefixed,
    non-empty snippet — never a bare filler like 'none' or 'null'."""
    pico = Pico(llm=fake_llm, memory=Memory(dir_path=tmp_path))
    streamed: list = []
    pico.on_tool = lambda name, content="": streamed.append(content)
    pico.run("read the README.md file and tell me what it says")
    assert streamed
    for snippet in streamed:
        assert snippet and snippet.strip()
        assert snippet.startswith("[")
        assert snippet.strip().lower() not in {"none", "null", "n/a", ""}
        assert " › none" not in snippet


class RecordingLLM(CountingLLM):
    """CountingLLM that also snapshots every message list it is given."""

    def __init__(self):
        super().__init__()
        self.all_messages = []

    def generate(self, messages=None, **kwargs):
        if messages:
            self.all_messages.append([dict(m) for m in messages])
        return super().generate(messages=messages, **kwargs)


def _has_compaction_summary(llm: RecordingLLM) -> bool:
    for messages in llm.all_messages:
        if any("## Compaction summary" in str(m.get("content", "")) for m in messages):
            return True
    return False


def test_no_compaction_under_threshold(tmp_path):
    llm = RecordingLLM()
    executor = Executor(name="executor", llm=llm, memory=Memory(dir_path=tmp_path))
    executor.run("Read README.md")
    assert not _has_compaction_summary(llm)


def test_compaction_folds_history_above_threshold(tmp_path):
    llm = RecordingLLM()
    executor = Executor(name="executor", llm=llm, memory=Memory(dir_path=tmp_path))
    executor.compaction_threshold = 5
    out = executor.run("Read README.md")
    assert _has_compaction_summary(llm)
    assert "tool task done" in out


def test_compaction_resets_per_run(tmp_path):
    llm = RecordingLLM()
    executor = Executor(name="executor", llm=llm, memory=Memory(dir_path=tmp_path))
    executor.compaction_threshold = 5
    executor.run("Read README.md")
    llm.all_messages.clear()
    executor.run("Read README.md")
    assert _has_compaction_summary(llm)


def test_tool_call_result_is_appended_to_messages(tmp_path):
    llm = RecordingLLM()
    executor = Executor(name="executor", llm=llm, memory=Memory(dir_path=tmp_path))
    executor.run("Read README.md")
    roles = [m["role"] for m in llm.all_messages[-1]]
    assert "assistant" in roles
    assert "tool" in roles
    tool_msg = next(m for m in llm.all_messages[-1] if m["role"] == "tool")
    assert '"ok"' in tool_msg["content"]
    assert tool_msg["tool_call_id"] == "call_1"


def test_parse_args_handles_fences_and_noise():
    executor = Executor(name="executor", llm=SimpleNamespace(tools=None))
    clean = SimpleNamespace(function=SimpleNamespace(arguments='{"path": "a", "limit": 2}'))
    assert executor._parse_args(clean) == {"path": "a", "limit": 2}
    fenced = SimpleNamespace(
        function=SimpleNamespace(arguments='```json\n{"cmd": "ls"}\n```')
    )
    assert executor._parse_args(fenced) == {"cmd": "ls"}
    noisy = SimpleNamespace(function=SimpleNamespace(arguments='here you go {"a": 1} ok?'))
    assert executor._parse_args(noisy) == {"a": 1}
    invalid = SimpleNamespace(function=SimpleNamespace(arguments="not json at all"))
    assert executor._parse_args(invalid) == {}
    array = SimpleNamespace(function=SimpleNamespace(arguments="[1, 2]"))
    assert executor._parse_args(array) == {}


def test_process_result_truncates_and_serializes():
    executor = Executor(name="executor", llm=SimpleNamespace(tools=None))
    huge = "x" * 30000
    body = executor._process_result({"ok": True, "data": huge})
    assert len(body) < 17000
    assert "…" in body
    weird = {"ok": True, "blob": object()}
    body = executor._process_result(weird)
    assert "not JSON-serializable" in body


def test_agent_does_not_end_while_tab_is_parked(monkeypatch):
    results = [_PAUSED, _CLAIMED]
    monkeypatch.setattr(base_agent_module, "dispatch", lambda name, **kw: results.pop(0))
    llm = _ScriptedLLM(
        [
            fake_message(None, _browse_call()),
            fake_message("the results are ready"),
            fake_message(None, _browse_call('{"action": "claim"}')),
            fake_message("the results are ready"),
        ]
    )
    executor = Executor(name="executor", llm=llm)
    out = executor.run("browse example.com")
    assert out == "the results are ready"
    assert llm._replies == []
    reminders = [c for c in llm.calls if "still handed off" in c]
    assert len(reminders) == 1
    assert executor._handoff_reminders == 1
    assert not out.startswith("WARNING")


def test_agent_reports_honestly_after_reminder_limit(monkeypatch):
    monkeypatch.setattr(base_agent_module, "dispatch", lambda name, **kw: dict(_PAUSED))
    llm = _ScriptedLLM(
        [
            fake_message(None, _browse_call()),
            fake_message("all done"),
            fake_message("all done"),
            fake_message("all done"),
        ]
    )
    executor = Executor(name="executor", llm=llm)
    out = executor.run("browse example.com")
    assert out.startswith("WARNING")
    assert "parked" in out
    assert "all done" in out
    assert executor._handoff_reminders == HANDOFF_REMINDER_LIMIT
    reminders = [c for c in llm.calls if "still handed off" in c]
    assert len(reminders) == HANDOFF_REMINDER_LIMIT


def test_agent_clears_handoff_state_on_resume(monkeypatch):
    monkeypatch.setattr(base_agent_module, "dispatch", lambda name, **kw: dict(_RESUMED))
    llm = _ScriptedLLM(
        [
            fake_message(None, _browse_call()),
            fake_message("result page read, goal complete"),
        ]
    )
    executor = Executor(name="executor", llm=llm)
    out = executor.run("browse example.com")
    assert out == "result page read, goal complete"
    assert executor._awaiting_human is False
    assert not [c for c in llm.calls if "still handed off" in c]


def test_track_handoff_state_ignores_other_tools():
    executor = Executor(name="executor", llm=SimpleNamespace(tools=None))
    executor._track_handoff_state("file_read", {"ok": False, "error": "boom"})
    assert executor._awaiting_human is False
    executor._track_handoff_state("ego_lite_browse_use", {"ok": True, "released": True})
    assert executor._awaiting_human is False