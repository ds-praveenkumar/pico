"""Research sub-agent for web and browser tasks via the ego-lite browser skill."""

from typing import Any, Dict, List, Optional

from brain.logging_setup import get_logger

from agents.base_agent import BaseAgent, openai_tool_schemas
from agents.tools.skill_read import list_skills

logger = get_logger(__name__)

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
    "Praveen, using the ego-lite browser skill and safe local tools. "
    "Only browse validated http(s) URLs. If the ego-lite browser skill is not "
    "installed yet, stop and report that the skill is missing so the master can "
    "decide whether to build it. "
    "Drive the browser one action per ego_lite_browse_use call: start with "
    "action='load' on the site's landing URL, read the snapshot, then click "
    "links (action='click'), fill inputs (action='fill'), and pick dropdowns "
    "(action='select') until the page shows what the master asked for; then "
    "stop and summarize. The browser page stays open between calls, so act on "
    "the current page. When you need to find a site, load a search url like "
    "https://www.google.com/search?q=<topic> and read the results — do not guess "
    "the hostname. Pass an action selector exactly as shown in the snapshot's "
    "loc=... fragment (e.g. selector='loc=css:a[aria-label=\"Services\"]') or a "
    "plain CSS/text selector — never paste a whole snapshot line. If text=... "
    "matches many nodes, disambiguate with a more specific loc=css:... selector. "
    "Snapshot refs like @5 are only valid within the same call — for actions on "
    "the open page use the stable loc=... / CSS / text "
    "(e.g. 'loc=css:a[aria-label=x]', 'a[href*=...]', 'text=...'). External "
    "links rarely navigate in-place, so when the snapshot shows the target on "
    "another host (or a click leaves the url unchanged) navigate straight to it "
    "with action='load' using that snapshot url. Do not guess deep URLs — read "
    "them from snapshots. When a page shows a CAPTCHA, a login/OTP prompt, or "
    "required form fields, STOP guessing — the result includes 'need_human' "
    "and the browser window is handed to the master on that page. Call "
    "'ask_master' to let Praveen solve it or provide the details (e.g. 'I hit a "
    "CAPTCHA on <url>, please solve it in the open browser and confirm'), and "
    "once they answer, resume the same page. If a browse call returns "
    "'paused', the tab is parked under Praveen's control from an earlier "
    "hand-off — call 'ask_master' to confirm he is done with it (e.g. 'I've "
    "finished with the tab, please continue'), and once he confirms resume "
    "with action='claim' (this reclaims the space and snapshots it), then "
    "keep acting normally. Never invent passwords, OTPs, or "
    "personal details. Stop as soon as the page clearly shows the result; do "
    "not keep clicking for no reason. Never access unrelated or private "
    "information. IMPORTANT — claim and release: the browser task space is "
    "claimed by pico for the whole user goal; once the goal is complete and "
    "you have the answer, ALWAYS end the browser task by calling "
    "ego_lite_browse_use with action='release' exactly once (no url needed). "
    "That closes the pico-owned task space and frees the browser claim for the "
    "next task. Never release while a hand-off is pending (the result had "
    "'need_human' or 'paused') — the master still owns the tab. Remember what "
    "you find: record useful facts with " "memory_remember, keep session notes with memory_note, capture completed "
    "research with memory_episode, and store important findings with "
    "semantic_remember for later recall by meaning."
)


def _researcher_tools() -> List[Dict[str, Any]]:
    """Return the researcher's browser-focused tool schemas."""
    return [s for s in openai_tool_schemas() if s["function"]["name"] in RESEARCHER_TOOL_NAMES]


class Researcher(BaseAgent):
    """Sub-agent that researches via the ego-lite browser skill."""

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
        return _RESEARCHER_SYSTEM_PROMPT

    def browsable(self) -> bool:
        """Return True when the ego-lite browser skill is present."""
        return "ego-lite-browser-use" in list_skills()

    def research(self, url: str, task: str = "Retrieve and summarize this page.") -> str:
        """Attempt one browser task and summarize the outcome."""
        if not self.browsable():
            return (
                "The ego-lite browser skill is not installed yet, so I cannot browse "
                f"{url!r}. Please ask the master whether to wire the browser skill up."
            )
        return self.run(f"{task}\nURL to visit: {url}")