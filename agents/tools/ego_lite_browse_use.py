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
required form, the script saves the CAPTCHA image — and the audio CAPTCHA when
the page exposes one — to a timestamped file so the master can read or hear it
even though the browser is headless, then hands the space off with
``task.handOff()`` and reports ``need_human``. The hand-off is self-resuming:
the tool announces what the master must do via ``notify_master``, then waits in
place with ``task.waitForControl`` (up to ``PICO_BROWSER_HANDOFF_TIMEOUT``
seconds, default 600; disable the wait with ``PICO_BROWSER_AWAIT_HUMAN=0``).
When control returns, the script reclaims the space, submits the form itself
(CAPTCHA, OTP, and required-form pages only — login pages stay read-only;
``PICO_BROWSER_AUTO_SUBMIT=0`` disables this), and snapshots the result page,
so the result carries ``resumed``/``submitted`` and the master never needs a
second instruction. If the wait times out the result is ``paused`` (with
``handoff_timeout``/``awaiting_human``) and the tab stays open under the
master's control: the agent asks via ``ask_master`` and reclaims the space
later with ``takeOverTaskSpace``/``claimTaskSpace`` (``action='claim'``),
never re-triggering a hand-off on the same page.

Actions: ``load`` (default) navigates and snapshots; ``click`` operates on
``selector``; ``fill`` types ``query`` into ``selector``; ``select`` selects the
``query`` option in the ``selector`` dropdown; ``press`` sends the ``query`` key
(default Enter) to submit a form; ``screenshot`` saves a full-page screenshot to
a timestamped file; ``claim`` re-claims a
master-owned (handed-off) task space after the master confirms they are done
with the tab, then acts like ``load``; ``release`` finishes the agent-owned task
space so the browser claim is freed for the next task (never closes a space the
master is currently using). Selectors accept the refs and
locators shown in the previous snapshot (``@6``, ``ref=6``, ``loc=css:...``,
``loc=role:button[name='...']``, or CSS).

