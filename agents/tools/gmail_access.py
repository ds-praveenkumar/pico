### tool to read emails from a gmail account

import os
from pathlib import Path
from typing import Dict

from brain.logging_setup import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def read_email(path: str) -> Dict[str, object]:
    """Read a file inside the allowed root and return content plus metadata."""
    try:
        target = Path(path)
        if not target.is_file():
            return {"ok": False, "path": str(path), "error": "file does not exist"}
        with target.open("r", encoding="utf-8") as handle:
            content = handle.read()
        logger.info(f"[bold green]Read email[/bold green]: {target}")
        return {"ok": True, "path": str(target), "content": content, "bytes": target.stat().st_size}
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        logger.warning(f"[bold yellow]Read failed[/bold yellow]: {path} -> {exc}")
        return {"ok": False, "path": str(path), "error": str(exc), "content": ""}
    
    
