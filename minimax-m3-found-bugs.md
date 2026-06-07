# DevDeskAI — Found Bugs

> Code review audit of `master-v2` branch. All issues found during deep review of `bot/agents.py`, `bot/persistence.py`, `bot/handlers/media.py`, `bot/handlers/agents.py`, `bot/ollama.py`, and `bot/main.py`.

---

## Summary

| Severity | Count |
|----------|-------|
| 🔴 Critical | 4 |
| 🟠 High | 8 |
| 🟡 Medium | 14 |
| **Total** | **26** |

---

## 🔴 Critical (Security / Data Loss)

### BUG-001: Shell Command Injection in Agent Runner
- **File:** `bot/agents.py:63`
- **Category:** Security — RCE
- **Description:** User-supplied `prompt` is interpolated directly into a shell string passed to `asyncio.create_subprocess_shell`.
- **Proof of Concept:**
  ```python
  # User sends: /claude "; touch /tmp/pwned; echo "
  # full_cmd becomes: claude -p ""; touch /tmp/pwned; echo ""
  # Shell parses it as: claude -p "" ; touch /tmp/pwned ; echo ""
  ```
- **Impact:** Remote code execution. Anyone using `/claude`, `/opencode`, `/codex`, `/copilot`, `/qwen`, `/gemini` can run arbitrary commands as the bot user.
- **Fix:** Use `asyncio.create_subprocess_exec(*argv)` with `shlex.split(info["run_cmd"]) + [prompt]`.

---

### BUG-002: Non-Atomic JSON Writes
- **File:** `bot/persistence.py:54-55`
- **Category:** Data loss
- **Description:** `with open(path, "w") as f: json.dump(...)` overwrites the file in-place. If the process is killed mid-write (SIGKILL, OOM, power loss), the file is truncated to partial bytes. On next load, `json.load` raises `JSONDecodeError` which is not caught, crashing the bot.
- **Impact:** All user data (chat history, settings, uploaded docs) lost on any unclean shutdown.
- **Fix:** Write to `tempfile.mkstemp(...)` then `os.replace(tmp, path)`. Wrap `load()` in `try/except json.JSONDecodeError` and back up corrupted files.

---

### BUG-003: PDF Resource Leak
- **File:** `bot/handlers/media.py:177-180`
- **Category:** Resource leak
- **Description:**
  ```python
  pdf_doc = fitz.open(stream=raw.read(), filetype="pdf")
  for page in pdf_doc:
      text += page.get_text() + "\n"
  pdf_doc.close()
  ```
  If `get_text()` raises (corrupt PDF, encrypted, etc.), `pdf_doc.close()` is never called. PyMuPDF holds native memory and possibly file handles.
- **Impact:** Memory leak on bad PDFs. Process can OOM after several failed uploads.
- **Fix:** Use `with fitz.open(...) as pdf_doc:` context manager.

---

### BUG-004: Whisper Model Blocks Event Loop
- **File:** `bot/handlers/media.py:41, 45`
- **Category:** Async / runtime
- **Description:** `_whisper_model = WhisperModel("base", device="auto")` is synchronous and takes 10-30 seconds on first use. Called from an async handler, it blocks the entire event loop. While loading, no other Telegram messages are processed.
- **Impact:** Bot appears frozen for all users during first voice message. A `/start` command sent during this time gets no response.
- **Fix:** Load at startup (eager init) or use `await asyncio.to_thread(WhisperModel, "base", device="auto")`.

---

## 🟠 High (Security / Stability)

### BUG-005: Subprocess Not Killed on Timeout
- **File:** `bot/agents.py:71`
- **Category:** Resource leak / runtime
- **Description:** `asyncio.wait_for(proc.communicate(), timeout=300)` cancels the await but does not kill the child process. The agent CLI keeps running, holding workspace file locks and consuming RAM.
- **Impact:** Zombie processes accumulate. Subsequent workflow steps may collide with a still-running agent.
- **Fix:**
  ```python
  except asyncio.TimeoutError:
      proc.kill()
      await proc.wait()
      return "Agent timed out (300s)."
  ```

