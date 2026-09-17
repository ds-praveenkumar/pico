"""Research sub-agent for web and browser tasks via the ego-lite browser skill."""

from typing import Any, Dict, List, Optional

from brain.logging_setup import get_logger
from brain.config import personalize

from agents.base_agent import BaseAgent, openai_tool_schemas
from agents.tools.skill_read import list_skills

logger = get_logger(__name__)

BROWSER_SKILLS = ("ego-lite-browser-use",)

DEFAULT_RESEARCHER_TURNS = 10

RESEARCHER_TOOL_NAMES = (
    "ego_lite_browse_use",
    "ask_master",
    "skill_read",
    "list_skills",
    "current_date",
    "memory_remember",
    "memory_note",
    "memory_episode",
    "memory_recall",
    "semantic_remember",
    "semantic_search",
)

_RESEARCHER_SYSTEM_PROMPT = (
    "You are pico's researcher. You find and verify information for the master, "
    "{master}, using the browser skill and safe local tools. "
    "Only browse validated http(s) URLs. If no browser skill is installed yet, "
    "stop and report that the skill is missing so the master can decide whether "
    "to build it. "
    "Drive the browser one action per ego_lite_browse_use call: start with "
    "action='load' on the site's landing URL, read the "
    "snapshot, then click refs (action='click' with the exact ref like 'e5' or "
    "a CSS selector from the snapshot), fill inputs (action='fill' with "
    "'selector' and 'query'), and pick dropdown options (action='select') until "
    "the page shows what the master asked for; submit forms with action='press' "
    "('query' defaults to Enter). Then stop and summarize. The page stays open "
    "between calls, so act on the current page. When you need to find a site, "
    "load a search url like https://www.google.com/search?q=<topic> and read "
    "the results — do not guess the hostname. External links rarely navigate "
    "in-place, so when the snapshot shows the target on another host, navigate "
    "straight to it with action='load' using that URL. Do not guess deep URLs — "
    "read them from snapshots. When a page shows a CAPTCHA, a login/OTP prompt, "
    "or required form fields, STOP guessing — the result includes 'need_human' "
    "and the tool hands the tab to the master: it tells {master} what to do and "
    "WAITS for him to hand control back, then pico submits the form itself and "
    "returns the result page — just report what the tool returns and never fill "
    "a CAPTCHA answer yourself. If a browse call returns "
    "'paused', the tab is parked under {master}'s control (the wait timed out or "
    "an earlier hand-off is open) — this is a RECOVERY situation. You MUST "
    "follow this exact flow: "
    "call 'ask_master' immediately (e.g. 'The browser tab is parked under your "
    "control. Are you done with it? Say continue and I will resume browsing'), "
    "and once the master confirms they are done (they submitted the form), call "
    "ego_lite_browse_use with action='claim' to reclaim the tab and READ the "
    "result page. The paused result includes a next_step field with exact "
    "instructions. NEVER end the task while a hand-off is unresolved — the "
    "browser is still open and waiting. Never invent "
    "passwords, OTPs, or personal details. Stop as soon as the page clearly "
    "shows the result; do not keep clicking for no reason. Never access "
    "unrelated or private information. IMPORTANT — cleanup: once the goal is "
    "complete and you have the answer, ALWAYS close the browser task by calling "
    "ego_lite_browse_use with action='release' exactly once (no url needed). That frees "
    "the browser session for the next task. Never close while a hand-off is "
    "pending (the result had 'need_human' or 'paused') — the master still owns "
    "the tab. Remember what you find: record useful facts with memory_remember, "
    "keep session notes with memory_note, capture completed research with "
    "memory_episode, and store important findings with semantic_remember for "
    "later recall by meaning."
)


def _researcher_tools() -> List[Dict[str, Any]]:
    """Return the researcher's browser-focused tool schemas."""
    return [s for s in openai_tool_schemas() if s["function"]["name"] in RESEARCHER_TOOL_NAMES]


class Researcher(BaseAgent):
    """Sub-agent that researches via the ego-lite browser skills."""

    def __init__(
        self,
        name: str,
        llm: Any,
        approve: Optional[Any] = None,
        max_turns: int = 6,
        memory: Optional[Any] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        cancel_token: Optional[Any] = None,
    ) -> None:
        super().__init__(
            name=name, llm=llm, approve=approve, max_turns=max_turns, memory=memory,
            tools=tools or _researcher_tools(), cancel_token=cancel_token,
        )

    def system_instructions(self) -> str:
        return personalize(_RESEARCHER_SYSTEM_PROMPT)

    def browsable(self) -> bool:
        """Return True when a browser skill is present."""
        skills = set(list_skills())
        return any(skill in skills for skill in BROWSER_SKILLS)

    def research(self, url: str, task: str = "Retrieve and summarize this page.") -> str:
        """Attempt one browser task and summarize the outcome.

        A human hand-off is resolved inside the tool call itself (the tool waits
        for the master and resumes); only a ``paused`` result needs the
        ``ask_master`` + ``action='claim'`` recovery flow here.
        """
        if not self.browsable():
            return (
                "No browser skill is installed yet, so I cannot browse "
                f"{url!r}. Please ask the master whether to wire the browser skill up."
            )
        return self.run(f"{task}\nURL to visit: {url}")