When a page needs a human (CAPTCHA/login/required form) the script saves the
CAPTCHA image — and the audio CAPTCHA when the page exposes one — to a
timestamped file so the master can read or hear it even though the browser is
headless, then hands the space off and reports ``need_human`` with the saved
paths. Files go to the current working directory unless ``PICO_ARTIFACTS_DIR``
overrides it.
"""

import json
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from brain.logging_setup import get_logger

from agents.tools.ask import notify_master

logger = get_logger(__name__)

ALLOWED_SCHEMES = {"http", "https"}

EGO_BROWSER_BIN = "ego-browser"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_CHARS = 6000
TASK_SPACE_NAME = "pico: research"

_ACTION_TIMEOUT_MS = 6000
_ARTIFACTS_ENV = "PICO_ARTIFACTS_DIR"

_AWAIT_HUMAN_ENV = "PICO_BROWSER_AWAIT_HUMAN"
_HANDOFF_TIMEOUT_ENV = "PICO_BROWSER_HANDOFF_TIMEOUT"
_AUTO_SUBMIT_ENV = "PICO_BROWSER_AUTO_SUBMIT"

DEFAULT_HANDOFF_TIMEOUT = 600
_HANDOFF_TIMEOUT_MIN = 30
_HANDOFF_TIMEOUT_MAX = 7200
_WAIT_POLL_MS = 1000

_AUTO_SUBMIT_KINDS = ("captcha", "form_input", "otp")
_SUBMIT_LABEL_RE = "submit|search|verify|continue|confirm|sign in|send|go"

_CONTROL_BACK_MARK = "CONTROL_BACK:"
_RESUMED_MARK = "RESUMED:"
_SUBMITTED_MARK = "SUBMITTED:"
_HANDOFF_TIMEOUT_MARK = "HANDOFF_TIMEOUT:"

_SNAPSHOT_BEGIN = "---SNAPSHOT-BEGIN---"
_SNAPSHOT_END = "---SNAPSHOT-END---"
_RESULT_BEGIN = "---RESULT-BEGIN---"
_RESULT_END = "---RESULT-END---"

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
_OTP_HINTS = (
    "otp",
    "one-time password",
    "one time password",
    "verification code",
    "verify code",
    "enter the code",
    "enter code",
    "type the code",
    "enter the otp",
    "code has been sent",
    "code sent to",
    "6 digit code",
    "6-digit code",
    "4 digit code",
    "4-digit code",
)
_INPUT_ROLE_RE = r"(textbox|combobox|password|checkbox|radio button|textfield|input)"

_HUMAN_MESSAGES = {
    "captcha": (
        "CAPTCHA or bot-check detected — pico cannot solve it. The ego-lite "
        "browser window is open on this page and handed to the master: ask them "
        "to type the CAPTCHA characters into the field and click the "
        "search/submit button in the open browser window themselves, then "
        "confirm. After they confirm, resume with action='claim' and read the "
        "result page."
    ),
    "otp": (
        "An OTP / one-time password is being requested — pico must never guess "
        "it. The ego-lite browser window is open on this page and handed to the "
        "master: ask them to type the code they received (SMS/email) into the "
        "field in the open browser window; pico then submits the form itself "
        "and reads the result page."
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


def _kind_for(text: str) -> Optional[str]:
    """Classify a page for CAPTCHA/OTP/login/required-form involvement (Python-side fallback)."""
    lower = (text or "").lower()
    has_input = re.search(_INPUT_ROLE_RE, lower) is not None
    has_password = "password" in lower
    if any(k in lower for k in _CAPTCHA_HINTS):
        return "captcha"
    if any(k in lower for k in _OTP_HINTS) and has_input:
        return "otp"
    if any(k in lower for k in _LOGIN_HINTS) and has_password:
        return "login"
    if any(k in lower for k in _FORM_HINTS) and has_input and ("required" in lower or "mandatory" in lower):
        return "form_input"
    return None


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


def artifacts_dir() -> Path:
    """Return the directory where browser artifacts (CAPTCHA images, screenshots) are saved.

    Defaults to the current working directory so the master can open the files
    pico writes without hunting for them; override with ``PICO_ARTIFACTS_DIR``.
    """
    override = os.getenv(_ARTIFACTS_ENV, "").strip()
    directory = Path(override).expanduser() if override else Path.cwd()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # noqa: BLE001 - fall back rather than fail the browse
        logger.debug("could not create artifacts dir %s: %s", directory, exc)
        return Path.cwd()
    return directory


def _env_flag(name: str, default: bool = True) -> bool:
    """Return a boolean environment flag; ``0``/``false``/``no``/``off`` disable it."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def _await_human_enabled() -> bool:
    """Return whether a hand-off waits in place for the master to finish."""
    return _env_flag(_AWAIT_HUMAN_ENV, True)


def _auto_submit_enabled() -> bool:
    """Return whether pico submits the form once control comes back to it."""
    return _env_flag(_AUTO_SUBMIT_ENV, True)


def handoff_timeout_seconds(override: Optional[int] = None) -> int:
    """Return how long pico waits for the master, clamped to a sane range."""
    value = override
    if value is None:
        raw = os.getenv(_HANDOFF_TIMEOUT_ENV, "").strip()
        try:
            value = int(raw) if raw else None
        except ValueError:
            value = None
    if value is None:
        value = DEFAULT_HANDOFF_TIMEOUT
    return max(_HANDOFF_TIMEOUT_MIN, min(int(value), _HANDOFF_TIMEOUT_MAX))


def _capture_paths() -> Dict[str, str]:
    """Return timestamped artifact paths for one browser round (never overwrites old files)."""
    directory = artifacts_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return {
        "captcha_image": str(directory / f"captcha-{stamp}.png"),
        "captcha_audio": str(directory / f"captcha-audio-{stamp}.wav"),
        "screenshot": str(directory / f"screenshot-{stamp}.png"),
    }