---

### BUG-006: Sync Ollama Call Blocks Event Loop
- **File:** `bot/ollama.py:69-78`
- **Category:** Async / runtime
- **Description:** The `ollama` Python client is synchronous. Calling `client.chat(...)` with a streaming generator and iterating it from async code blocks the event loop for 5-60 seconds (full response time).
- **Impact:** All concurrent users see degraded responsiveness while one user gets a long response. Telegram may disconnect the bot.
- **Fix:** Wrap iteration in `asyncio.to_thread(...)`.

---

### BUG-007: No Corrupted Data File Recovery
- **File:** `bot/persistence.py:21-23`
- **Category:** Data loss
- **Description:** `load()` only catches `FileNotFoundError`. A corrupted JSON file (from BUG-002 or manual edit) raises `JSONDecodeError` and crashes the bot on startup.
- **Impact:** Bot cannot start after any data corruption event. All settings lost.
- **Fix:** Add `except json.JSONDecodeError` that backs up the corrupt file and starts fresh.

---

### BUG-008: Predictable Temp File Path
- **File:** `bot/handlers/media.py:116`
- **Category:** Security — symlink attack
- **Description:** `f"/tmp/voice_{uid}_{int(time.time())}.ogg"` creates a file in a world-writable directory with a predictable name. A local attacker could pre-create a symlink at this path pointing to a sensitive file, and Whisper would read it.
- **Impact:** Local privilege escalation / information disclosure.
- **Fix:** Use `tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)` and clean up in `finally`.

---

### BUG-009: Unbounded Document Storage
- **File:** `bot/handlers/media.py:190` + `bot/persistence.py:13`
- **Category:** DoS / disk fill
- **Description:** `persistence.user_docs[uid] = text` stores the entire uploaded document text in a dict that is JSON-serialized on every save. A 20MB PDF becomes a 30MB JSON file. `save()` runs on every chat turn. No cap, no eviction.
- **Impact:** Disk fills over time. Each save takes longer. Eventually OOM or disk full.
- **Fix:** Cap raw upload size to 20MB. Cap stored text to 50,000 chars. Save large docs to `workspace/<uid>/docs/<hash>.txt` and store only the path.

---

### BUG-010: Tmp File Cleanup Not in `finally`
- **File:** `bot/handlers/media.py:117-127`
- **Category:** Resource leak
- **Description:** If `wh.transcribe()` raises, `os.remove(ogg_path)` is skipped. Files accumulate in `/tmp`.
- **Impact:** Slow `/tmp` fill on errors.
- **Fix:** Wrap in `try/finally`.

---

### BUG-011: No Size Limit on Photo Download
- **File:** `bot/handlers/media.py:77-81`
- **Category:** Performance / DoS
- **Description:** `update.message.photo[-1]` is the highest-resolution photo, up to 10MB. Base64-encoding adds 33% overhead, sent as 13MB to Ollama on every image query.
- **Impact:** Slow image responses, high RAM usage.
- **Fix:** Use `photo[1]` (medium resolution, ~320px) or `photo[2]` (~800px). Cap to 1024px on the long side.

---

### BUG-012: `save()` Called on Every Chat Turn
- **File:** `bot/ollama.py:119` (and other handlers)
- **Category:** Performance
- **Description:** Every chat response calls `persistence.save(config.DATA_FILE)`, which serializes the entire state to JSON. With 10 active users, that's 10 file writes/second. Each write is 30-100ms.
- **Impact:** Bot becomes slow under load. Disk thrashing.
- **Fix:** Debounce saves (e.g., flush every 5s) or use an in-memory queue + background flusher.

---

## 🟡 Medium (Code Quality / Edge Cases)

### BUG-013: Module-Level Mutable State
- **File:** `bot/persistence.py:7-15`
- **Category:** Testability
- **Description:** Global dicts at module scope make unit testing impossible without import-time tricks.
- **Fix:** Wrap in a `class DataStore` singleton. Or add a comment marking them as "singleton state, do not import in tests."

