"""Tests for `bot.ollama.build_messages` — deterministic, no I/O."""
from __future__ import annotations

from collections import defaultdict

import pytest

from bot import config, ollama, persistence


@pytest.fixture(autouse=True)
def reset_state():
    persistence.chat_histories = defaultdict(list)
    persistence.user_prompts = {}
    persistence.user_personas = {}
    yield
    persistence.chat_histories = defaultdict(list)
    persistence.user_prompts = {}
    persistence.user_personas = {}


def _seed_history(user_id: int, count: int) -> None:
    for i in range(count):
        persistence.chat_histories[user_id].append(
            {"user": f"u{i}", "assistant": f"a{i}"}
        )


def test_build_messages_includes_system_then_history_then_new() -> None:
    user_id = 1
    _seed_history(user_id, 3)

    msgs = ollama.build_messages(user_id, "current question")

    # 1 system + 3 user/assistant pairs + 1 new user = 8
    assert len(msgs) == 1 + 3 * 2 + 1
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == config.SYSTEM_PROMPT

    # Pairs in order.
    assert msgs[1] == {"role": "user", "content": "u0"}
    assert msgs[2] == {"role": "assistant", "content": "a0"}
    assert msgs[3] == {"role": "user", "content": "u1"}
    assert msgs[6] == {"role": "assistant", "content": "a2"}

    # New message appended last.
    assert msgs[-1] == {"role": "user", "content": "current question"}


def test_build_messages_uses_persona_when_set() -> None:
    user_id = 2
    persistence.user_personas[user_id] = "pirate"

    msgs = ollama.build_messages(user_id, "hi")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == ollama.PERSONAS["pirate"]
    assert "pirate" in msgs[0]["content"].lower()


def test_build_messages_falls_back_to_user_prompt() -> None:
    user_id = 3
    persistence.user_prompts[user_id] = "Custom prompt for user 3"

    msgs = ollama.build_messages(user_id, "anything")
    assert msgs[0]["content"] == "Custom prompt for user 3"


def test_build_messages_default_system_prompt_when_nothing_set() -> None:
    user_id = 4
    msgs = ollama.build_messages(user_id, "hello")
    assert msgs[0]["content"] == config.SYSTEM_PROMPT


def test_build_messages_unknown_persona_falls_through_to_prompts() -> None:
    user_id = 5
    persistence.user_personas[user_id] = "not-a-real-persona"
    persistence.user_prompts[user_id] = "user-level fallback"

    msgs = ollama.build_messages(user_id, "hi")
    assert msgs[0]["content"] == "user-level fallback"


def test_build_messages_truncates_to_max_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAX_HISTORY", 2)
    user_id = 6
    _seed_history(user_id, 5)

    msgs = ollama.build_messages(user_id, "now")

    # system + 2 pairs + new user = 6
    assert len(msgs) == 1 + 2 * 2 + 1
    # Earliest entries (u0..u2) should be dropped — only u3, u4 remain.
    user_contents = [m["content"] for m in msgs if m["role"] == "user"]
    assert "u0" not in user_contents
    assert "u1" not in user_contents
    assert "u2" not in user_contents
    assert "u3" in user_contents
    assert "u4" in user_contents
    assert user_contents[-1] == "now"


def test_build_messages_attaches_image_to_last_user_message() -> None:
    user_id = 7
    msgs = ollama.build_messages(user_id, "describe", image_data="b64-bytes")
    last = msgs[-1]
    assert last["role"] == "user"
    assert last["content"] == "describe"
    assert last.get("images") == ["b64-bytes"]


def test_get_model_falls_back_to_default() -> None:
    assert ollama.get_model(999) == config.MODEL


def test_get_temperature_returns_none_by_default() -> None:
    assert ollama.get_temperature(999) is None


def test_get_temperature_returns_user_value() -> None:
    persistence.user_temps[42] = 0.42
    try:
        assert ollama.get_temperature(42) == 0.42
    finally:
        persistence.user_temps.pop(42, None)
