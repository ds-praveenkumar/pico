"""Ego-lite browser automation tool.

Drives websites through the ``ego-browser`` CLI (the connection layer of the
ego-lite browser) by running a small Node script in the ego-lite embedded
runtime. The script opens a persistent Agent-owned task space, navigates one
Page, optionally performs a single interaction (click, fill, or select), and
snapshots the result as text.

The bridge is safe by construction:

- Only validated http(s) URLs with a hostname are accepted for navigation.
- Inputs are never handed to a shell; the script is passed via argv and every
  interpolated value is JSON-encoded.
- The page snapshot is truncated to ``max_chars`` so a huge page cannot flood
  context.
- The task space is claimed for the whole user goal and kept alive across
  calls: a click, fill, or select in one call is the starting state for the
  next. Only a ``release`` action (``task.finish({ keep: [] })``) closes the
  agent's task space and frees the browser claim for the next task, and only a
  hand-off (``task.handOff``) hands control to the master mid-goal.

Human-in-the-loop: when the page shows a CAPTCHA, a login/OTP prompt, or a
required form, the script hands the space off to the master with
``task.handOff()`` (the ego-lite browser window stays open on that page) and
reports ``need_human`` instead of guessing. The next call resumes the same
space with ``takeOverTaskSpace``/``claimTaskSpace``.

Actions: ``load`` (default) navigates and snapshots; ``click`` operates on
``selector``; ``fill`` types ``query`` into ``selector``; ``select`` selects the
``query`` option in the ``selector`` dropdown; ``claim`` re-claims a
master-owned (handed-off) task space after the master confirms they are done
with the tab, then acts like ``load``; ``release`` finishes the agent-owned task
space so the browser claim is freed for the next task (never closes a space the
master is currently using). Selectors accept the refs and
locators shown in the previous snapshot (``@6``, ``ref=6``, ``loc=css:...``,
``loc=role:button[name='...']``, or CSS).
"""

import json
import platform
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
TASK_SPACE_NAME = "pico: research"

_ACTION_TIMEOUT_MS = 6000

_SNAPSHOT_BEGIN = "---SNAPSHOT-BEGIN---"
_SNAPSHOT_END = "---SNAPSHOT-END---"

# Snapshot keyword hints used (in the Node script and in tests) to decide when
# the page needs the master instead of another guessed click.
_CAPTCHA_HINTS = (
    "captcha",
    "recaptcha",
    "re-captcha",
    "verify you are human",
    "are you human",
    "prove you are not a robot",
    "security check",
    "safety check",
    "enter the characters",
    "type the characters",
    "confirm you are human",
)
_LOGIN_HINTS = (
    "sign in",
    "sign-in",
    "log in",
    "login",
    "password",
    "two-factor",
    "enter the code",
    "verification code",
    "one-time password",
    "enter your credentials",
    "verify your identity",
    "otp",
    "authentication required",
)
_FORM_HINTS = (
    "is required",
    "this field is required",
    "mandatory field",
    "all fields are required",
    "please fill",
    "please enter",
)
_INPUT_ROLE_RE = r"(textbox|combobox|password|checkbox|radio button|textfield|input)"

_HUMAN_MESSAGES = {
    "captcha": (
        "CAPTCHA or bot-check detected — pico cannot solve it. Ask the master "
        "through 'ask_master'; the ego-lite browser window is open on this page "
        "and waiting for them to solve it. After they confirm, resume browsing."
    ),
    "login": (
        "Login, OTP, or two-factor prompt detected — pico must never guess "
        "credentials. Ask the master through 'ask_master' to sign in in the open "
        "ego-lite browser window (or provide the needed details) and confirm; "
        "then resume browsing."
    ),
    "form_input": (
        "The page is asking for details pico does not have (required form "
        "fields). Ask the master through 'ask_master' what to fill in, or have "
        "them complete it in the open ego-lite browser window; then resume."
    ),
}


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


def _raise_ego_lite_window() -> bool:
    """Bring the ego-lite window to the front so the master can see and use it.

    macOS only; best-effort and never raises. Called after a navigation or a
    hand-off so the browser visibly pops up (e.g. for a CAPTCHA) instead of
    staying hidden behind other windows.
    """
    if platform.system() != "Darwin" or shutil.which("osascript") is None:
        return False
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "ego lite" to activate'],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - focus is best-effort
        logger.debug("could not raise the ego-lite window: %s", exc)
        return False


