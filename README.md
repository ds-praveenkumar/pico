# pico

```
       _           
 _ __ (_) ___ ___  
| '_ \| |/ __/ _ \ 
| |_) | | (_| (_) |
| .__/|_|\___\___/ 
|_|                
```

pico is your day-to-day personal assistant — it plans and executes tasks using
world-class tools, always under your (Praveen's) supervision, and remembers your
choices so it can serve you better over time.

## Features (v1)

- **Interactive REPL** (`python app.py`) and **single-shot** mode, both backed by a
  live rich dashboard that shows the running task plan, token usage, per-agent
  activity, memory sizes, and a log tail. Use `--plain` to disable the full-screen
  view, or `-y` to auto-approve tools in single-shot mode.
- **Multi-agent delegation**: `pico` (orchestrator) plans a task and delegates to
  worker sub-agents — `executor` (safe file/shell work) and `researcher`
  (web/browser research).
- **Supervision**: in interactive mode, only higher-risk tool calls (file writes,
  browsing, non-trivial shell commands) pause for your approval — read-only tools
  and trivial commands (e.g. `date`, `echo`, `ls`, `git status`) run automatically
  and are logged. In single-shot mode all tool calls are refused unless `-y` is
  passed (supervised flow: approve/deny each call).
- **Sandboxed shell**: `bash` runs inside a sandbox (`agents/tools/sandbox.py`) —
  a scrubbed environment with no API keys, enforced CPU/memory/process limits,
  a timeout, and a command allowlist. **pico never deletes files.**
- **Safe tools**: command allowlist + destructive-command blocklist
  (`bash`), path-confined reads/writes (`file_read`/`file_write`), skill reader,
  browser automation through ego-lite (`ego_lite_browse_use`), and read-only
  Gmail over IMAP (`gmail_latest` / `gmail_search`).
- **Gmail access**: pico reads your inbox (latest/unread/search) over IMAP using
  the stdlib — read-only, never deletes or modifies email. Configure `GMAIL_IMAP_USER`
  and `GMAIL_IMAP_PASSWORD` (an **app password**) in `.env`.
- **Skills**: declarative YAML skills teach pico domain workflows — browser use,
  Gmail, **gym routines**, and **expense planning**.
- **Auto-compaction**: long tool-loop conversations are folded into a one-call
  summary when they cross `PICO_COMPACTION_TOKENS` (default 24000), so the context
  window never blows up mid-task.
- **Growing memory**: pico keeps working memory (session scratchpad), episodic
  memory (a log of completed tasks), and long-term memory (facts and preferences),
  persisted under `~/.pico` (override with `PICO_MEMORY_PATH`). On top of that it
  stores memorable sentences in semantic memory and searches them by meaning with
  a local vector search — no network needed. Agents capture memories with the
  `memory_*` and `semantic_*` tools, and pico reviews its memories before each task.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your provider keys
python app.py          # interactive REPL
```

## CLI usage

```bash
python app.py                       # interactive REPL (asks before each tool call)
python app.py "Summarize README.md" # single-shot; tool calls are refused by default
python app.py "Summarize README.md" --yes  # single-shot with auto-approved tools
python app.py --plain "Summarize README.md"  # no full-screen dashboard
```

## Live dashboard

In the REPL, pico runs as a **full-screen chat TUI**: the dashboard owns the
terminal for the whole session, showing the plan, live token usage, per-agent
activity, memory, streamed LLM output, and a log tail. The **input line lives
inside the TUI** — type your next task at the bottom `pico> ` prompt and press
Enter. Higher-risk calls ask for your approval in the TUI too. After each task
the answer and a session token-usage line appear on screen; `exit` quits.

Pass `--plain` to use the classic console REPL instead (input in the terminal).

While a task runs, the plan transitions
(pending → running → done) like this:

```
╭─ status ───────────────────────────────────────────────╮
│ pico — your day-to-day assistant                       │
│ provider=nvidia  model=gpt-5  tokens: 412 in / 96 out  │
├─ Task plan ──────────────────┬─ Tokens ────────────────┤
│ ○ pending   executor: read    │ prompt          412    │
│ ▶ running   pico: plan        │ completion       96    │
│ ○ pending   researcher: …     │ total            508   │
│                              ├─ Activity ─────────────┤
│                              │ executor           2    │
│                              │ researcher         1    │
│                              ├─ Memory ───────────────┤
│                              │ working: 6             │
│                              │ long-term: 3           │
│                              │ semantic: 4            │
│                              ├─ Log tail ─────────────┤
│                              │ pico :: planning …     │
├─ executing plan… ─────────────────────────────────────┤
╰───────────────────────────────────────────────────────╯
```

Render details (colors are live): cyan status header, blue task plan, green
right column (tokens / activity / memory / log tail), and a magenta footer
status line. The dashboard takes over the alternate screen during a task and
restores the normal console when it finishes.

Token accounting lives on each LLM client (`BaseLLM.usage` / `last_generation`);
`Pico.usage` sums across the orchestrator and both sub-agents.

## Providers (via `.env`)

`PROVIDER` selects the model backend; `nvidia` is the default.

```
PROVIDER=nvidia                 # openai | nvidia | cerebras
MODEL_ID=...  API_KEY=...       # openai
NVIDIA_MODEL_ID / NVIDIA_API_KEY / NVIDIA_BASE_URL   # nvidia
CEREBRAS_MODEL_ID / CEREBRAS_API_KEY / CEREBRAS_BASE_URL  # cerebras
LOG_LEVEL=INFO                  # optional
PICO_MEMORY_PATH=~/.pico        # optional memory directory
PICO_COMPACTION_TOKENS=24000    # optional auto-compaction threshold
GMAIL_IMAP_USER=you@gmail.com   # Gmail access (read-only IMAP)
GMAIL_IMAP_PASSWORD=xxxx        # an app password, NOT your account password
GMAIL_IMAP_HOST=imap.gmail.com  # optional
```

Never commit `.env` — it is git-ignored.

## Application logs

pico uses async **rich logging** (`brain/logging_setup.py`) — a `QueueHandler` +
`QueueListener` pair feeds log records to a rich console handler with timestamps,
markup, and pretty tracebacks. No configuration is needed to start; set the level
via the `LOG_LEVEL` env var in `.env`:

```
LOG_LEVEL=INFO      # DEBUG | INFO | WARNING | ERROR | CRITICAL (default INFO)
```

- Logs print to stderr as `name :: message` (e.g. `agents.pico :: Planning step…`),
  independent of pico's replies in stdout.
- Levels follow the standard logging hierarchy — `DEBUG` shows tool call
  details, `INFO` shows normal operation, `WARNING`/`ERROR` only surface
  issues.
- Invalid values fall back to `INFO`; there is no log file by default (pipe
  stderr to a file to keep a session log: `python app.py 2> pico.log`).

## Project layout

`AGENTS.md` describes the architecture, conventions, and dev workflow in detail.
The short version:

- `brain/` — LLM clients (all OpenAI-compatible), async rich logging, notes store.
- `agents/` — `pico` orchestrator, `executor`/`researcher` sub-agents, safe tools.
- `agents/prompts/system.md` — pico's master system prompt (source of truth).
- `tests/` — pytest suite (all mocked, no network calls).

## Development

```bash
python -m pytest -q   # run the test suite
```