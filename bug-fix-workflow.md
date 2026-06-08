# Bug-fix workflow — parallel execution plan

**Source of truth:** `bug-report-by-opus.md` (30 bugs, OPUS-001 … OPUS-030).
**Goal:** fix all 30 bugs with maximum parallelism while preventing merge conflicts.

---

## Strategy

Each parallel work-group runs in its **own git worktree** (one branch per group). File ownership is partitioned so two groups never touch the same file. After all parallel groups merge, two sequential cleanup steps run last (because they're cross-cutting).

```
                  ┌─────── A (security) ───────┐
                  │                            │
                  ├─────── B (persistence) ────┤
                  │                            │
   master ───────►├─────── C (ollama+callers) ─┤───► merge ───► G (markdown+comments) ───► H (CI/tests) ───► done
                  │                            │       │
                  ├─────── D (media) ──────────┤       │
                  │                            │       │
                  ├─────── E (agents) ─────────┤       │
                  │                            │       │
                  └─────── F (core+prod) ──────┘       │
                                                       │
                                                       └─ (G is serial because it touches every file)
```

---

## File ownership matrix

| Group | Files owned | Bugs |
|---|---|---|
| **A — Security** | `bot/handlers/admin.py`, `bot/main.py`, `bot/config.py` | 001, 008, 015, 022 |
| **B — Persistence** | `bot/persistence.py` | 004, 005, 011, 014 |
| **C — Ollama** | `bot/ollama.py` + every `generate()` caller (`core`, `media`, `productivity`, `custom`, `agents`) | 003, 006, 013, 023, 024, 027 |
| **D — Media** | `bot/handlers/media.py` (+ `MAX_DOC_CHARS` in `productivity.py`) | 002 (base64), 016, 017, 018, 019, 029 |
| **E — Agents** | `bot/agents.py`, `bot/handlers/agents.py`, `bot/handlers/custom.py` | 007 (research only), 009, 010, 020, 026 |
| **F — Core/prod** | `bot/handlers/core.py`, `bot/handlers/productivity.py` | 002 (DDGS + list calls), 012, 025 |
| **G — Cleanup** | every file (sequential) | 021, 028 |
| **H — Tooling** | new files only (`pyproject.toml`, `tests/`, `.github/workflows/`) | 030 |

### Known overlap risk
- **C vs. A/D/E/F**: Group C migrates every `generate()` caller to handle exceptions. It edits files that A, D, E, F also own. **Mitigation:** C edits ONLY the lines that catch `OllamaError` and reply; it does not touch any other logic in those files. Groups A/D/E/F instructed not to refactor the `generate()` call sites — leave them as-is, C will rewire them.
- **D vs. F on `productivity.py`**: D owns the `MAX_DOC_CHARS` constant only; F owns the rest. Hard-coded contract in their prompts.

---

## Merge order

1. Run A, B, D, E, F in parallel (5 worktrees).
2. Merge all 5 into a staging branch.
3. Run C against the staging branch (its scope is the whole codebase).
4. Merge C.
5. Run G sequentially (markdown audit + comment cleanup) — must come after all source changes.
6. Run H (tests + CI) against the final state.

This puts C *after* the file-local groups so it migrates the *final* caller code, not the original.

> Revised: simpler alternative — run all 6 groups (A–F) in parallel from master, accepting that C will need manual merge resolution on the caller migrations. Pick this if you'd rather not wait for the staging round-trip.

---

## Per-agent prompt template

Each agent receives:

1. **Context:** path to `bug-report-by-opus.md` for the exact bug specs.
2. **File ownership:** explicit list of files they may edit. Anything else is read-only.
3. **Non-goals:** which adjacent refactors to skip (so they don't stray into another group's territory).
4. **Verification:** import-only smoke test before reporting done:
   ```bash
   BOT_TOKEN=x python3 -c "import bot.main; print('OK')"
   ```
5. **Output:** a short report listing which OPUS-NNN bugs were fixed, with file:line for each, plus anything that couldn't be done.

---

## What I won't touch without confirmation

- **OPUS-007** (agent CLI invocations): research-only because the binaries aren't installed in this environment. Group E produces a doc of what *should* go in `AGENTS_CONFIG`; the actual swap waits on a human running `claude --help`, `qwen --help`, etc.
- **OPUS-012** (drop persisted history): the report flags persistence as a privacy concern. Group F drops `user_docs` after `/ask` (safe). Dropping `chat_histories` entirely would be a behavior change — left for explicit user decision.
- **Branch protection / force pushes**: no destructive git operations. Each group commits to its own branch; merges go through normal `git merge`.

---

## Status

Tracked via the task list. Tasks A–H are created and pending. Dispatch waits on user confirmation.
