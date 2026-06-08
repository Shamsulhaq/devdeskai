# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

DevDeskAI is a Telegram bot that bridges users to a local Ollama server, plus six external coding-agent CLIs (`claude`, `opencode`, `codex`, `qwen`, `gemini`, `copilot`). Per-user state (history, model, persona, temperature, current agent, uploaded doc) is kept in memory and persisted to `bot_data.json`.

## Commands

```bash
# Run (polling mode by default; set WEBHOOK_URL in .env for webhook mode)
python3 main.py
python3 -m bot   # equivalent — main.py is a thin runpy wrapper

# Install deps
pip install -r requirements.txt

# Docker
docker build -t devdeskai .
docker run -d --name devdeskai --env-file .env \
  -v ./bot_data.json:/app/bot_data.json \
  -v ./workspace:/app/workspace \
  --network host devdeskai   # host network so container reaches Ollama on localhost
```

There is no test suite, linter, or formatter configured. The README's "lint/import check" is just `python3 -m bot`, which exits early because `config.py` raises if `BOT_TOKEN` is unset — so an import-only smoke test requires `BOT_TOKEN=x python3 -c "import bot.main"`.

`BOT_TOKEN` is required in `.env`; the process refuses to start without it.

## Architecture

### Entry flow
`main.py` → `bot/__main__.py` → `bot.main.main()` which:
1. Loads `bot_data.json` into module-level dicts in `bot/persistence.py`.
2. Calls `agents.detect()` (via `shutil.which`) to populate `_agents` with availability.
3. Registers handlers, then runs polling or webhook based on `WEBHOOK_URL`.
4. `post_init` eagerly loads the Whisper model in a thread so the first voice message isn't blocked.

### Per-user state lives in `bot/persistence.py` as module-level dicts
`chat_histories`, `user_prompts`, `user_models`, `user_temps`, `user_agent`, `user_agent_history` (bounded `deque`, maxlen 50), `user_docs`, `user_personas`, `stats`. Everything is keyed by Telegram `user_id` (int). `save_async` writes atomically (tempfile + `os.replace`) and is serialized by an `asyncio.Lock`. **Every state mutation must be followed by `await persistence.save_async(config.DATA_FILE)`** — that's the only durability mechanism.

### Three-mode message routing in `bot/handlers/media.py::handle_message`
A single text message can mean three different things depending on user state:
1. **Agent mode** — if `persistence.user_agent[uid]` is set, the message is shelled out to that CLI via `agents.run_cli` in a per-user workspace under `WORKSPACE_DIR/<uid>/` (path-traversal guarded by `_safe_workspace`).
2. **Photo with text** — base64-encoded image is attached to the Ollama request (vision models only). `PREFERRED_PHOTO_INDEX = 2` picks a ~800px size, not the original.
3. **Plain chat** — routed to `ollama.generate_and_reply`, which builds messages from history + system prompt and streams the response.

### Ollama client wrapper (`bot/ollama.py`)
- `get_client()` lazily constructs a singleton `ollama.Client`.
- `_sync_generate` is the synchronous streaming call; **it must run via `asyncio.to_thread`** (`generate()` does this) or it blocks the event loop. Streaming chunks are concatenated; nothing is sent to Telegram until the full response is ready.
- One automatic retry on `ConnectionError`/`TimeoutError` with a 1s backoff.
- `num_predict` is capped at `MAX_PREDICT_TOKENS = 1024`.
- `get_system_prompt(uid)` prefers persona → custom prompt → default, in that order.

### Coding-agent subprocess (`bot/agents.py`)
- `AGENTS_CONFIG` defines six CLIs and their invocation strings. `detect()` populates an `available` flag for each based on whether the binary is on `PATH`.
- `run_cli` uses `asyncio.create_subprocess_exec` with `shlex.split` (no shell interpolation) and a 300s `AGENT_TIMEOUT`. On timeout it kills the process.

### Telegram reply size
`ollama.reply_long` splits responses over 4096 chars at newlines/spaces and sends with `parse_mode=HTML`. **All user-facing model output must be passed through `html.escape()` first** (see handlers — every reply does `escape(reply)`).

### Custom commands
`config.CUSTOM_COMMANDS` is built at startup from any `CUSTOM_CMD_<NAME>` env var; each registers a `/name` handler in `bot/handlers/custom.py` that prepends the configured prompt text to the user's message before sending to Ollama.

## Conventions to follow

- **Optional dependencies** (`faster_whisper`/`whisper`, `duckduckgo_search`, `fitz`/`PyMuPDF`) are imported inside `try/except ImportError` with a module-level `*_AVAILABLE` flag and a user-facing "install X" message at runtime. Preserve this pattern when adding new optional features.
- **Bug fixes are commented `# BUG-NNN fix:`** referencing entries in `minimax-m3-found-bugs.md`. When touching these lines, consult that doc to understand the original failure mode before refactoring the fix away.
- **Webhook port allowlist** is hardcoded to `(443, 80, 88, 8443)` in `config.py` because Telegram only accepts these — don't relax this without checking Telegram's docs.

## Reference docs in repo

- `README.md` — user-facing install/config/commands reference.
- `DOCS.md` — fuller user manual.
- `FEATURES.md` — feature inventory.
- `minimax-m3-found-bugs.md` — catalog of historical bugs referenced by `BUG-NNN` comments in the code.
