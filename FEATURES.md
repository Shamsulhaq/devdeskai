# Feature Plan — Workflow Builder (Multi-Agent Orchestration)

> Status: Proposed · Priority: High · Target: master-v3

---

## Overview

The Workflow Builder transforms a single user prompt into an end-to-end development pipeline. Instead of asking "what do you want me to build?" and waiting for one AI to do everything, the bot acts as an **orchestrator** — it analyzes the task, generates a structured plan, dispatches each phase to the best-suited agent, reviews outputs, reworks failures, and delivers the final result.

**User experience:**

```
/build me a personal website with a blog and dark mode
→ Bot: Analyzing request...
→ Bot: Generated workflow with 6 phases
→ Bot: [Research] Claude analyzing requirements... ✅
→ Bot: [Plan] Claude creating architecture... ✅
→ Bot: [Plan Review] Codex validating plan... ✅
→ Bot: [UI Build] Gemini generating HTML/CSS... ✅
→ Bot: [Code Review] Claude reviewing code... ⚠️ Issues found, reworking...
→ Bot: [Code Review] Claude reviewing code... ✅
→ Bot: [Test] Copilot verifying setup... ✅
→ Bot: [Docs] Gemini writing documentation... ✅
→ Bot: ✅ Project complete! Files in workspace/12345/
```

---

## Architecture

```
User: "/build personal website"
          │
          ▼
┌─────────────────────────────┐
│      Planner (Ollama)       │  ← Brain that decomposes task
│  Generates structured JSON  │
└──────────┬──────────────────┘
           │ Workflow Plan
           ▼
┌─────────────────────────────┐
│      Workflow Engine        │  ← Async executor in bot/workflow/
│  ┌──────────────────────┐   │
│  │   Phase Executor     │   │  ← Runs phases sequentially
│  │   ┌──────┐ ┌──────┐  │   │
│  │   │ Step │ │ Step │  │   │  ← Each step = one agent task
│  │   └──┬───┘ └──┬───┘  │   │
│  │      ▼         ▼      │   │
│  │  ┌────────┐ ┌────────┐│   │
│  │  │ Agent  │ │ Agent  ││   │  ← Routes to CLI agent
│  │  │ Claude │ │ Codex  ││   │
│  │  └───┬────┘ └───┬────┘│   │
│  │      ▼           ▼     │   │
│  │  workspace/    workspace│   │
│  └──────────────────────┘   │
│             │               │
│             ▼               │
│  ┌──────────────────────┐   │
│  │   Review & Rework    │   │  ← Validate output, loop if needed
│  │   max_retries = 2    │   │
│  └──────────────────────┘   │
│             │               │
│             ▼               │
│  ┌──────────────────────┐   │
│  │  Completion Report   │   │  ← Summary + file listing
│  └──────────────────────┘   │
└─────────────────────────────┘
          │
          ▼
    Telegram: "✅ Job done!"
```

---

## Data Model

### Workflow

```json
{
  "id": "wf_1712345678",
  "user_id": 12345,
  "task": "Build a personal website with blog and dark mode",
  "status": "running",
  "created_at": 1712345678,
  "phases": [
    {
      "name": "Research",
      "status": "completed",
      "steps": [
        {
          "id": "step_1",
          "agent": "claude",
          "prompt": "Analyze requirements for a personal website...",
          "input_file": null,
          "output_file": "workspace/12345/requirements.md",
          "status": "completed",
          "retries": 0
        }
      ]
    },
    {
      "name": "Plan",
      "status": "running",
      "steps": [...]
    }
  ],
  "current_phase": 1,
  "current_step": 0,
  "error": null
}
```

### Planner Output (Ollama generates this)

```
You are a workflow planner. Given a user's build request, output ONLY a JSON array of phases.
Each phase has: name, agent, prompt_template, input_from (previous step id), review_step (bool)

Example output:
[
  {
    "name": "Research",
    "steps": [
      {"agent": "claude", "prompt": "Analyze requirements for: {task}", "output": "requirements.md"}
    ]
  },
  {
    "name": "Plan",
    "steps": [
      {"agent": "claude", "prompt": "Create architecture plan based on requirements.md", "input": "requirements.md", "output": "plan.md"},
      {"agent": "codex", "prompt": "Review this plan for feasibility: {plan}", "input": "plan.md", "review": true}
    ]
  }
]
```

---

## Phases & Agent Routing

