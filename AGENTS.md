# AGENTS.md

## Overview
Agent manager system that executes tasks via a Planner (Ollama) → Workflow Engine → Agents pipeline with Review & Rework.

```
User: "/build personal website"
          │
          ▼
┌─────────────────────────────┐
│      Planner (Ollama)       │  ← Decomposes task into structured JSON plan
└──────────┬──────────────────┘
           │ Workflow Plan (steps, agents, dependencies)
           ▼
┌─────────────────────────────┐
│      Workflow Engine        │  ← Generic DAG executor
│  ┌──────────────────────┐   │
│  │   Step Executor      │   │  ← Per-step: dispatch → review → retry/fail
│  │   ┌──────┐ ┌──────┐  │   │
│  │   │ Step │ │ Step │  │   │
│  │   └──┬───┘ └──┬───┘  │   │
│  │      ▼         ▼      │   │
│  │  ┌────────┐ ┌────────┐│   │
│  │  │ Agent  │ │ Agent  ││   │  ← Routes to CLI agent or Ollama
│  │  │ Claude │ │ Codex  ││   │
│  │  └───┬────┘ └───┬────┘│   │
│  │      ▼           ▼     │   │
│  │  workspace/    workspace│   │
│  └──────────────────────┘   │
│             │               │
│             ▼               │
│  ┌──────────────────────┐   │
│  │   Review & Rework    │   │  ← Generic gate: evaluate → pass/retry (max N)
│  └──────────────────────┘   │
│             │               │
│             ▼               │
│  ┌──────────────────────┐   │
│  │  Completion Report   │   │
│  └──────────────────────┘   │
└─────────────────────────────┘
          │
          ▼
    Telegram: "✅ Job done!"
```

## Dev workflow
- `python3 main.py` — run bot (polling mode).
- `BOT_TOKEN=x python3 -c "import bot.main"` — import smoke test.
- `pytest tests/` — run tests (`asyncio_mode=auto`).
- `ruff check .` — lint (line-length 100, target py310).

## Critical gotchas
- **`persistence.save_async()` is debounced (2s)** — every state mutation must call it. Sync `persistence.save()` bypasses debounce.
- **Ollama `client.chat()` is sync** — must run via `asyncio.to_thread`. Never call on the event loop directly.
- **`html.escape()`** — all user-facing model output must be escaped.
- **Agent subprocess** = `asyncio.create_subprocess_shell` with `shlex.split`; 300s timeout. Path-traversal guard in `_safe_workspace()`.
- **`persistence.save()` is synchronous** — used in agent handlers; inconsistent with async path.

## Architecture map
- `bot/workflow/planner.py` — decomposes tasks into `WorkflowPlan` via Ollama
- `bot/workflow/engine.py` — generic DAG executor for any WorkflowPlan
- `bot/workflow/models.py` — `Step`, `WorkflowPlan`, `Workflow` dataclasses
- `bot/workflow/orchestrator.py` — routes to agents with fallback + rate limits
- `bot/workflow/brain.py` — Ollama-powered brain functions (PRD, review, routing)
- `bot/workflow/usage.py` — per-agent RPH/TPH tracking, dead-agent detection
- `bot/workflow/handlers.py` — Telegram handlers for `/build`, `/wf_status`, `/wf_cancel`

## Code conventions
- **Optional deps**: `try/except ImportError` with `*_AVAILABLE` flag + user-facing message.
- **Bug fix comments**: `# OPUS-NNN fix:` inline.
- **Webhook ports**: `(443, 80, 88, 8443)` — Telegram-enforced.
- **Workflow `/build`**: planner generates structured steps → engine executes DAG → review gate checks output.

## Testing quirks
- `conftest.py` sets `BOT_TOKEN=test` and neutralizes `dotenv.load_dotenv`.
- `test_config.py` uses `importlib.reload(config_module)` — needs `clean_custom_env` fixture.
- Tests mock `asyncio.create_subprocess_shell` — no real agents spawned.