def _js_selector(selector: str) -> str:
    """Normalize selector input into something the browser script can act on."""
    import re

    value = (selector or "").strip()
    if value.isdigit():
        return "@" + value
    match = re.fullmatch(r"ref=(\d+)", value)
    if match:
        return "@" + match.group(1)
    loc = re.search(r"loc=(?:css|role|href):[^\s,]+", value)
    if loc:
        return loc.group(0)
    ref = re.search(r"ref=(\d+)", value)
    if ref:
        return "@" + ref.group(1)
    return value


def _literal(value: str) -> str:
    """JSON-encode a string as a JS literal."""
    return json.dumps(value or "")


def _script_for(
    url: str,
    action: str,
    selector: Optional[str],
    query: Optional[str],
    timeout_ms: int,
) -> str:
    """Build the ego-lite Node script for one navigate/act/release + snapshot round.

    The script reuses the durable task space ``TASK_SPACE_NAME``: it resumes
    (``takeOverTaskSpace``, falling back to ``claimTaskSpace``) and adopts the
    previously kept tab as ``p1`` when the space already exists, and creates it
    (with a fresh ``p1``) on the first call. After snapshotting, a heuristic
    scans the page text for CAPTCHA/login/required-form hints; when found the
    script hands the space off to the master (``task.handOff()``) and prints
    ``NEED_HUMAN:<kind>``, otherwise the space stays agent-owned for the next
    round. For ``action='release'`` the script instead closes the agent-owned
    task space with ``task.finish({ keep: [] })`` (printing ``RELEASED:true``)
    so the browser claim is freed for the next task; a space currently under the
    master's control is never closed (``RELEASE_SKIPPED``).
    """
    sel = _js_selector(selector or "")
    claim_round = action == "claim"
    release_round = action == "release"
    lines = [
        "(async () => {",
        f"  const NAME = {_literal(TASK_SPACE_NAME)};",
        f"  const CLAIM_ROUND = {'true' if claim_round else 'false'};",
        f"  const RELEASE_ROUND = {'true' if release_round else 'false'};",
        "  const CAPTCHA_KW = " + json.dumps(list(_CAPTCHA_HINTS)) + ";",
        "  const LOGIN_KW = " + json.dumps(list(_LOGIN_HINTS)) + ";",
        "  const FORM_KW = " + json.dumps(list(_FORM_HINTS)) + ";",
        "  function kindFor(text) {",
        "    const t = (text || '').toLowerCase();",
        "    const hasInput = /" + _INPUT_ROLE_RE + "/.test(t);",
        "    if (CAPTCHA_KW.some(k => t.includes(k))) return 'captcha';",
        "    if (LOGIN_KW.some(k => t.includes(k)) && hasInput) return 'login';",
        "    if (FORM_KW.some(k => t.includes(k)) && hasInput) return 'form_input';",
        "    return null;",
        "  }",
        "  let task;",
        "  let page;",
        "  let paused = false;",
        "  const existing = (await listTaskSpaces()).find(s => s.name === NAME);",
        "  if (RELEASE_ROUND) {",
        "    if (existing && existing.ownership === 'user') {",
        "      console.log('RELEASE_SKIPPED: the master currently owns this task space; pico does not close a tab that is under the master\\'s control.');",
        "    } else if (existing) {",
        "      try {",
        "        task = await takeOverTaskSpace(existing.id);",
        "        await task.finish({ keep: [] });",
        "        console.log('RELEASED:true');",
        "      } catch (e) {",
        "        console.log('RELEASE_ERROR: ' + (e && e.message ? e.message : e));",
        "      }",
        "    } else {",
        "      console.log('RELEASED:true');",
        "    }",
        "    return;",
        "  }",
        "  if (existing && !CLAIM_ROUND && existing.ownership === 'user') {",
        "    paused = true;",
        "    console.log('SESSION_PAUSED: the master currently owns this task space (it was handed off to them); browser commands are paused until they continue. Never claim a user-owned space on your own: ask the master through ask_master whether they are done with the tab, and resume with action=claim only after they say to continue.');",
        "  } else if (existing) {",
        "    try {",
        "      if (existing.ownership === 'user' || CLAIM_ROUND) {",
        "        try { task = await claimTaskSpace(existing.id); }",
        "        catch (e) { task = await takeOverTaskSpace(existing.id); }",
        "      } else {",
        "        try { task = await takeOverTaskSpace(existing.id); }",
        "        catch (e) { task = await claimTaskSpace(existing.id); }",
        "      }",
        "      const tabs = await task.tabs();",
        "      const active = tabs.find(t => t.active) || tabs[0];",
        "      if (active && !active.label) {",
        "        page = await task.adopt(active.page, { as: 'p1' });",
        "      } else if (active && active.label) {",
        "        page = task.page(active.label);",
        "      } else {",
        "        page = task.page('p1');",
        "      }",
        "    } catch (e) {",
        "      console.log('SESSION_ERROR: ' + (e && e.message ? e.message : e));",
        "      task = await taskSpace(NAME + ': ' + Date.now());",
        "      page = task.page('p1');",
        "    }",
        "  } else {",
        "    task = await taskSpace(NAME);",
        "    page = task.page('p1');",
        "  }",
        "  if (!paused) {",
    ]
    if url:
        lines += [
            "  try {",
            f"    await page.goto({_literal(url)}, {{ timeout: {timeout_ms} }});",
            "  } catch (e) {",
            "    console.log('NAV_ERROR: ' + (e && e.message ? e.message : e));",
            "  }",
            "  await page.waitForLoadState('load').catch(() => {});",
        ]
    action = (action or "load").lower()
    if action == "click" and sel:
        lines += [
            "  try {",
            f"    await page.click({_literal(sel)}, {{ label: 'pico click', timeout: "
            + str(_ACTION_TIMEOUT_MS)
            + " });",
            "  } catch (e) {",
            "    console.log('ACTION_ERROR: ' + (e && e.message ? e.message : e));",
            "  }",
        ]
    elif action == "fill" and sel:
        lines += [
            "  try {",
            f"    await page.fill({_literal(sel)}, {_literal(query or '')}, "
            "{ clearFirst: true, timeout: "
            + str(_ACTION_TIMEOUT_MS)
            + " });",
            "  } catch (e) {",
            "    console.log('ACTION_ERROR: ' + (e && e.message ? e.message : e));",
            "  }",
        ]
    elif action == "select" and sel:
        lines += [
            "  try {",
            f"    await page.selectOption({_literal(sel)}, {_literal(query or '')}, "
            "{ timeout: "
            + str(_ACTION_TIMEOUT_MS)
            + " });",
            "  } catch (e) {",
            "    console.log('ACTION_ERROR: ' + (e && e.message ? e.message : e));",
            "  }",
        ]
    lines += [
        "  await page.waitForTimeout(400).catch(() => {});",
        "  const snapText = await page.snapshot({ includeStableLocator: true });",
        "  console.log('TITLE: ' + (await page.title()));",
        "  console.log('FINAL_URL: ' + (await page.url()));",
        "  console.log('" + _SNAPSHOT_BEGIN + "');",
        "  console.log(snapText);",
        "  console.log('" + _SNAPSHOT_END + "');",
        "  const need = kindFor(snapText || '');",
        "  if (need) {",
        "    await task.handOff();",
        "    console.log('NEED_HUMAN:' + need);",
        "  } else if (CLAIM_ROUND) {",
        "    console.log('CLAIMED:true');",
        "  }",
        "  }",
        "})();",
    ]
    return "\n".join(lines)


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
    url: str = "",
    action: str = "load",
    selector: Optional[str] = None,
    query: Optional[str] = None,
    timeout: Optional[int] = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Dict[str, object]:
    """Act in the ego-lite browser and return the page snapshot as text.

    ``action`` selects what happens this round: ``load`` navigates to ``url``;
    ``click`` clicks ``selector``; ``fill`` types ``query`` into ``selector``;
    ``select`` chooses the ``query`` option inside the ``selector`` dropdown;
    ``claim`` re-takes a handed-off (master-owned) space after the master says
    they are done, then acts like ``load``; ``release`` finishes the agent-owned
    task space so the browser claim is freed for the next task. The task space
    persists between calls, so later rounds can act on ``url`` loaded earlier
    without re-navigating.
    """
    if url and not _is_safe_url(url):
        logger.warning(f"[bold red]Unsafe URL refused[/bold red]: {url!r}")
        return {"ok": False, "url": url, "error": "only http(s) URLs with a hostname are allowed"}

    if not browser_available():
        logger.warning("[bold yellow]ego-browser CLI missing[/bold yellow] (install ego lite)")
        return {
            "ok": False,
            "url": url,
            "error": "ego-browser CLI is not installed; install ego lite first",
        }

    timeout_s = timeout or DEFAULT_TIMEOUT
    script = _script_for(url, action, selector, query, timeout_ms=int(timeout_s * 1000))
    try:
        proc = _run_browser_script(script, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "url": url, "error": f"browser timeout after {timeout_s}s"}
    except OSError as exc:
        return {"ok": False, "url": url, "error": f"could not start ego-browser: {exc}"}

    combined = proc.stdout + "\n" + proc.stderr
    release_round = (action or "load").lower() == "release"
    title, final_url = extract_metadata(combined)
    snapshot = extract_snapshot(combined, max_chars=max_chars)
    nav_error = extract_nav_error(combined)
    action_error = extract_action_error(combined)
    session_error = extract_session_error(combined)
    paused_error = extract_session_paused(combined)
    kind = extract_need_human(combined)
    if release_round:
        if extract_released(combined):
            logger.info("[bold green]Browser released[/bold green] (task space closed; claim freed for the next task)")
            return {"ok": True, "url": url, "action": "release", "released": True}
        release_error = extract_release_error(combined)
        if release_error:
            logger.warning(f"[bold red]Browser release failed[/bold red]: {release_error}")
            return {"ok": False, "url": url, "action": "release", "error": release_error}
        skipped = extract_release_skipped(combined) or "no browser task space to release"
        logger.info(f"[bold yellow]Browser release skipped[/bold yellow]: {skipped}")
        return {"ok": True, "url": url, "action": "release", "released": False, "skipped_reason": skipped}
    if paused_error:
        _raise_ego_lite_window()
        logger.warning("[bold yellow]Browser session paused[/bold yellow] (master owns the tab)")
        return {
            "ok": False,
            "paused": True,
            "url": url,
            "error": paused_error,
            "hint": "The tab is parked under the master's control. Ask the master "
            "through 'ask_master' whether they are done with it; once they say "
            "to continue, resume with action='claim' (this reclaims the space "
            "and snapshots the page).",
        }
    if not snapshot and not title:
        return {
            "ok": False,
            "url": url,
            "error": "ego-browser produced no usable output",
            "nav_error": nav_error,
            "action_error": action_error,
            "session_error": session_error,
            "stderr": proc.stderr.strip()[:max_chars],
        }
    result: Dict[str, object] = {
        "ok": True,
        "url": url,
        "final_url": final_url or url,
        "title": title,
        "nav_error": nav_error,
        "action_error": action_error,
        "session_error": session_error,
        "action": action,
        "result": snapshot,
    }
    if extract_claimed(combined):
        result["claimed"] = True
    if kind:
        _raise_ego_lite_window()
        result["need_human"] = {
            "kind": kind,
            "message": _HUMAN_MESSAGES.get(kind, "The page needs a human."),
            "hint": "Stop guessing. Ask the master through 'ask_master'. The "
            "ego-lite browser window is open on this page and under their "
            "control until pico resumes it.",
        }
        logger.warning(f"[bold yellow]Browser needs the master[/bold yellow]: {kind}")
    else:
        if url:
            _raise_ego_lite_window()
        logger.info(f"[bold green]Browsed[/bold green]: {url or title or final_url} -> {title}")
    return result


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


