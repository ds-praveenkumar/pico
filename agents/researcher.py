"""Research sub-agent for web and browser tasks via the ego-lite browser skill."""

from typing import Any

from brain.logging_setup import get_logger

from agents.base_agent import BaseAgent
from agents.tools.skill_read import list_skills

logger = get_logger(__name__)

_RESEARCHER_SYSTEM_PROMPT = (
    "You are pico's researcher. You find and verify information for the master, "
    "Praveen, using the ego-lite browser skill and safe local tools. "
    "Only browse validated http(s) URLs. If the ego-lite browser skill is not "
    "installed yet, stop and report that the skill is missing so the master can "
    "decide whether to build it. Never access unrelated or private information. "
    "Remember what you find: record useful facts with memory_remember, keep "
    "session notes with memory_note, capture completed research with "
    "memory_episode, and store important findings with semantic_remember for "
    "later recall by meaning."
)


class Researcher(BaseAgent):
    """Sub-agent that researches via the ego-lite browser skill."""

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