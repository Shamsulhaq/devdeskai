import json
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

chat_histories: dict[int, list[dict]] = defaultdict(list)
user_prompts: dict[int, str] = {}
user_models: dict[int, str] = {}
user_temps: dict[int, float] = {}
user_agent: dict[int, str] = {}
user_agent_history: dict[int, list[dict]] = defaultdict(list)
user_docs: dict[int, str] = {}
user_personas: dict[int, str] = {}
stats: dict = {"total_messages": 0, "user_ids": []}


def load(path: str) -> None:
    global chat_histories, user_prompts, user_models, user_temps, user_agent
    global user_agent_history, user_docs, user_personas, stats
    try:
        with open(path) as f:
            data = json.load(f)
        chat_histories = defaultdict(
            list, {int(k): v for k, v in data.get("histories", {}).items()}
        )
        user_prompts = {int(k): v for k, v in data.get("prompts", {}).items()}
        user_models = {int(k): v for k, v in data.get("models", {}).items()}
        user_temps = {int(k): v for k, v in data.get("temps", {}).items()}
        user_agent = {int(k): v for k, v in data.get("agent", {}).items()}
        user_agent_history = defaultdict(
            list, {int(k): v for k, v in data.get("agent_history", {}).items()}
        )
        user_docs = {int(k): v for k, v in data.get("docs", {}).items()}
        user_personas = {int(k): v for k, v in data.get("personas", {}).items()}
        stats = data.get("stats", {"total_messages": 0, "user_ids": []})
        logger.info("Loaded data from %s", path)
    except FileNotFoundError:
        logger.info("No data file found at %s, starting fresh", path)


def save(path: str) -> None:
    data = {
        "histories": {str(k): v for k, v in chat_histories.items()},
        "prompts": {str(k): v for k, v in user_prompts.items()},
        "models": {str(k): v for k, v in user_models.items()},
        "temps": {str(k): v for k, v in user_temps.items()},
        "agent": {str(k): v for k, v in user_agent.items()},
        "agent_history": {str(k): v for k, v in user_agent_history.items()},
        "docs": {str(k): v for k, v in user_docs.items()},
        "personas": {str(k): v for k, v in user_personas.items()},
        "stats": stats,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def track_user(user_id: int) -> None:
    if user_id not in stats["user_ids"]:
        stats["user_ids"].append(user_id)
    stats["total_messages"] += 1
