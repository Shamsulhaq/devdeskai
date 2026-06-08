# Bug Report — DevDeskAI

Reviewer: Claude Opus 4.7
Date: 2026-06-07
Scope: full codebase (`bot/` package, ~1300 LOC)

Bugs are grouped by severity. Each entry has a fixed ID (`OPUS-NNN`) so they can be referenced from commits and follow-up reviews.

---

## Critical

### OPUS-001 — `/announce` auth bypass
**File:** `bot/handlers/admin.py:17`
**Severity:** Critical (security)

```python
if config.ADMIN_IDS and uid not in config.ADMIN_IDS:
    await update.message.reply_text("Not authorized.")
    return
```

When `ADMIN_IDS` is unset (the default in `.env.example`), `config.ADMIN_IDS` is an empty `set` → falsy → the whole guard is skipped. **Any user can broadcast to every user the bot has ever spoken to.**

**Fix:**
```python
if not config.ADMIN_IDS or uid not in config.ADMIN_IDS:
    await update.message.reply_text("Not authorized.")
    return
```

Also consider failing closed at startup: if `ADMIN_IDS` is empty, refuse to register the `/announce` handler at all.

---

### OPUS-002 — Sync calls block the asyncio event loop
**Files:**
- `bot/handlers/core.py:73` and `:95` — `get_client().list()` (sync HTTP to Ollama).
- `bot/handlers/productivity.py:44-45` — `DDGS()` context manager + `ddgs.text(...)` (sync HTTP).
- `bot/handlers/media.py:124` — `base64.b64encode(img_bytes)` on multi-MB photos.

**Severity:** Critical (performance / availability)

`ollama.generate` correctly wraps the Ollama call with `asyncio.to_thread`, but these other paths run synchronously on the loop thread. While one of them is in flight, *every other user's message stalls*.

**Fix:** wrap each in `await asyncio.to_thread(...)`. Example for `list_models`:
```python
data = await asyncio.to_thread(get_client().list)
```

---

### OPUS-003 — Ollama error strings persisted as legitimate replies
**File:** `bot/ollama.py:96-102` + every caller of `generate`

**Severity:** Critical (correctness)

`generate` returns user-facing error strings (`"Ollama is unreachable…"`, `f"Error: {e}"`) on failure. Callers then do:

```python
persistence.chat_histories[uid].append({"user": ..., "assistant": reply})
```

So error messages are saved as past assistant turns and fed back to the model on the next request, poisoning the conversation context.

**Fix:** raise an exception (or return a `Result`/sentinel) and let the caller decide whether to persist. Only persist on success.

---

### OPUS-004 — Unbounded persisted growth
**Files:** `bot/persistence.py:122`, `bot/ollama.py:143`

**Severity:** Critical (resource exhaustion)

Two unbounded structures:
1. `chat_histories[uid]` is appended on every message forever. `MAX_HISTORY` only trims what's *sent* to Ollama, not what's *stored*.
2. `stats["user_ids"]` is a `list` checked with `if user_id not in stats["user_ids"]` — O(n) per message **and** unbounded.

Both compound the write-amplification problem (OPUS-005) — every message rewrites the entire growing file.

**Fix:**
- Cap stored history at the same `MAX_HISTORY` (or slightly larger for context).
- Switch `user_ids` to a `set` (serialize as sorted list).

---

### OPUS-005 — Write amplification on every message
**File:** `bot/persistence.py:81-118` + every handler that calls `save_async`

**Severity:** Critical (scalability)

Every state mutation triggers a full re-serialization of *all* user state to JSON and an atomic file rewrite. At ~100 users with growing histories this is MBs per message. The `_save_lock` prevents overlap but doesn't reduce the cost.

**Fix (in increasing effort):**
1. Debounce: coalesce saves to at most one every N seconds.
2. Per-user files.
3. Switch to SQLite.

---

## High

### OPUS-006 — `reply_long` can break HTML parsing on splits
**File:** `bot/ollama.py:121-129`

**Severity:** High (correctness)

When neither `\n` nor space is found within `MAX_LEN = 4096`, the function splits at `MAX_LEN` as a raw character index. HTML tags like `<code>...</code>` get cut open across two Telegram messages, both of which fail to parse with `ParseMode.HTML` and either error out or render escaped.

**Fix:** escape *after* splitting plain text, or detect open tags and only split at safe boundaries.

---

### OPUS-007 — Agent CLI invocations are likely wrong for 5 of 6 agents
**File:** `bot/agents.py:13-44`

