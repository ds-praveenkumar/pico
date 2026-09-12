"""Sandboxed shell execution for pico tools.

Commands run in a subprocess with a scrubbed environment (no API keys or secrets,
a minimal PATH that still finds the project venv) and, on POSIX, hard resource
limits applied via ``setrlimit``: CPU seconds, address space, file descriptors,
and process count. This contains shell work without needing a container runtime.
Network is intentionally not restricted, so the caller's allowlist and path
confinement still apply.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from brain.logging_setup import get_logger

logger = get_logger(__name__)

CPU_LIMIT_SECONDS = 45
MEM_LIMIT_BYTES = 2 * 1024**3  # 2 GiB address space
FD_LIMIT = 256
NPROC_LIMIT = 128

_ALLOWED_ENV_KEYS = ("PATH", "LANG", "TERM", "PYTHONUNBUFFERED", "TMPDIR", "TZ", "VIRTUAL_ENV")


def _posix_limits() -> None:
    """Apply resource limits for the child process (POSIX only)."""
    try:
        import resource
    except ImportError:
        return

    def set_limit(kind: int, value: int) -> None:
        try:
            resource.setrlimit(kind, (value, value))
        except (OSError, ValueError):
            pass

    set_limit(resource.RLIMIT_CPU, CPU_LIMIT_SECONDS)
    set_limit(resource.RLIMIT_AS, MEM_LIMIT_BYTES)
    set_limit(resource.RLIMIT_NOFILE, FD_LIMIT)
    if hasattr(resource, "RLIMIT_NPROC"):
        set_limit(resource.RLIMIT_NPROC, NPROC_LIMIT)


def _default_path(cwd: str) -> str:
    """Build a minimal PATH that still includes the project/venv binaries."""
    parts = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    venv = os.environ.get("VIRTUAL_ENV", "")
    if venv:
        parts.insert(0, os.path.join(venv, "bin"))
    venv_bin = Path(cwd) / ".venv" / "bin"
    if venv_bin.is_dir():
        parts.insert(0, str(venv_bin))
    return ":".join(parts)


def sandboxed_env(cwd: str) -> Dict[str, str]:
    """Return a cleaned environment for sandboxed children: no secrets, working HOME."""
    env: Dict[str, str] = {}
    for key in _ALLOWED_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    env.setdefault("PATH", _default_path(cwd))
    env["HOME"] = str(cwd)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run(command: List[str], timeout: int = 30, cwd: Optional[str] = None) -> Dict[str, object]:
    """Run an argv command inside the sandbox and return a summary dict.

    Returns the same shape as :func:`bash.run_command` so callers are unchanged.
    """
    workdir = cwd or os.getcwd()
    env = sandboxed_env(workdir)
    preexec = None if sys.platform == "win32" else _posix_limits
    try:
        proc = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=workdir,
            env=env,
            preexec_fn=preexec,
            close_fds=True,
        )
        logger.info(f"[bold green]Sandbox ran[/bold green]: {command[0]}")
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": proc.stdout,
            "error": proc.stderr,
            "sandboxed": True,
        }
    except subprocess.TimeoutExpired:
        logger.warning(f"[bold yellow]Sandboxed command timed out[/bold yellow]: {command}")
        return {
            "ok": False,
            "returncode": -1,
            "error": f"command timed out after {timeout}s",
            "output": "",
            "sandboxed": True,
        }
    except OSError as exc:
        logger.error(f"[bold red]Sandboxed command failed[/bold red]: {command[0]} -> {exc}")
        return {"ok": False, "returncode": -1, "error": str(exc), "output": "", "sandboxed": True}