def _captcha_capture_lines(image_path: str, audio_path: str, fallback_shot: str) -> List[str]:
    """Return Node lines that save the page's CAPTCHA image (and audio CAPTCHA) to disk.

    The ego-lite browser is often headless, so the master cannot read a CAPTCHA
    on screen. The script fetches the CAPTCHA image element through
    ``page.fetch(..., {saveAs})`` (page cookies are used) and falls back to a
    full-page screenshot when no CAPTCHA image element exists. When the page
    exposes an audio CAPTCHA, that is saved too so the master can listen to it.
    """
    lines = [
        "    try {",
        "      const capSrc = await page.evaluate(() => {",
        "        const imgs = Array.from(document.querySelectorAll('img'));",
        "        const hit = imgs.find(i => /captcha/i.test((i.id || '') + ' ' + (i.alt || '') + ' ' + (i.src || '')));",
        "        return hit ? hit.src : null;",
        "      });",
        "      if (capSrc) {",
        f"        await page.fetch(capSrc, {{ saveAs: {_literal(image_path)} }});",
        f"        console.log('CAPTCHA_IMAGE:' + {_literal(image_path)});",
        "      } else {",
        f"        await page.screenshot({{ path: {_literal(fallback_shot)} }});",
        f"        console.log('CAPTCHA_IMAGE:' + {_literal(fallback_shot)});",
        "      }",
        "    } catch (e) { console.log('CAPTCHA_SAVE_ERROR: ' + (e && e.message ? e.message : e)); }",
    ]
    if audio_path:
        lines += [
            "    try {",
            "      const audioSrc = await page.evaluate(() => {",
            "        const nodes = Array.from(document.querySelectorAll('audio source, audio[src]'));",
            "        const urls = nodes.map(n => n.src || n.getAttribute('src') || '');",
            "        return urls.find(u => /play|audio|wav|captcha/i.test(u)) || null;",
            "      });",
            f"      if (audioSrc) {{ await page.fetch(audioSrc, {{ saveAs: {_literal(audio_path)} }}); console.log('CAPTCHA_AUDIO:' + {_literal(audio_path)}); }}",
            "    } catch (e) { console.log('CAPTCHA_AUDIO_ERROR: ' + (e && e.message ? e.message : e)); }",
        ]
    return lines


def _handoff_announcement(
    kind: str,
    url: str,
    captcha_image: Optional[str] = None,
    captcha_audio: Optional[str] = None,
) -> str:
    """Return the notice telling the master what to do in the handed-off window."""
    label = {
        "captcha": "a CAPTCHA",
        "otp": "an OTP / one-time password",
        "login": "a login/OTP prompt",
        "form_input": "required form fields",
    }.get(kind, "input only you can give")
    parts = [f"Browser needs the master: {label} on {url or 'the open page'}."]
    if captcha_image:
        parts.append(f"The CAPTCHA image is saved at {captcha_image}.")
    if captcha_audio:
        parts.append(f"The audio CAPTCHA is saved at {captcha_audio}.")
    parts.append(
        "Solve or fill it in the open ego-lite window and hand control back — "
        "pico will submit the form and continue by itself."
    )
    return " ".join(parts)


def _auto_submit_node_lines() -> List[str]:
    """Return the Node lines that submit the form once control is back."""
    return [
        "        let submitted = { how: 'skipped', selector: '', reason: 'not submitted' };",
        "        if (!AUTO_SUBMIT) {",
        "          submitted.reason = 'auto-submit disabled';",
        "        } else if (!SUBMIT_KINDS.includes(need)) {",
        "          submitted.reason = 'login page: read only';",
        "        } else {",
        "          try {",
        "            const nowUrl = await page.url();",
        "            const nowTitle = await page.title();",
        "            if (nowUrl !== beforeUrl || nowTitle !== beforeTitle) {",
        "              submitted.reason = 'page already advanced';",
        "            } else {",
        "              const picked = await page.evaluate((re) => {",
        "                const rx = new RegExp(re, 'i');",
        "                const text = (el) => ((el.getAttribute('aria-label') || '') + ' ' + (el.value || '') + ' ' + (el.textContent || '') + ' ' + (el.name || '') + ' ' + (el.id || '')).trim();",
        "                const nodes = Array.from(document.querySelectorAll('button, input[type=submit], input[type=button], [role=button]'));",
        "                const hit = nodes.find((el) => !el.disabled && rx.test(text(el)));",
        "                if (!hit) { return null; }",
        "                hit.setAttribute('data-pico-submit', '1');",
        "                return '[data-pico-submit=\"1\"]';",
        "              }, SUBMIT_RE);",
        "              if (picked) {",
        "                await page.click(picked, { label: 'pico submit', timeout: ACTION_MS });",
        "                submitted = { how: 'click', selector: picked, reason: 'clicked the submit control' };",
        "              } else {",
        "                await page.keyboard.press('Enter');",
        "                submitted = { how: 'enter', selector: '', reason: 'no submit control found; pressed Enter' };",
        "              }",
        "            }",
        "          } catch (e) {",
        "            submitted = { how: 'skipped', selector: '', reason: 'submit failed: ' + (e && e.message ? e.message : e) };",
        "          }",
        "        }",
        "        console.log('SUBMITTED:' + JSON.stringify(submitted));",
    ]