---

### BUG-014: Unbounded `user_agent_history`
- **File:** `bot/persistence.py:12`
- **Category:** Memory growth
- **Description:** Every agent invocation appends to `user_agent_history[uid]`. No cap. Long-running users see this grow forever.
- **Fix:** Use `deque(maxlen=50)` or trim in `save()`:
  ```python
  for uid in user_agent_history:
      user_agent_history[uid] = user_agent_history[uid][-50:]
  ```

---

### BUG-015: Race Condition on Concurrent Saves
- **File:** `bot/persistence.py:42-55`
- **Category:** Data integrity
- **Description:** Multiple async handlers can call `save()` concurrently. Last write wins; intermediate state may be lost.
- **Fix:** Add `asyncio.Lock` to serialize saves.

---

### BUG-016: `update.message.text` May Be Empty
- **File:** `bot/handlers/agents.py:24`
- **Category:** Runtime error
- **Description:** `update.message.text.split()[0]` raises `IndexError` if text is empty. `CommandHandler` should never fire on empty text, but defensive coding is good.
- **Fix:**
  ```python
  parts = (update.message.text or "").split()
  if not parts:
      return
  agent_name = parts[0].lstrip("/").lower()
  ```

---

### BUG-017: `WORKSPACE_DIR` Not Validated
- **File:** `bot/config.py:18` + `bot/handlers/agents.py:45`
- **Category:** Security / path
- **Description:** `WORKSPACE_DIR` from env is used in `os.makedirs` and `os.path.join`. A malicious `.env` (`WORKSPACE_DIR=/etc`) would attempt to create user dirs in system paths. `os.path.join` does not prevent `..` traversal.
- **Fix:** Validate at config load:
  ```python
  WORKSPACE_DIR = os.path.abspath(os.getenv("WORKSPACE_DIR", os.path.join(os.getcwd(), "workspace")))
  os.makedirs(WORKSPACE_DIR, exist_ok=True)
  ```

---

### BUG-018: Workspace Files Not Cleaned Up
- **File:** `bot/handlers/agents.py:45-46`
- **Category:** Disk growth
- **Description:** `workspace/<uid>/` grows indefinitely. Agents may write hundreds of files per task.
- **Fix:** Add per-user size cap, age-based cleanup, or a `/clean` command.

---

### BUG-019: `PERSONAS["default"]` Captured at Import Time
- **File:** `bot/ollama.py:125`
- **Category:** Edge case
- **Description:** `PERSONAS = { "default": config.SYSTEM_PROMPT, ... }` — if `SYSTEM_PROMPT` is `None` or empty, all "default" persona lookups return `None`. The LLM receives `role: "system", content: null` and errors.
- **Fix:** Ensure `config.SYSTEM_PROMPT` has a fallback. Validate at config load.

---

### BUG-020: No Retry on Ollama Connection Failure
- **File:** `bot/ollama.py:69`
- **Category:** Reliability
- **Description:** If Ollama is temporarily unavailable (e.g., during a model reload), every request fails immediately.
- **Fix:** Wrap in a single retry with 1s backoff.

---

### BUG-021: No Max Tokens on Generation
- **File:** `bot/ollama.py:69-73`
- **Category:** Performance / cost
- **Description:** No `num_predict` set. A long question can produce 4000+ tokens, which is then split into multiple Telegram messages anyway.
- **Fix:** Add `opts["num_predict"] = 1024` (or similar).

---

### BUG-022: `reply_long` Uses `hasattr` for Type Dispatch
- **File:** `bot/ollama.py:87`
- **Category:** Code quality
- **Description:** `reply = target.message.reply_text if hasattr(target, "message") else target` is fragile. `Callable` types can coincidentally have a `message` attribute.
- **Fix:**
  ```python
  if callable(target):
      reply = target
  else:
      reply = target.message.reply_text
  ```

---

