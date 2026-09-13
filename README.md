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
  browser automation through ego-lite (`ego_lite_browse_use`), and Gmail via
  OAuth (`gmail_list` / `gmail_search` / `gmail_read` / `gmail_send` /
  `gmail_mark`).
- **Gmail access**: pico reads your inbox (latest/unread/search) via OAuth2.
  Reading runs automatically; sending and marking mail pause for your explicit
  approval. pico never deletes email. See
  [Gmail setup](#gmail-setup-oauth2) for the one-time authorization.
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

Run `python app.py` in an interactive terminal to open the full-screen Rich TUI.
The dashboard owns the terminal for the whole session and renders, top to bottom:

- a cyan **status** header with the provider, model, and cumulative token
  counts (prompt / completion / total);
- a **pico** answer pane showing your latest reply as markdown — or a spinner
  while pico is working;
- a blue **task plan** beside a green **live** column containing streamed
  `agent › …` output, token usage, per-agent activity, memory sizes, and a
  captured log tail;
- a magenta footer that becomes the in-TUI input box.

The input line lives inside the TUI. Type your next task at the bottom `pico> `
prompt and press Enter. Approvals and master questions (yes/no or free text)
render in the same footer, so the screen stays intact. After each task, the
answer and session token usage remain in the answer pane; type `exit` to quit.
Pass `--plain` to use the classic console REPL instead.

### Working view

This is a representative render from the current `dashboard.py` implementation
(at a 100-column terminal; values change as tasks run):

```text
╭───────────────────────────────────────────── status ─────────────────────────────────────────────╮
│ pico — your day-to-day assistant                                                                 │
│ provider=nvidia  model=example-model  tokens: 412 in / 96 out / 508 total                        │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
╭────────────────────────────────────────────── pico ──────────────────────────────────────────────╮
│ Type a task below and press Enter.                                                               │
│                                                                                                  │
│                                                                                                  │
│                                                                                                  │
│                                                                                                  │
│                                                                                                  │
│                                                                                                  │
│                                                                                                  │
│                                                                                                  │
│                                                                                                  │
│                                                                                                  │
│                                                                                                  │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
╭──────────────────────────────────────────────────────────╮╭──────────────────────────────────────╮
│                        Task plan                         ││ ╭────────── Live output ───────────╮ │
│  Status         Step                                     ││ │ executor › Reading README.md     │ │
│  ✓ done         pico: Plan the request  (planned)        ││ ╰──────────────────────────────────╯ │
│  ▶ running      executor: Read README.md                 ││                Tokens                │
│  ● pending      researcher: Research the details         ││  Kind                         Count  │
│                                                          ││  prompt                         412  │
│                                                          ││  completion                      96  │
│                                                          ││  total                          508  │
│                                                          ││               Activity               │
│                                                          ││  Agent                     Requests  │
│                                                          ││  executor                         1  │
│                                                          ││  pico                             1  │
│                                                          ││  researcher                       1  │
│                                                          ││ ╭───────────── Memory ─────────────╮ │
│                                                          ││ │ working: 0                       │ │
│                                                          ││ │ episodes: 52                     │ │
│                                                          ││ │ long-term: 0                     │ │
│                                                          ││ │ semantic: 0                      │ │
│                                                          ││ ╰──────────────────────────────────╯ │
│                                                          ││ ╭──────────── Log tail ────────────╮ │
│                                                          ││ │ agents.pico :: Planning step     │ │
│                                                          ││ │ complete                         │ │
│                                                          ││ ╰──────────────────────────────────╯ │
│                                                          ││                                      │
│                                                          ││                                      │
╰──────────────────────────────────────────────────────────╯╰──────────────────────────────────────╯
╭────────────────────────────────────────────── pico ──────────────────────────────────────────────╮
│ pico> Summarize README.md▌                                                                       │
│                                                                                                  │
│                                                                                                  │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
```

Markers are live: `● pending`, `▶ running`, `✓ done`, `✗ failed`. The dashboard
uses the terminal alternate screen while running and restores the normal console
when it exits.

Token accounting lives on each LLM client (`BaseLLM.usage` / `last_generation`);
`Pico.usage` sums across the orchestrator and both sub-agents.

## Providers (via `.env`)

`PROVIDER` selects the model backend; `nvidia` is the default.

```
PROVIDER=nvidia                 # openai | nvidia | cerebras | groq
MODEL_ID=...  API_KEY=...       # openai
NVIDIA_MODEL_ID / NVIDIA_API_KEY / NVIDIA_BASE_URL   # nvidia
CEREBRAS_MODEL_ID / CEREBRAS_API_KEY / CEREBRAS_BASE_URL  # cerebras
GROQ_MODEL_ID / GROQ_API_KEY / GROQ_BASE_URL         # groq (GROK_* accepted as fallback)
LOG_LEVEL=INFO                  # optional
PICO_MEMORY_PATH=~/.pico        # optional memory directory
PICO_COMPACTION_TOKENS=24000    # optional auto-compaction threshold
GMAIL_CLIENT_SECRET_PATH=file:///path/to/client_secret_*.json  # OAuth app
GMAIL_CREDENTIALS_PATH=~/.agents/gmail                         # token dir (optional)
GMAIL_IMAP_USER=you@gmail.com   # optional legacy IMAP address (superseded by OAuth)
GMAIL_IMAP_PASSWORD=xxxx        # optional app password (superseded by OAuth)
```

Never commit `.env` — it is git-ignored.

## Gmail setup (OAuth2)

pico talks to your inbox through the official Gmail API using OAuth2. You do
this once — afterwards the token auto-refreshes and you can just ask pico to
check your mail.

### 1. Create a Google Cloud OAuth client

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and
   create a project (or reuse one).
2. Enable the **Gmail API**: *APIs & Services → Library* → search for
   "Gmail API" → *Enable*.
3. Configure the consent screen: *APIs & Services → OAuth consent screen* →
   choose **External**, add an app name, and add your Gmail address under
   **Test users**.
4. Create the OAuth client: *APIs & Services → Credentials → Create
   Credentials → OAuth client ID* → application type **Desktop app** →
   *Create*.
5. Download the generated `client_secret_*.json` file.

### 2. Point pico at the client secrets

Copy the file into the credentials directory as `credentials.json`, or set
`GMAIL_CLIENT_SECRET_PATH` in `.env` (a plain path or `file://` URI both work):

```
GMAIL_CLIENT_SECRET_PATH=file:///absolute/path/to/client_secret_xxx.apps.googleusercontent.com.json
```

Resolution order when pico starts: `credentials.json` beside the token →
`GMAIL_CLIENT_SECRET_PATH` → any `client_secret_*.json` in the credentials
directory.

### 3. Authorize once

```bash
.venv/bin/python -m agents.tools.gmail_oauth
```

A browser opens; sign in with your Google account and approve the requested
scopes. The authorized token is saved as `token.json` in the credentials
directory and is refreshed automatically going forward.

### Where things live

Files land in `~/.agents/gmail/` by default — or in the existing
`~/.personal-assistant/gmail/` folder if you had an older setup — and you can
override the directory with `GMAIL_CREDENTIALS_PATH`:

| File | Purpose |
| --- | --- |
| `credentials.json` / `client_secret_*.json` | OAuth client secrets exported from Google Cloud |
| `token.json` | Authorized access + refresh token (auto-generated, auto-refreshed) |

Treat both files as secrets: they live outside the repo and must never be
committed or logged.

### What the token can do

The sign-in grants three scopes — `gmail.readonly`, `gmail.send`,
`gmail.modify`:

- `gmail_list` / `gmail_search` / `gmail_read` run automatically when asked.
- `gmail_send` (send/reply) and `gmail_mark` (read / unread / flagged) pause
  for your explicit approval before pico acts.
- Email is never deleted.

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