"""Ego-lite browser automation tool.

Visits websites through the ``ego-browser`` CLI (the connection layer of the
ego-lite browser). It runs a small Node script in the ego-lite embedded runtime:
open a task space, navigate the first page, snapshot it, and finish. A task space
is isolated from Praveen's own tabs, and the page snapshot is returned as text.

The bridge is safe by construction:

- Only validated http(s) URLs with a hostname are accepted.
- The input is never handed to a shell; the script is passed via argv.
- The output is truncated to ``max_chars`` so a huge page cannot flood context.
"""

import json
import shutil
import subprocess
from typing import Dict, Optional
from urllib.parse import urlparse

from brain.logging_setup import get_logger

logger = get_logger(__name__)

ALLOWED_SCHEMES = {"http", "https"}

EGO_BROWSER_BIN = "ego-browser"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_CHARS = 6000

_SNAPSHOT_BEGIN = "---SNAPSHOT-BEGIN---"
_SNAPSHOT_END = "---SNAPSHOT-END---"


def _is_safe_url(url: str) -> bool:
    """Return True when the URL is a valid http(s) URL with a hostname."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ALLOWED_SCHEMES and bool(parsed.netloc)


def browser_available() -> bool:
    """Return True when the ego-browser CLI is on PATH."""
    return shutil.which(EGO_BROWSER_BIN) is not None


def _script_for(url: str, timeout_ms: int) -> str:
    """Build the ego-lite Node script for one navigation + snapshot."""
    url_literal = json.dumps(url)
    return (
        "(async () => {\n"
        "  const task = await taskSpace('pico: visit ' + "
        + url_literal
        + ");\n"
        "  const page = task.page('p1');\n"
        "  try {\n"
        "    await page.goto("
        + url_literal
        + ", { timeout: "
        + str(timeout_ms)
        + " });\n"
        "  } catch (e) {\n"
        "    console.log('NAV_ERROR: ' + (e && e.message ? e.message : e));\n"
        "  }\n"
        "  console.log('TITLE: ' + (await page.title()));\n"
        "  console.log('FINAL_URL: ' + (await page.url()));\n"
        "  console.log('"
        + _SNAPSHOT_BEGIN
        + "');\n"
        "  console.log(await page.snapshot());\n"
        "  console.log('"
        + _SNAPSHOT_END
        + "');\n"
        "  await task.finish({ keep: [] });\n"
        "})();"
    )


def _run_browser_script(script: str, timeout: int) -> subprocess.CompletedProcess:
    """Run a script in the ego-browser embedded runtime (no shell)."""
    return subprocess.run(
        [EGO_BROWSER_BIN, "nodejs", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def browse(
    url: str,
    action: str = "load",
    timeout: Optional[int] = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Dict[str, object]:
    """Visit a website via the ego-lite browser and return its snapshot as text.

    ``action`` is reserved for future behaviors (``search``/``extract`` pass the
    search or target URL as ``url``); navigation + snapshot applies to all.
    """
    if not _is_safe_url(url):
        logger.warning(f"[bold red]Unsafe URL refused[/bold red]: {url!r}")
        return {"ok": False, "url": url, "error": "only http(s) URLs with a hostname are allowed"}

    if not browser_available():
        logger.warning("[bold yellow]ego-browser CLI missing[/bold yellow] (install ego lite)")
        return {
            "ok": False,
            "url": url,
            "error": "ego-browser CLI is not installed; install ego lite first",
        }

    script = _script_for(url, timeout_ms=int((timeout or DEFAULT_TIMEOUT) * 1000))
    try:
        proc = _run_browser_script(script, timeout=timeout or DEFAULT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "url": url, "error": f"browser timeout after {timeout or DEFAULT_TIMEOUT}s"}
    except OSError as exc:
        return {"ok": False, "url": url, "error": f"could not start ego-browser: {exc}"}

    combined = proc.stdout + "\n" + proc.stderr
    title, final_url = extract_metadata(combined)
    snapshot = extract_snapshot(combined, max_chars=max_chars)
    nav_error = extract_nav_error(combined)
    if not snapshot and not title:
        return {
            "ok": False,
            "url": url,
            "error": "ego-browser produced no usable output",
            "nav_error": nav_error,
            "stderr": proc.stderr.strip()[:max_chars],
        }
    logger.info(f"[bold green]Browsed[/bold green]: {url} -> {title or final_url}")
    return {
        "ok": True,
        "url": url,
        "final_url": final_url or url,
        "title": title,
        "nav_error": nav_error,
        "result": snapshot,
    }


def extract_metadata(stdout: str) -> tuple:
    """Return (title, final_url) lines found in ego-browser stdout."""
    title, final_url = "", ""
    for line in stdout.splitlines():
        if line.startswith("TITLE:"):
            title = line[len("TITLE:"):].strip()
        elif line.startswith("FINAL_URL:"):
            final_url = line[len("FINAL_URL:"):].strip()
    return title, final_url


def extract_nav_error(stdout: str) -> Optional[str]:
    """Return any NAV_ERROR text found in ego-browser stdout."""
    for line in stdout.splitlines():
        if line.startswith("NAV_ERROR:"):
            return line[len("NAV_ERROR:"):].strip()
    return None


def extract_snapshot(stdout: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Return the snapshot block between markers, truncated to max_chars."""
    before = stdout.find(_SNAPSHOT_BEGIN)
    if before == -1:
        return ""
    start = before + len(_SNAPSHOT_BEGIN)
    end = stdout.find(_SNAPSHOT_END, start)
    if end == -1:
        end = len(stdout)
    snapshot = stdout[start:end].strip("\n")
    if len(snapshot) > max_chars and max_chars > 0:
        snapshot = snapshot[:max_chars] + "\n...[truncated by pico]"
    return snapshot