def _handoff_node_lines(capture: Dict[str, str]) -> List[str]:
    """Return the Node lines that wait for the master, resume, and snapshot the result."""
    lines = [
        "    if (AWAIT_HUMAN) {",
        "      const beforeUrl = await page.url().catch(() => '');",
        "      const beforeTitle = await page.title().catch(() => '');",
        "      let returned = false;",
        "      try {",
        "        await task.waitForControl({ interval: WAIT_POLL_MS, timeout: HANDOFF_TIMEOUT_MS });",
        "        returned = true;",
        "      } catch (e) { returned = false; }",
        "      if (returned) {",
        "        console.log('CONTROL_BACK:true');",
    ]
    lines += _auto_submit_node_lines()
    lines += [
        "        await page.waitForLoadState('load').catch(() => {});",
        "        await page.waitForTimeout(400).catch(() => {});",
    ]
    if capture.get("screenshot"):
        shot = capture.get("screenshot", "")
        lines += [
            "        try {",
            f"          await page.screenshot({{ path: {_literal(shot)} }});",
            f"          console.log('SCREENSHOT:' + {_literal(shot)});",
            "        } catch (e) { console.log('SCREENSHOT_ERROR: ' + (e && e.message ? e.message : e)); }",
        ]
    lines += [
        "        const resumedText = await page.snapshot({ includeStableLocator: true });",
        "        console.log('TITLE: ' + (await page.title()));",
        "        console.log('FINAL_URL: ' + (await page.url()));",
        "        console.log('" + _RESULT_BEGIN + "');",
        "        console.log(resumedText);",
        "        console.log('" + _RESULT_END + "');",
        "        console.log('RESUMED:true');",
        "      } else {",
        "        console.log('HANDOFF_TIMEOUT:' + HANDOFF_TIMEOUT_MS);",
        "      }",
        "    }",
    ]
    return lines


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
    capture: Optional[Dict[str, str]] = None,
    await_human: bool = False,
    handoff_timeout_ms: int = 0,
    auto_submit: bool = False,
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
    capture = capture or {}
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
        "  const OTP_KW = " + json.dumps(list(_OTP_HINTS)) + ";",
        f"  const AWAIT_HUMAN = {'true' if await_human else 'false'};",
        f"  const AUTO_SUBMIT = {'true' if auto_submit else 'false'};",
        f"  const HANDOFF_TIMEOUT_MS = {int(handoff_timeout_ms)};",
        f"  const WAIT_POLL_MS = {_WAIT_POLL_MS};",
        f"  const ACTION_MS = {_ACTION_TIMEOUT_MS};",
        "  const SUBMIT_KINDS = " + json.dumps(list(_AUTO_SUBMIT_KINDS)) + ";",
        "  const SUBMIT_RE = " + _literal(_SUBMIT_LABEL_RE) + ";",
        "  function kindFor(text) {",
        "    const t = (text || '').toLowerCase();",
        "    const hasInput = /" + _INPUT_ROLE_RE + "/.test(t);",
        "    const hasPasswordInput = /type\\s*=\\s*[\"\x27]?password/i.test(t) || /password/i.test(t);",
        "    if (CAPTCHA_KW.some(k => t.includes(k))) return 'captcha';",
        "    if (OTP_KW.some(k => t.includes(k)) && hasInput) return 'otp';",
        "    if (LOGIN_KW.some(k => t.includes(k)) && hasPasswordInput) return 'login';",
        "    if (FORM_KW.some(k => t.includes(k)) && hasInput && (/required/i.test(t) || /mandatory/i.test(t))) return 'form_input';",
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
        "  const delegated = existing ? (existing.ownership === 'user' || existing.ownership === 'agentDelegatedToUser') : false;",
        "  if (existing && !CLAIM_ROUND && delegated) {",
        "    paused = true;",
        "    console.log('SESSION_PAUSED: the master currently owns task space id=' + existing.id + ' (it was handed off to them); browser commands are paused until they continue. Never take a master-owned space back on your own: ask the master through ask_master whether they are done with the tab, and resume with action=claim only after they say to continue.');",
        "  } else if (existing) {",
        "    try {",
        "      try { task = await takeOverTaskSpace(existing.id); }",
        "      catch (e) { task = await claimTaskSpace(existing.id); }",
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
    elif action == "press":
        lines += [
            "  try {",
            f"    await page.keyboard.press({_literal(query or 'Enter')});",
            "  } catch (e) {",
            "    console.log('ACTION_ERROR: ' + (e && e.message ? e.message : e));",
            "  }",
        ]
    elif action == "screenshot":
        shot = capture.get("screenshot") or ""
        lines += [
            "  try {",
            f"    await page.screenshot({{ path: {_literal(shot)} }});",
            f"    console.log('SCREENSHOT:' + {_literal(shot)});",
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
        "  const need = CLAIM_ROUND ? null : kindFor(snapText || '');",
        "  if (need) {",
    ]
    if capture.get("captcha_image"):
        lines += _captcha_capture_lines(
            capture.get("captcha_image", ""),
            capture.get("captcha_audio", ""),
            capture.get("screenshot", ""),
        )
    lines += [
        "    await task.handOff();",
        "    console.log('NEED_HUMAN:' + need);",
    ]
    if await_human:
        lines += _handoff_node_lines(capture)
    lines += [
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
    await_human: bool = True,
    handoff_timeout: Optional[int] = None,
) -> Dict[str, object]:
    """Act in the ego-lite browser and return the page snapshot as text.

    ``action`` selects what happens this round: ``load`` navigates to ``url``;
    ``click`` clicks ``selector``; ``fill`` types ``query`` into ``selector``;
    ``select`` chooses the ``query`` option inside the ``selector`` dropdown;
    ``press`` sends the ``query`` key (default Enter) to submit a form;
    ``screenshot`` saves a full-page screenshot to a timestamped file;
    ``claim`` re-takes a handed-off (master-owned) space after the master says
    they are done, then acts like ``load``; ``release`` finishes the agent-owned
    task space so the browser claim is freed for the next task. The task space
    persists between calls, so later rounds can act on ``url`` loaded earlier
    without re-navigating.

    When the page needs a human, the CAPTCHA image (and audio CAPTCHA, when
    present) is saved to a file so the master can read it even though the
    browser is headless; the paths are returned under ``need_human``.
    Artifacts land in the current working directory unless
    ``PICO_ARTIFACTS_DIR`` overrides it.

    The hand-off is self-resuming by default (``await_human``, gated by
    ``PICO_BROWSER_AWAIT_HUMAN``): the tool announces what the master must do
    (``notify_master``), waits in place with ``task.waitForControl`` for up to
    ``handoff_timeout`` seconds (default ``PICO_BROWSER_HANDOFF_TIMEOUT`` /
    600), then reclaims the space, submits the form itself (CAPTCHA and
    required-form pages only — login/OTP pages stay read-only), and snapshots
    the result page, reporting ``resumed``/``submitted``. When the master does
    not return control in time the result is ``paused``/``handoff_timeout`` and
    the agent must recover with ``ask_master`` + ``action='claim'``.
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
    capture = _capture_paths()
    wait_seconds = handoff_timeout_seconds(handoff_timeout)
    awaiting = await_human and _await_human_enabled()
    script = _script_for(
        url,
        action,
        selector,
        query,
        timeout_ms=int(timeout_s * 1000),
        capture=capture,
        await_human=awaiting,
        handoff_timeout_ms=wait_seconds * 1000,
        auto_submit=_auto_submit_enabled(),
    )
    try:
        proc = _run_browser_script(
            script, timeout=timeout_s + (wait_seconds if awaiting else 0)
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "url": url, "error": f"browser timeout after {timeout_s}s"}
    except OSError as exc:
        return {"ok": False, "url": url, "error": f"could not start ego-browser: {exc}"}

    combined = proc.stdout + "\n" + proc.stderr
    release_round = (action or "load").lower() == "release"
    title, final_url = extract_metadata(combined)
    snapshot = extract_snapshot(combined, max_chars=max_chars)
    control_back = extract_control_back(combined)
    resumed = extract_resumed(combined)
    submitted = extract_submitted(combined)
    handoff_timeout_hit = extract_handoff_timeout(combined)
    nav_error = extract_nav_error(combined)
    action_error = extract_action_error(combined)
    session_error = extract_session_error(combined)
    paused_error = extract_session_paused(combined)
    captcha_image = extract_captcha_image(combined)
    captcha_audio = extract_captcha_audio(combined)
    screenshot = extract_screenshot(combined)
    kind = extract_need_human(combined)
    if not kind and snapshot and action not in ("release", "claim"):
        kind = _kind_for(snapshot)
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
        # Extract the task space id from the SESSION_PAUSED notice so the agent
        # can heal itself by calling action='claim' with the right space.
        space_id = None
        next_step = "call ask_master to confirm the master is done with the tab, then resume with action='claim'"
        m = re.search(r"task space id[:= ]?(\d+)", paused_error)
        if m:
            space_id = int(m.group(1))
        return {
            "ok": False,
            "paused": True,
            "url": url,
            "error": paused_error,
            "space_id": space_id,
            "next_step": next_step,
            "hint": "The tab is parked under the master's control (task space id: "
            + str(space_id)
            + "). Ask the master through 'ask_master' whether they are done with it; "
            + "once they say to continue, resume with action='claim' (this reclaims the space "
            + "and snapshots the page).",
        }
    if kind and awaiting and not resumed:
        notify_master(_handoff_announcement(kind, final_url or url, captcha_image, captcha_audio))
    if handoff_timeout_hit is not None:
        return {
            "ok": False,
            "paused": True,
            "handoff_timeout": True,
            "awaiting_human": True,
            "url": url,
            "error": (
                "the master did not hand control back within "
                f"{handoff_timeout_hit // 1000}s, so the tab is still parked"
            ),
            "space_id": None,
            "next_step": (
                "call ask_master to check whether the master finished with the open "
                "browser tab; once they confirm, resume with action='claim' (no url) "
                "and read the result page"
            ),
            "hint": (
                "The tab is parked under the master's control. Ask the master through "
                "'ask_master' whether they are done with it; resume with "
                "action='claim' when they say to continue."
            ),
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
    if resumed:
        result_snapshot = extract_result_snapshot(combined, max_chars=max_chars)
        snapshot = result_snapshot or snapshot
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
    if control_back:
        result["control_returned"] = True
    if resumed:
        result["resumed"] = True
    if submitted is not None:
        result["submitted"] = submitted
    if extract_claimed(combined):
        result["claimed"] = True
    if screenshot:
        result["screenshot"] = screenshot
    if kind and not resumed:
        _raise_ego_lite_window()
        hint = (
            "Stop guessing and ask the master through 'ask_master'. The ego-lite "
            "browser window is open on this page and handed to the master: ask them "
            "to type the CAPTCHA into the field and submit the form (click the "
            "search/submit button) in the open browser window themselves."
        )
        if captcha_image:
            hint += (
                " If the browser is headless, the CAPTCHA image was saved to "
                f"{captcha_image!r} — give the master that path so they can open it "
                "(and play the audio file when present) to read the code."
            )
        if awaiting:
            hint += (
                " Then hand control back — pico waits for the browser, submits the "
                "form itself, and reads the result page without a second instruction."
            )
        else:
            hint += (
                " When the master confirms they submitted the form, resume with "
                "action='claim' (no url) to read the result page — do NOT fill the "
                "CAPTCHA answer yourself."
            )
        need_human: Dict[str, object] = {
            "kind": kind,
            "message": _HUMAN_MESSAGES.get(kind, "The page needs a human."),
            "hint": hint,
        }
        if captcha_image:
            need_human["captcha_image"] = captcha_image
        if captcha_audio:
            need_human["captcha_audio"] = captcha_audio
        result["need_human"] = need_human
        logger.warning(
            f"[bold yellow]Browser needs the master[/bold yellow]: {kind}"
            + (f" (saved {captcha_image})" if captcha_image else "")
        )
    elif resumed:
        logger.info(
            f"[bold green]Browser resumed[/bold green]: {title or final_url or url} "
            + (f"(submitted: {submitted.get('how')})" if submitted else "")
        )
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


def extract_control_back(stdout: str) -> bool:
    """Return True when the master handed the browser back to the agent."""
    return any(
        line.startswith(_CONTROL_BACK_MARK)
        and line[len(_CONTROL_BACK_MARK):].strip().lower() == "true"
        for line in stdout.splitlines()
    )


def extract_resumed(stdout: str) -> bool:
    """Return True when the script resumed the goal after the master's input."""
    return any(
        line.startswith(_RESUMED_MARK)
        and line[len(_RESUMED_MARK):].strip().lower() == "true"
        for line in stdout.splitlines()
    )


