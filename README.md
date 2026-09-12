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
- **Supervision**: every tool call in interactive mode asks for your approval
  before it runs. Single-shot mode refuses tool calls unless `--yes` is passed.
- **Safe tools**: command allowlist + destructive-command blocklist
  (`bash`), path-confined reads/writes (`file_read`/`file_write`), skill reader,
  and a browser-automation stub (`ego_lite_browse_use`). **pico never deletes files.**
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

While a task runs, pico switches to a full-screen TUI showing the current plan
(pending → running → done), live token usage (prompt/completion/total) from every
LLM request, which agent is calling, memory sizes, and a log tail. After each
task the completed plan, the summary, and a session token-usage line are printed
back in the normal console.

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