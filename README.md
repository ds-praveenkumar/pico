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
  browser automation through ego-lite (`ego_lite_browse_use`), Gmail via
  OAuth (`gmail_list` / `gmail_search` / `gmail_read` / `gmail_send` /
  `gmail_mark`), Google Calendar via OAuth (`calendar_list` / `calendar_create`
  / `calendar_respond`), Google Sheets via OAuth (`sheets_read` /
  `sheets_append` / `sheets_update`), plus `url_fetch` (read-only web pages),
  `weather` (Open-Meteo forecast), `voice_speak` (macOS text-to-speech), and
  topic-aware `latest_news`.
- **Gmail access**: pico reads your inbox (latest/unread/search) via OAuth2.
  Reading runs automatically; sending and marking mail pause for your explicit
  approval. pico never deletes email. See
  [Gmail setup](#gmail-setup-oauth2) for the one-time authorization.
- **Google Calendar**: pico reads your upcoming events via
  `calendar_list` (auto-approve); creating events and replying to invites pause
  for your approval. See [Google Calendar setup](#google-calendar-setup-oauth2).
- **Google Sheets**: pico reads and appends to your spreadsheets — the durable
  home for the **expense-planner** skill's ledger. Reads run automatically;
  appends/updates pause for your approval. See [Google Sheets setup](#google-sheets-setup-oauth2).
- **Skills**: declarative YAML skills teach pico domain workflows — browser use
  (ego-lite), Gmail, **gym routines**, and **expense planning**.
- **Auto-compaction**: long tool-loop conversations are folded into a one-call
  summary when they cross `PICO_COMPACTION_TOKENS` (default 24000), so the context
  window never blows up mid-task.
- **Growing memory**: pico keeps working memory (session scratchpad), episodic
  memory (a log of completed tasks), and long-term memory (facts and preferences),
  persisted under `~/.pico` (override with `PICO_MEMORY_PATH`). On top of that it
  stores memorable sentences in semantic memory and searches them by meaning with
  a local vector search — no network needed. Agents capture memories with the
  `memory_*` and `semantic_*` tools, and pico reviews its memories before each task.
- **Roadmap (not built yet)**: an **MCP client bridge** so pico can consume
  external MCP servers (allowlisted, namespaced `mcp_<server>_<tool>`, routed
  through the same registry and approval policy).

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

This is a representative render from the current `ui/dashboard.py` implementation
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
PROVIDER=nvidia                 # openai | nvidia | cerebras | groq | openrouter
MODEL_ID=...  API_KEY=...       # openai
NVIDIA_MODEL_ID / NVIDIA_API_KEY / NVIDIA_BASE_URL   # nvidia
CEREBRAS_MODEL_ID / CEREBRAS_API_KEY / CEREBRAS_BASE_URL  # cerebras
GROQ_MODEL_ID / GROQ_API_KEY / GROQ_BASE_URL         # groq (GROK_* accepted as fallback)
OPENROUTER_MODEL_ID / OPENROUTER_API_KEY / OPENROUTER_BASE_URL  # openrouter
LOG_LEVEL=INFO                  # optional
PICO_MEMORY_PATH=~/.pico        # optional memory directory
PICO_COMPACTION_TOKENS=24000    # optional auto-compaction threshold
PICO_FONT_SIZE=18               # optional TUI font size in points (OSC 7770 terminals)
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

## Google Calendar setup (OAuth2)

Calendar uses the same Google Cloud project as Gmail — same consent screen, same
credentials flow, and `requirements.txt` already ships the Google client
libraries.

1. In Google Cloud, enable the **Google Calendar API** (*APIs & Services →
   Library → "Google Calendar API" → Enable*).
2. In the OAuth consent screen you already configured for Gmail, add the
   `https://www.googleapis.com/auth/calendar.readonly` and
   `https://www.googleapis.com/auth/calendar.events` scopes (or create a second
   OAuth *Desktop app* client).
3. Point pico at the client secrets and authorize once:

```bash
# .env
CALENDAR_CLIENT_SECRET_PATH=file:///absolute/path/to/client_secret_xxx.json

.venv/bin/python -m agents.tools.calendar_oauth
```

Credentials and the authorized token live in `~/.agents/calendar/` (override
with `CALENDAR_CREDENTIALS_PATH`). All three Google modules (`gmail_*`,
`calendar_*`, `sheets_*`) share the same resolution rules as Gmail:
`credentials.json` beside the token → `*_CLIENT_SECRET_PATH` env → any
`client_secret_*.json` in the credentials directory.

### What the token can do

- `calendar_list` runs automatically (read-only).
- `calendar_create` and `calendar_respond` pause for your explicit approval.
- Events are never deleted.

## Google Sheets setup (OAuth2)

Spreadsheets give pico its durable, master-owned data store — the
expense-planner skill uses one as its live expense ledger.

1. Enable the **Google Sheets API** in Google Cloud (*APIs & Services → Library →
   "Google Sheets API" → Enable*) and make sure the consent screen includes the
   `spreadsheets.readonly` and `spreadsheets` scopes.
2. Point pico at the client secrets and authorize once:

```bash
# .env
SHEETS_CLIENT_SECRET_PATH=file:///absolute/path/to/client_secret_xxx.json

.venv/bin/python -m agents.tools.sheets_oauth
```

Credentials and the authorized token live in `~/.agents/sheets/` (override with
`SHEETS_CREDENTIALS_PATH`).

3. Create a spreadsheet in your browser (e.g. an expense ledger with
   `Date | Category | Amount | Note` columns), share it with your own account,
   and tell pico its URL once — pico remembers it (`expense_sheet_id`) and reads
   it with `sheets_read`, appends approved rows with `sheets_append`.

### What the token can do

- `sheets_read` runs automatically (read-only).
- `sheets_append` and `sheets_update` pause for your explicit approval.
- Nothing is ever deleted from a spreadsheet.

## Everyday tools

- `latest_news` — top headlines, or pass `topic` for news about a subject
  (Google News search).
- `weather` — current conditions + short forecast for any place name
  (Open-Meteo, no API key).
- `url_fetch` — read one public page as plain text; SSRF-guarded (no private or
  loopback destinations), byte-capped, time-limited.
- `voice_speak` — speak text aloud on macOS via the built-in `say` command.

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

## Terminal UI (Textual)

`python app.py --tui` runs pico inside a modern **Textual** interface. It is a
drop-in for the legacy rich dashboard REPL — same agent pipeline, same approval
supervision, same `-y` single-shot behavior — with dedicated screens:

- **Home** — live answer (markdown), task plan, streamed agent/tool output, log
  tail, token usage, and the prompt line.
- **History** (`h`) — recently completed tasks, redacted and recorded to JSONL.
- **Memory** (`m`) — read-only browser over working, episodic, long-term, and
  semantic memory.
- **Settings** (`s`) — session-only log level, output mode, terminal font size,
  stream visibility, and history capture toggle.
- **Help** (`?`) — key bindings and feature reference.
- **Ctrl+k** command palette, **Ctrl+n** multi-line compose, **Ctrl+e** export
  redacted history, **Ctrl+c** cancels the running task cooperatively.

Higher-risk tool calls (shell writes, gmail sends, calendar/sheets writes, and
anything unclear) pause for an on-screen approval modal; the `ask_master` tool
(CAPTCHA/OTP/forms) opens a free-text modal too, bridged safely from the agent's
worker thread.

History capture is **opt-in** and off by default: completed tasks are only
written (as redacted JSONL in `~/.pico/tui_history.jsonl`, or under
`PICO_MEMORY_PATH`) after you enable it in Settings. Email, browsing, voice,
calendar, and sheet payloads are always scrubbed from the record, and API
keys/tokens/passwords/email addresses are redacted before anything touches disk.

The TUI can resize the terminal font too. Pick a size under **Settings → Terminal
font size**, or set `PICO_FONT_SIZE` to a point size. pico emits the mintty
`OSC 7770` sequence, which mintty, Ghostty, kitty, and Warp honour; terminals
without it (VS Code's integrated terminal among them) ignore the sequence
harmlessly, so set the size in the terminal's own preferences there — for VS Code
that is `"terminal.integrated.fontSize"`.

```bash
python app.py --tui                    # interactive Textual session
python app.py --tui "summary of README.md" -y   # single-shot in the Textual UI
```

`--tui` and `--plain` are mutually exclusive; without either, pico keeps the
legacy rich dashboard REPL.