### BUG-023: Handler Order Is Fragile
- **File:** `bot/main.py:65-84`
- **Category:** Maintainability
- **Description:** Message handler registration order is critical. A new filter added between lines 66 and 82 could shadow the catch-all text/photo handler.
- **Fix:** Add comment block explaining ordering, or use a single dispatcher with explicit priority.

---

### BUG-024: `error_handler` Type Hints Are Wrong
- **File:** `bot/main.py:24-25`
- **Category:** Type safety
- **Description:** `context: object` and `hasattr(context, 'error')` are workarounds. Real type is `ContextTypes.DEFAULT_TYPE`.
- **Fix:**
  ```python
  from telegram.ext import ContextTypes
  
  async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
      logger.error("Update %s caused error %s", update, context.error)
  ```

---

### BUG-025: Webhook Port Not Validated
- **File:** `bot/main.py:88-89` + `bot/config.py:14`
- **Category:** Runtime error
- **Description:** Telegram webhooks only accept ports 443, 80, 88, 8443. Any other port causes `BadRequest` from Telegram at runtime.
- **Fix:** Validate at config load:
  ```python
  if config.WEBHOOK_PORT not in (443, 80, 88, 8443):
      raise ValueError(f"Invalid WEBHOOK_PORT: {config.WEBHOOK_PORT}. Allowed: 443, 80, 88, 8443")
  ```

---

### BUG-026: Webhook URL Not Validated as HTTPS
- **File:** `bot/main.py:88-89` + `bot/config.py:13`
- **Category:** Runtime error
- **Description:** Telegram requires HTTPS for webhooks. HTTP URLs fail at runtime.
- **Fix:** Validate at config load:
  ```python
  if not config.WEBHOOK_URL.startswith("https://"):
      raise ValueError("WEBHOOK_URL must be HTTPS for Telegram webhooks")
  ```

---

## Recommended Fix Priority

| Order | Bug | Rationale |
|-------|-----|-----------|
| 1 | BUG-001 | RCE — security-critical |
| 2 | BUG-002 | Data loss on any unclean shutdown |
| 3 | BUG-006 | UX bug, affects all users |
| 4 | BUG-003 | Resource leak on user error |
| 5 | BUG-004 | UX bug, blocks bot during first voice msg |
| 6 | BUG-005 | Resource leak / workflow correctness |
| 7 | BUG-009 | DoS, grows over time |
| 8 | BUG-007 | Recovery from BUG-002 |
| 9 | BUG-008 | Local security |
| 10 | BUG-012 | Performance under load |
| 11-26 | * | Code quality, edge cases, polish |

---

## Test Cases Needed

```python
# tests/test_security.py
def test_agent_prompt_injection_escaped():
    """Verify a prompt with shell metachars is passed as a single arg."""
    # Mock create_subprocess_exec, send prompt: ; touch /tmp/pwned ; echo
    # Assert argv ends with the raw prompt string, not split

# tests/test_persistence.py
def test_atomic_write_does_not_truncate_on_signal():
    """Verify a SIGKILL mid-write does not corrupt bot_data.json."""
    # Spawn save() in subprocess, send SIGKILL at random point
    # Reload, verify file is valid JSON or doesn't exist (not partial)

def test_corrupted_file_recovers():
    """Verify a corrupt bot_data.json is backed up and bot starts."""
    # Write garbage to data file
    # Call load(), assert new file created with .corrupt.<ts> suffix

# tests/test_media.py
def test_pdf_close_on_exception():
    """Verify pdf_doc.close() runs even when get_text() raises."""
    # Mock get_text to raise, assert close() called

def test_photo_size_capped():
    """Verify the largest photo is not downloaded."""
    # Send update with 3 photo sizes, assert only medium used

# tests/test_ollama.py
def test_ollama_blocking_does_not_block_loop():
    """Verify the event loop can process other coroutines while Ollama runs."""
    # Start generate(), verify asyncio.sleep(0) runs immediately

# tests/test_agents.py
def test_subprocess_killed_on_timeout():
    """Verify a timed-out agent process is killed."""
    # Mock agent that runs for 600s, set timeout=1, assert proc.kill() called
```