**Severity:** High (correctness)

`claude -p` is the documented one-shot flag. The other five (`opencode`, `codex`, `qwen`, `gemini`, `copilot`) are configured as bare `<binary> <prompt>` — most of these CLIs expect a `--prompt` flag, a subcommand, or stdin. End-to-end verify each before claiming the feature works.

**Fix:** test each CLI against the actual binary; update `AGENTS_CONFIG` with the right invocation per tool.

---

### OPUS-008 — `/announce` ignores Telegram flood limits
**File:** `bot/handlers/admin.py:25-32`

**Severity:** High (availability)

The loop sends to every user back-to-back with no sleep, no batching, no per-chat throttling. Telegram's global limit is ~30 messages/sec; broadcasting to a thousand users gets the bot temporarily banned.

**Fix:** add `await asyncio.sleep(0.05)` between sends, or use a bounded-concurrency worker pool. Handle `RetryAfter` exceptions.

---

### OPUS-009 — `enter_agent` command parsing is fragile
**File:** `bot/handlers/agents.py:28-29`

**Severity:** High (correctness)

```python
parts = text.split(maxsplit=1)
agent_name = parts[0].lstrip("/").lower()
```

Breaks on `/claude@MyBotName some prompt` (the form Telegram uses when the bot is invoked in a group). The trailing `@botname` becomes part of `agent_name` and no agent matches.

**Fix:** strip `@<bot_username>` from the first token, or use `update.message.entities` to extract the command.

---

### OPUS-010 — `handle_custom` crashes when message text is None
**File:** `bot/handlers/custom.py:13`

**Severity:** High (robustness)

```python
cmd = update.message.text.split()[0].lstrip("/").lower()
```

Crashes with `AttributeError` if `update.message.text` is `None` (e.g. a command sent as a caption on a photo, or some edited-message edge cases). The whole handler then fails.

**Fix:**
```python
text = update.message.text or ""
if not text:
    return
cmd = text.split()[0].lstrip("/").lower()
```

---

### OPUS-011 — `_get_lock` lazy init race
**File:** `bot/persistence.py:28-32`

**Severity:** High (correctness, low probability)

```python
def _get_lock() -> asyncio.Lock:
    global _save_lock
    if _save_lock is None:
        _save_lock = asyncio.Lock()
    return _save_lock
```

Two coroutines hitting this on cold start could each see `_save_lock is None` and construct separate locks, defeating the serialization. In CPython under cooperative async this is mostly safe (no `await` between check and assignment), but fragile.

**Fix:** construct at module level: `_save_lock = asyncio.Lock()`. (Safe because telegram-ext sets up the loop before any handler runs.)

---

### OPUS-012 — PII / privacy exposure in `bot_data.json`
**Files:** `bot/handlers/media.py:260`, `bot/ollama.py:143`

**Severity:** High (privacy)

`bot_data.json` accumulates every user's full chat history + up to 50 000 chars per user of uploaded document content, in plaintext, forever. There's no retention policy and the README's Docker example bind-mounts this file directly. Single file leak → every user's conversations and docs are compromised.

**Fix:**
- Drop `user_docs[uid]` after `/ask` returns (or TTL).
- Document the privacy implications in README.
- Consider not persisting `chat_histories` at all (memory-only).

---

## Medium

### OPUS-013 — No per-user rate limiting
**File:** `bot/ollama.py:78`

**Severity:** Medium (availability)

A single spammy user can queue unlimited Ollama calls, saturating the backend and starving every other user. Each per-message handler also does its own save and replies, multiplying the cost.

**Fix:** per-user `asyncio.Semaphore(1)` keyed by `uid`, plus a coarse global semaphore matched to Ollama concurrency.

---

### OPUS-014 — Persistence schema is unversioned
**File:** `bot/persistence.py:60-77`

**Severity:** Medium (maintainability)

The loader expects specific keys (`histories`, `prompts`, …) but there's no `schema_version` field. Any rename or restructure = silent data loss for existing users.

**Fix:** add `"schema_version": 1` to the saved dict and a migration ladder in `load()`.

---

### OPUS-015 — `error_handler` may log PII
**File:** `bot/main.py:25-27`

**Severity:** Medium (privacy)

```python
logger.error("Update %s caused error %s", update, context.error)
```

The full `Update` object includes user message text, captions, file metadata, and user ID. With default logging that goes to stdout (and any aggregator). Log `update.update_id` and the error only.

---

