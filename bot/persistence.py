import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from collections import defaultdict, deque

from bot import config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SAVE_DEBOUNCE_SECONDS = 2.0

AGENT_HISTORY_MAX = 50  # per user

chat_histories: dict[int, list[dict]] = defaultdict(list)
user_prompts: dict[int, str] = {}
user_models: dict[int, str] = {}
user_temps: dict[int, float] = {}
user_agent: dict[int, str] = {}
user_agent_history: dict[int, deque] = defaultdict(
    lambda: deque(maxlen=AGENT_HISTORY_MAX)
)
user_docs: dict[int, str] = {}
user_personas: dict[int, str] = {}
# user_ids is internally a set for O(1) membership; serialized as a sorted list.
stats: dict = {"total_messages": 0, "user_ids": set()}

# module-level lock; asyncio.Lock() does not require a running loop in 3.10+.
_save_lock: asyncio.Lock = asyncio.Lock()

_pending_save_task: asyncio.Task | None = None
_pending_save_path: str | None = None


def _trim_history(history: list[dict]) -> list[dict]:
    cap = 2 * config.MAX_HISTORY
    if len(history) > cap:
        return history[-cap:]
    return history


def append_history(uid: int, user_msg: dict, assistant_msg: dict) -> None:
    """OPUS-004 fix: append a user/assistant pair and enforce the cap in place."""
    history = chat_histories[uid]
    history.append(user_msg)
    history.append(assistant_msg)
    cap = 2 * config.MAX_HISTORY
    if len(history) > cap:
        del history[: len(history) - cap]


def _backup_file(path: str, reason: str) -> None:
    try:
        backup = f"{path}.bak.{int(time.time())}.{reason}"
        shutil.copy2(path, backup)
        logger.warning("Backed up %s to %s", path, backup)
    except Exception as exc:
        logger.error("Failed to back up %s: %s", path, exc)


def _coerce_user_ids(raw) -> set[int]:
    if raw is None:
        return set()
    try:
        return {int(x) for x in raw}
    except (TypeError, ValueError):
        return set()


def load(path: str) -> None:
    global chat_histories, user_prompts, user_models, user_temps, user_agent
    global user_agent_history, user_docs, user_personas, stats
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.info("No data file at %s, starting fresh", path)
        return
    except json.JSONDecodeError as e:
        backup = f"{path}.corrupt.{int(time.time())}"
        try:
            os.rename(path, backup)
            logger.error(
                "Corrupted %s (%s). Backed up to %s. Starting fresh.",
                path, e, backup,
            )
        except OSError:
            logger.exception("Failed to back up corrupted %s", path)
        return
    except Exception:
        logger.exception("Failed to load %s", path)
        return

    version = data.get("schema_version", 0)
    if not isinstance(version, int):
        logger.error("Invalid schema_version %r in %s; starting fresh", version, path)
        _backup_file(path, "invalid-schema")
        return
    if version > SCHEMA_VERSION:
        logger.error(
            "Data file %s schema version %d > supported %d; refusing to load",
            path, version, SCHEMA_VERSION,
        )
        _backup_file(path, f"future-v{version}")
        return
    # version 0 = pre-versioning; read as-is. Future migrations chain here.

    chat_histories = defaultdict(
        list,
        {int(k): _trim_history(list(v)) for k, v in data.get("histories", {}).items()},
    )
    user_prompts = {int(k): v for k, v in data.get("prompts", {}).items()}
    user_models = {int(k): v for k, v in data.get("models", {}).items()}
    user_temps = {int(k): v for k, v in data.get("temps", {}).items()}
    user_agent = {int(k): v for k, v in data.get("agent", {}).items()}
    user_agent_history = defaultdict(
        lambda: deque(maxlen=AGENT_HISTORY_MAX),
        {
            int(k): deque(v, maxlen=AGENT_HISTORY_MAX)
            for k, v in data.get("agent_history", {}).items()
        },
    )
    user_docs = {int(k): v for k, v in data.get("docs", {}).items()}
    user_personas = {int(k): v for k, v in data.get("personas", {}).items()}
    raw_stats = data.get("stats", {"total_messages": 0, "user_ids": []})
    stats = {
        "total_messages": int(raw_stats.get("total_messages", 0) or 0),
        "user_ids": _coerce_user_ids(raw_stats.get("user_ids")),
    }
    logger.info("Loaded data from %s (schema v%d)", path, version)


def _serialize() -> dict:
    cap = 2 * config.MAX_HISTORY
    histories_out: dict[str, list[dict]] = {}
    for k, v in chat_histories.items():
        if len(v) > cap:
            del v[: len(v) - cap]
        histories_out[str(k)] = list(v)
    return {
        "schema_version": SCHEMA_VERSION,
        "histories": histories_out,
        "prompts": {str(k): v for k, v in user_prompts.items()},
        "models": {str(k): v for k, v in user_models.items()},
        "temps": {str(k): v for k, v in user_temps.items()},
        "agent": {str(k): v for k, v in user_agent.items()},
        "agent_history": {str(k): list(v) for k, v in user_agent_history.items()},
        "docs": {str(k): v for k, v in user_docs.items()},
        "personas": {str(k): v for k, v in user_personas.items()},
        "stats": {
            "total_messages": stats.get("total_messages", 0),
            "user_ids": sorted(stats.get("user_ids", set())),
        },
    }


def save(path: str) -> None:
    """Atomic write: write to tmp file, then os.replace()."""
    data = _serialize()
    dir_name = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".bot_data_", dir=dir_name)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


async def _debounced_save(path: str) -> None:
    """OPUS-005 fix: wait out the debounce window, then write under the lock.

    Subsequent save_async() calls during the sleep only update _pending_save_path,
    so all writes within the window coalesce into a single disk write.
    """
    global _pending_save_task, _pending_save_path
    try:
        await asyncio.sleep(SAVE_DEBOUNCE_SECONDS)
        target = _pending_save_path or path
        async with _save_lock:
            await asyncio.to_thread(save, target)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Debounced save failed")
    finally:
        _pending_save_task = None
        _pending_save_path = None


async def save_async(path: str) -> None:
    """OPUS-005 fix: coalesce save calls into a single debounced write."""
    global _pending_save_task, _pending_save_path
    _pending_save_path = path
    if _pending_save_task is None or _pending_save_task.done():
        _pending_save_task = asyncio.create_task(_debounced_save(path))


def track_user(user_id: int) -> None:
    # O(1) set membership; tolerate legacy list state defensively.
    user_ids = stats.get("user_ids")
    if not isinstance(user_ids, set):
        user_ids = set(user_ids or ())
        stats["user_ids"] = user_ids
    user_ids.add(int(user_id))
    stats["total_messages"] = stats.get("total_messages", 0) + 1
