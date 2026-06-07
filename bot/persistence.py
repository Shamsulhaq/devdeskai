import asyncio
import json
import logging
import os
import tempfile
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

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
stats: dict = {"total_messages": 0, "user_ids": []}

_save_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _save_lock
    if _save_lock is None:
        _save_lock = asyncio.Lock()
    return _save_lock


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
        # BUG-007 fix: back up corrupt file, don't crash
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

    chat_histories = defaultdict(
        list, {int(k): v for k, v in data.get("histories", {}).items()}
    )
    user_prompts = {int(k): v for k, v in data.get("prompts", {}).items()}
    user_models = {int(k): v for k, v in data.get("models", {}).items()}
    user_temps = {int(k): v for k, v in data.get("temps", {}).items()}
    user_agent = {int(k): v for k, v in data.get("agent", {}).items()}
    # BUG-014 fix: bound agent history
    user_agent_history = defaultdict(
        lambda: deque(maxlen=AGENT_HISTORY_MAX),
        {
            int(k): deque(v, maxlen=AGENT_HISTORY_MAX)
            for k, v in data.get("agent_history", {}).items()
        },
    )
    user_docs = {int(k): v for k, v in data.get("docs", {}).items()}
    user_personas = {int(k): v for k, v in data.get("personas", {}).items()}
    stats = data.get("stats", {"total_messages": 0, "user_ids": []})
    logger.info("Loaded data from %s", path)


def save(path: str) -> None:
    """Atomic write: write to tmp file, then os.replace()."""
    data = {
        "histories": {str(k): list(v) for k, v in chat_histories.items()},
        "prompts": {str(k): v for k, v in user_prompts.items()},
        "models": {str(k): v for k, v in user_models.items()},
        "temps": {str(k): v for k, v in user_temps.items()},
        "agent": {str(k): v for k, v in user_agent.items()},
        "agent_history": {
            str(k): list(v) for k, v in user_agent_history.items()
        },
        "docs": {str(k): v for k, v in user_docs.items()},
        "personas": {str(k): v for k, v in user_personas.items()},
        "stats": stats,
    }

    # BUG-002 fix: atomic write via tempfile + os.replace
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


async def save_async(path: str) -> None:
    """BUG-015 fix: serialize concurrent saves with an asyncio.Lock."""
    async with _get_lock():
        await asyncio.to_thread(save, path)


def track_user(user_id: int) -> None:
    if user_id not in stats["user_ids"]:
        stats["user_ids"].append(user_id)
    stats["total_messages"] += 1
