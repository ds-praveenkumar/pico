"""Skill reader tool.

Lists skills available in the skills directory and returns the raw YAML content
for a named skill. Uses stdlib only (no PyYAML dependency).
"""

import os
from pathlib import Path
from typing import Dict, List, Optional

from brain.logging_setup import get_logger
from brain.config import personalize

logger = get_logger(__name__)

SKILLS_DIR = Path(__file__).resolve().parents[2] / "agents" / "skills"


def list_skills() -> List[str]:
    """Return names of every skill directory under agents/skills/."""
    skills: List[str] = []
    if not SKILLS_DIR.is_dir():
        return skills
    for entry in sorted(SKILLS_DIR.iterdir()):
        if entry.is_dir():
            skills.append(entry.name)
    return skills


def read_skill(skill_name: str) -> Dict[str, object]:
    """Return the raw content of a skill YAML file, keyed by skill name."""
    skill_dir = SKILLS_DIR / skill_name
    if not skill_dir.is_dir():
        available = list_skills()
        logger.warning(f"[bold yellow]Skill not found[/bold yellow]: {skill_name} (available: {available})")
        return {"ok": False, "skill": skill_name, "error": "skill directory not found", "available": available}
    candidates = [f for f in skill_dir.iterdir() if f.suffix in (".yml", ".yaml")]
    if not candidates:
        return {"ok": False, "skill": skill_name, "error": "no .yml/.yaml file found in skill directory"}
    target = candidates[0]
    content = personalize(target.read_text(encoding="utf-8"))
    logger.info(f"[bold green]Read skill[/bold green]: {skill_name} -> {target.name}")
    return {"ok": True, "skill": skill_name, "path": str(target), "content": content}