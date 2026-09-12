"""Safe file write tool.

Writes text inside the project root (or an explicitly approved path) only.
Never deletes: this module has no remove/unlink capability by design.
"""

import os
from pathlib import Path
from typing import Dict, Optional

from brain.logging_setup import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_within_root(path: Path, root: Path) -> Path:
    """Resolve path and guarantee it stays inside root; raise ValueError otherwise."""
    candidate = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes allowed root: {candidate}")
    return candidate


def write_file(path: str, content: str, root: Optional[Path] = None) -> Dict[str, object]:
    """Write content to a file inside the allowed root; refuse anything else."""
    root = root or PROJECT_ROOT
    try:
        target = _resolve_within_root(Path(path), root)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            handle.write(content)
        logger.info(f"[bold green]Wrote file[/bold green]: {target}")
        return {"ok": True, "path": str(target), "bytes": len(content.encode("utf-8"))}
    except (ValueError, OSError) as exc:
        logger.warning(f"[bold yellow]Write failed[/bold yellow]: {path} -> {exc}")
        return {"ok": False, "path": str(path), "error": str(exc)}