def extract_submitted(stdout: str) -> Optional[Dict[str, str]]:
    """Return the SUBMITTED payload describing what pico pressed, when present."""
    for line in stdout.splitlines():
        if not line.startswith(_SUBMITTED_MARK):
            continue
        raw = line[len(_SUBMITTED_MARK):].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {"how": "unknown", "selector": "", "reason": raw}
        if isinstance(data, dict):
            return {str(key): str(value) for key, value in data.items()}
        return {"how": "unknown", "selector": "", "reason": raw}
    return None


def extract_handoff_timeout(stdout: str) -> Optional[int]:
    """Return the HANDOFF_TIMEOUT duration in milliseconds, when present."""
    for line in stdout.splitlines():
        if not line.startswith(_HANDOFF_TIMEOUT_MARK):
            continue
        raw = line[len(_HANDOFF_TIMEOUT_MARK):].strip()
        try:
            return int(raw)
        except ValueError:
            return 0
    return None


def extract_captcha_image(stdout: str) -> Optional[str]:
    """Return the path of a saved CAPTCHA image (or fallback screenshot), if any."""
    for line in stdout.splitlines():
        if line.startswith("CAPTCHA_IMAGE:"):
            return line[len("CAPTCHA_IMAGE:"):].strip()
    return None


def extract_captcha_audio(stdout: str) -> Optional[str]:
    """Return the path of a saved audio CAPTCHA, if the page exposed one."""
    for line in stdout.splitlines():
        if line.startswith("CAPTCHA_AUDIO:"):
            return line[len("CAPTCHA_AUDIO:"):].strip()
    return None


def extract_screenshot(stdout: str) -> Optional[str]:
    """Return the path of a saved full-page screenshot."""
    for line in stdout.splitlines():
        if line.startswith("SCREENSHOT:"):
            return line[len("SCREENSHOT:"):].strip()
    return None


def _extract_block(stdout: str, begin_mark: str, end_mark: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Return the text between two markers, truncated to max_chars."""
    before = stdout.find(begin_mark)
    if before == -1:
        return ""
    start = before + len(begin_mark)
    end = stdout.find(end_mark, start)
    if end == -1:
        end = len(stdout)
    block = stdout[start:end].strip("\n")
    if len(block) > max_chars and max_chars > 0:
        block = block[:max_chars] + "\n...[truncated by pico]"
    return block


def extract_snapshot(stdout: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Return the snapshot block between markers, truncated to max_chars."""
    return _extract_block(stdout, _SNAPSHOT_BEGIN, _SNAPSHOT_END, max_chars)


def extract_result_snapshot(stdout: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Return the post-hand-off result snapshot, or "" when there was no resume."""
    return _extract_block(stdout, _RESULT_BEGIN, _RESULT_END, max_chars)