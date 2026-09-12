"""Safe file read tool.

Only reads files inside the project root (or an explicitly approved path).
Resolves symlinks and rejects any path that escapes the allowed directory.
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


def read_file(path: str, root: Optional[Path] = None, limit: Optional[int] = None) -> Dict[str, object]:
    """Read a file inside the allowed root and return content plus metadata."""
    root = root or PROJECT_ROOT
    try:
        target = _resolve_within_root(Path(path), root)
        if not target.is_file():
            return {"ok": False, "path": str(path), "error": "file does not exist"}
        with target.open("r", encoding="utf-8") as handle:
            if limit is not None:
                content = "".join(handle.readlines()[:limit])
            else:
                content = handle.read()
        logger.info(f"[bold green]Read file[/bold green]: {target}")
        return {
            "ok": True,
            "path": str(target),
            "content": content,
            "bytes": target.stat().st_size,
        }
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        logger.warning(f"[bold yellow]Read failed[/bold yellow]: {path} -> {exc}")
        return {"ok": False, "path": str(path), "error": str(exc), "content": ""}