### OPUS-016 — `bot/handlers/media.py::handle_document` leaks exception details
**File:** `bot/handlers/media.py:267-268`

**Severity:** Medium (information disclosure)

```python
except Exception as e:
    await update.message.reply_text(f"Document error: {e}")
```

Sends raw exception messages (including paths, traceback fragments from libraries) back to the user. Same pattern in `productivity.py:65`, `productivity.py:103`, `custom.py:29`, `agents.py:53`.

**Fix:** `logger.exception(...)` + reply with a generic message.

---

### OPUS-017 — `MAX_DOC_CHARS` mismatch silently truncates `/ask`
**Files:** `bot/handlers/productivity.py:82` (`MAX_DOC_CHARS = 8000`) vs. `bot/handlers/media.py:22` (`MAX_DOC_CHARS_STORED = 50_000`)

**Severity:** Medium (correctness)

The bot stores up to 50k chars of a document, but `/ask` only sends the first 8k to the model. Users with 30-page PDFs get answers based on the cover page and silently miss the rest.

**Fix:** either unify the constants, or implement chunking + retrieval if you genuinely need 50k support.

---

### OPUS-018 — Document text decoded with `errors="replace"`
**File:** `bot/handlers/media.py:246`

**Severity:** Medium (correctness)

`.csv`, `.json`, `.md`, `.txt` are decoded as UTF-8 with `errors="replace"`. cp1252/Latin-1 exports get silently corrupted (`�` replacement chars sprinkled through the doc) and the model is asked to answer questions against garbage.

**Fix:** try UTF-8, then a couple of common fallbacks (`utf-8-sig`, `cp1252`), or detect with `chardet`/`charset-normalizer`.

---

### OPUS-019 — `_safe_workspace` redundant check + brittle string compare
**File:** `bot/handlers/media.py:292-300`

**Severity:** Medium (clarity)

```python
if not str(user_dir).startswith(str(base) + os.sep) and user_dir != base:
    raise ValueError(...)
```

Since `user_dir = base / str(user_id)`, the `user_dir != base` branch is always true. `Path.is_relative_to(base)` (Python 3.9+) is cleaner and correct on edge cases (trailing separators, symlinks).

**Fix:**
```python
if not user_dir.is_relative_to(base):
    raise ValueError(...)
```

---

### OPUS-020 — `agents.run_cli` always appends stderr
**File:** `bot/agents.py:100`

**Severity:** Medium (UX)

```python
return (out + f"\n\n[stderr]\n{err}") if err else out
```

Many CLIs write progress bars, telemetry warnings, or auth notices to stderr even on success. Users see successful runs cluttered with `[stderr] downloading...`. Only append on non-zero exit.

**Fix:**
```python
if proc.returncode != 0 and err:
    return out + f"\n\n[stderr]\n{err}"
return out
```

---

### OPUS-021 — Markdown formatting sent without `parse_mode`
**Files:** `bot/handlers/admin.py:9-12`, `bot/handlers/core.py:67`, `bot/handlers/agents.py:19`, several others

**Severity:** Medium (UX)

Many handlers send messages containing `**bold**` Markdown but don't set `parse_mode=ParseMode.MARKDOWN`. They render as literal asterisks in Telegram.

**Fix:** add `parse_mode=ParseMode.MARKDOWN` everywhere `**` / `_..._` appears, or drop the asterisks.

---

### OPUS-022 — `CUSTOM_CMD_*_PROMPT` parsing registers ghost commands
**File:** `bot/config.py:43-49`

**Severity:** Medium (correctness)

The loop iterates `os.environ` and treats *every* `CUSTOM_CMD_*` as a command unless it ends in `_PROMPT`. But it also looks up `CUSTOM_CMD_<NAME>_PROMPT` as an override. The unclear consequence: `CUSTOM_CMD_FOO_PROMPT=...` set alone (without a `CUSTOM_CMD_FOO`) registers nothing — fine — but `CUSTOM_CMD_FOO=...` with `CUSTOM_CMD_FOO_PROMPT=...` registers `/foo` with the second value, surprising users who set both.

**Fix:** document precedence in README; consider raising on conflict.

---

## Low

### OPUS-023 — `reply_long` overloaded `target` parameter
**File:** `bot/ollama.py:107-115`

**Severity:** Low (clarity)

```python
async def reply_long(target: Update | Callable, text: str) -> None:
    if callable(target) and not hasattr(target, "message"):
        reply = target
    else:
        reply = target.message.reply_text
```

