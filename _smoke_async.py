"""One-off async smoke check (not part of the test suite)."""
import asyncio
import pathlib
import sys
import tempfile

sys.path.insert(0, "tests")

from conftest import FakeLLM  # noqa: E402
from brain.memory import Memory  # noqa: E402
from agents.cancellation import CancellationToken, TaskCancelled  # noqa: E402
from agents.executor import Executor  # noqa: E402
from agents.pico import Pico  # noqa: E402


async def main() -> None:
    llm = FakeLLM()
    exec_ = Executor(name="executor", llm=llm, approve=None)
    out = await exec_.run_async("Read README.md")
    assert "tool task done" in out, out
    print("async executor OK:", out)

    tmp = pathlib.Path(tempfile.mkdtemp())
    mem = Memory(dir_path=tmp)
    pico = Pico(llm=FakeLLM(), memory=mem)
    token = CancellationToken()
    pico.cancel_token = token
    pico.executor.cancel_token = token
    pico.researcher.cancel_token = token
    events = []
    pico.on_plan = lambda steps: events.append(("plan", steps))
    pico.on_step = lambda i, s, n: events.append(("step", i, s))
    reply = await pico.run_async("read the README.md file and tell me what it says")
    assert "Report:" in reply, reply
    assert any(e[0] == "plan" for e in events), events
    assert any(e[0] == "step" and e[2] == "done" for e in events), events
    print("async pico OK:", reply)

    tok2 = CancellationToken()
    tok2.cancel()
    try:
        await Executor(name="x", llm=FakeLLM(), cancel_token=tok2).run_async("hi")
    except TaskCancelled as exc:
        print("cancellation OK:", exc)


asyncio.run(main())