def extract_action_error(stdout: str) -> Optional[str]:
    """Return any ACTION_ERROR text found in ego-browser stdout."""
    for line in stdout.splitlines():
        if line.startswith("ACTION_ERROR:"):
            return line[len("ACTION_ERROR:"):].strip()
    return None


def extract_session_error(stdout: str) -> Optional[str]:
    """Return any SESSION_ERROR text found in ego-browser stdout."""
    for line in stdout.splitlines():
        if line.startswith("SESSION_ERROR:"):
            return line[len("SESSION_ERROR:"):].strip()
    return None


def extract_session_paused(stdout: str) -> Optional[str]:
    """Return the SESSION_PAUSED notice when the master owns the task space."""
    for line in stdout.splitlines():
        if line.startswith("SESSION_PAUSED:"):
            return line[len("SESSION_PAUSED:"):].strip()
    return None


def extract_claimed(stdout: str) -> bool:
    """Return True when a claim round re-took the master-owned space."""
    return any(line.startswith("CLAIMED:") for line in stdout.splitlines())


def extract_released(stdout: str) -> bool:
    """Return True when a release round closed the agent-owned task space."""
    return any(
        line.startswith("RELEASED:")
        and line[len("RELEASED:"):].strip().lower() == "true"
        for line in stdout.splitlines()
    )


def extract_release_error(stdout: str) -> Optional[str]:
    """Return any RELEASE_ERROR text found in ego-browser stdout."""
    for line in stdout.splitlines():
        if line.startswith("RELEASE_ERROR:"):
            return line[len("RELEASE_ERROR:"):].strip()
    return None


def extract_release_skipped(stdout: str) -> Optional[str]:
    """Return a RELEASE_SKIPPED reason when the space stays under the master."""
    for line in stdout.splitlines():
        if line.startswith("RELEASE_SKIPPED:"):
            return line[len("RELEASE_SKIPPED:"):].strip()
    return None


def extract_need_human(stdout: str) -> Optional[str]:
    """Return the NEED_HUMAN kind (captcha/login/form_input) or None."""
    for line in stdout.splitlines():
        if line.startswith("NEED_HUMAN:"):
            return line[len("NEED_HUMAN:"):].strip()
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