The dual-purpose parameter and `hasattr` check are hard to read. Split into `reply_long_to_update(update, text)` and `reply_long_via(send_fn, text)`.

---

### OPUS-024 — `MAX_PREDICT_TOKENS = 1024` hardcoded
**File:** `bot/ollama.py:15`

**Severity:** Low (configurability)

Hardcoded at 1024 — too small for code-generation use cases, way too large for `/persona pirate`-style chats. Should be env-driven (`MAX_PREDICT_TOKENS` in `.env`).

---

### OPUS-025 — `switch_model` does two RTTs per switch
**File:** `bot/handlers/core.py:84-106`

**Severity:** Low (performance)

Calls `get_client().list()` to validate the model name on every `/switch`. Cache the list with a short TTL (5–10s) to avoid hammering Ollama when users explore models.

---

### OPUS-026 — `exit_agent` writes even when no-op
**File:** `bot/handlers/agents.py:68-78`

**Severity:** Low (efficiency)

Calls `persistence.user_agent.pop(uid, None)` and `save_async` unconditionally. If the user wasn't in agent mode, `pop` is a no-op but the save still rewrites the entire JSON file. Guard:

```python
if uid not in persistence.user_agent:
    await update.message.reply_text("Not in agent mode.")
    return
```

---

### OPUS-027 — Streaming consumed but not surfaced
**File:** `bot/ollama.py:71-75`

**Severity:** Low (UX / waste)

`stream=True` is requested, then chunks are concatenated and only sent to Telegram once complete. Either drop streaming (use the non-stream API; simpler) **or** actually surface it by progressively editing the Telegram message (`bot.edit_message_text`) for live-typing UX.

---

### OPUS-028 — `BUG-NNN fix:` comments will rot
**Files:** ~24 sites across `bot/`

**Severity:** Low (maintainability)

Inline comments like `# BUG-024 fix: proper type hint` reference an external bug catalog (`minimax-m3-found-bugs.md`). Future maintainers refactoring these lines have no easy way to tell whether the comment still applies. Delete obvious ones; keep only where the constraint is non-obvious (e.g. "must run in thread or it blocks the loop").

---

### OPUS-029 — `handle_voice` double-buffers audio
**File:** `bot/handlers/media.py:159-165`

**Severity:** Low (efficiency)

Downloads voice into `io.BytesIO()`, then writes that buffer to a `NamedTemporaryFile`. `file.download_to_drive(path)` writes straight to disk and skips the in-memory copy.

---

### OPUS-030 — No tests, no linter, no type checker
**Severity:** Low (process)

Zero test suite, no `pyproject.toml`, no `ruff`/`mypy` configs. For a project with this many feature surfaces (Whisper, DDGS, PDF, six agent CLIs, webhook *and* polling), adding even minimal CI would catch most of OPUS-001 through OPUS-010 before merge.

Recommended starting points:
- `ruff check bot/` (zero-config).
- `pytest` tests around `persistence` round-trip, `agents.run_cli` (mock subprocess), `ollama.build_messages` (no I/O).

---

## Architecture / design observations (not bugs)

These are not defects but tensions that will get worse as the project grows. Listed for visibility.

- **A1** Module-level mutable globals in `bot/persistence.py` make testing and concurrent scenarios hard. A `State` dataclass stored in `application.bot_data` would unblock unit tests.
- **A2** No repository / service layer — handlers reach directly into dicts and remember to call `save_async`. A `UserStore` wrapper would centralize the contract.
- **A3** Optional-dependency `try/except ImportError + *_AVAILABLE` is duplicated three times. A single `bot/features.py` capability map would deduplicate.
- **A4** `handle_message` does three different things (agent mode / photo+text / plain chat) by branching on `persistence.user_agent` and `update.message.photo`. Splitting into named handlers would be clearer.

---

## Summary

| Severity | Count |
|---|---|
| Critical | 5 |
| High | 7 |
| Medium | 10 |
| Low | 8 |
| **Total** | **30** |

The most urgent items to address:

1. **OPUS-001** — auth bypass on `/announce`. Ship a fix today.
2. **OPUS-003** — error strings poisoning chat history. Easy fix, big correctness win.
3. **OPUS-002** — sync calls blocking the event loop. Affects every user.
4. **OPUS-004 + OPUS-005** — unbounded growth + write amplification. Buys time before scaling pain.
5. **OPUS-007** — verify the five non-Claude agent CLIs actually work end-to-end; the feature may be silently broken.