| Phase | Primary Agent | Fallback | Description |
|-------|---------------|----------|-------------|
| Research | Claude | Ollama | Analyze requirements, gather context |
| Plan | Claude | Codex | Create architecture, tech stack, file structure |
| Plan Review | Codex | Gemini | Validate plan feasibility, suggest improvements |
| Build (UI) | Gemini | Claude | Generate HTML/CSS, frontend code |
| Build (Logic) | Claude | Copilot | Backend logic, API routes, database |
| Build (Parallel) | Codex + Copilot + Claude | — | Independent modules built concurrently |
| Code Review | Claude | OpenCode | Review all generated code, find bugs |
| Test | Copilot | Ollama | Generate and run tests |
| Documentation | Gemini | OpenCode | Write README, API docs, setup guide |
| Final Verification | Claude | Gemini | End-to-end check, ensure completeness |

### Agent Command Mapping

| Agent | CLI Command | Used For |
|-------|-------------|----------|
| Claude | `claude -p "{prompt}"` | Planning, review, architecture |
| Codex | `codex "{prompt}"` | Code generation, validation |
| Gemini | `gemini "{prompt}"` | UI, documentation, creative |
| Copilot | `copilot "{prompt}"` | Testing, debugging |
| OpenCode | `opencode "{prompt}"` | Refactoring, documentation |

---

## Implementation Phases

### Phase 1 — Core Engine (`bot/workflow/`)

**Files to create:**

```
bot/workflow/
  __init__.py
  models.py        ← Workflow, Phase, Step dataclasses
  planner.py       ← Prompt to Ollama → structured plan JSON
  engine.py        ← Async executor, status tracking, progress updates
  agent_router.py  ← Routes steps to correct agent CLI
  handlers.py      ← /build, /status, /cancel commands
```

**Capabilities:**
- `/build <task>` generates plan, runs phases sequentially
- Progress updates sent to Telegram (typing + text)
- Each step writes output to user workspace
- Review steps can trigger rework (max 2 retries)
- `/status` shows current workflow progress
- Data persisted to `bot_data.json`

### Phase 2 — Parallel Execution

- Run independent steps concurrently
- Merge outputs from parallel agents
- Handle conflicts (e.g., both agents wrote same file)

### Phase 3 — Templates & Presets

- `/build website` → known multi-phase workflow
- `/build api` → API-focused workflow
- `/build script` → single-file workflow
- Saved templates for common tasks

### Phase 4 — Human-in-the-Loop

- Pause at review steps: "Plan ready. Approve? (y/n)"
- User can send feedback mid-workflow
- `/rework <step>` to retry a specific step

---

## Telegram User Flow

```
User: /build personal website
Bot: 🔍 Analyzing your request...
Bot: 📋 Generated workflow with 6 phases:
     1. Research → Claude
     2. Architecture Plan → Claude
     3. Plan Review → Codex
     4. UI Build → Gemini
     5. Code + Backend → Claude
     6. Documentation → Gemini

Bot: Starting Phase 1/6 — Research
Bot:   [Claude] Analyzing requirements... ✅ (12s)

Bot: Starting Phase 2/6 — Architecture Plan
Bot:   [Claude] Creating plan... ✅ (45s)

Bot: Starting Phase 3/6 — Plan Review
Bot:   [Codex] Validating plan... ✅ (28s)

Bot: Starting Phase 4/6 — UI Build
Bot:   [Gemini] Generating pages... ⚠️ Failed (timeout)
Bot:   [Gemini] Retry 1/2... ✅ (35s)

Bot: Starting Phase 5/6 — Code + Backend
Bot:   [Claude] Writing backend... ✅ (90s)
Bot:   [Code Review] Claude reviewing... ⚠️ Issues found
Bot:   [Claude] Reworking... ✅ (30s)

Bot: Starting Phase 6/6 — Documentation
Bot:   [Gemini] Writing docs... ✅ (25s)

Bot: ✅ **Build Complete!** (4m 45s)
    Files created:
    • index.html
    • style.css
    • blog.html
    • app.py
    • README.md
    📁 workspace/12345/

User: /status
Bot: 📊 **Last Build: personal website**
    Status: ✅ Complete
    Duration: 4m 45s
    Phases: 6/6 ✅
    Files: 5
```

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Agent CLI not installed | Skip step, log warning, continue |
| Agent timeout (>120s) | Retry once, then skip |
| Review step fails | Mark step for rework, rerun build step |
| Planner fails to generate JSON | Retry with stricter prompt, then error |
| User cancels | Kill running subprocess, save partial state |

---

## Files to Modify

| File | Change |
|------|--------|
| `bot/main.py` | Register `/build`, `/status`, `/cancel` handlers |
| `bot/persistence.py` | Add `workflows` dict to data model |
| `bot/config.py` | Add workflow-related env vars |
| `bot/agents.py` | No changes (reuses existing agent routing) |

---

## Open Questions

1. Should the planner use the user's Ollama model or always a specific model?
2. Max workflow execution time before forced timeout?
3. Should completed workflows be auto-cleaned from disk?
4. Parallel execution: merge strategy when two agents modify the same file?
