"""Round-trip and recovery tests for `bot.persistence`.

The module uses module-level globals (see architecture observation A1 in the
bug report), so tests reset state via attribute assignment on the module
before each scenario.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import pytest

from bot import persistence


def _reset_state() -> None:
    """Wipe persistence module globals back to their initial values."""
    persistence.chat_histories = defaultdict(list)
    persistence.user_prompts = {}
    persistence.user_models = {}
    persistence.user_temps = {}
    persistence.user_agent = {}
    persistence.user_agent_history = defaultdict(list)
    persistence.user_docs = {}
    persistence.user_personas = {}
    persistence.stats = {"total_messages": 0, "user_ids": set()}


@pytest.fixture(autouse=True)
def clean_state():
    _reset_state()
    yield
    _reset_state()


def test_round_trip_save_load(tmp_path: Path) -> None:
    persistence.chat_histories[42].append({"user": "hi", "assistant": "hello"})
    persistence.chat_histories[42].append({"user": "ping", "assistant": "pong"})
    persistence.user_models[42] = "gemma3:4b"
    persistence.user_temps[42] = 0.7
    persistence.stats = {"total_messages": 2, "user_ids": {42}}

    data_file = tmp_path / "bot_data.json"
    persistence.save(str(data_file))

    assert data_file.exists()
    raw = json.loads(data_file.read_text())
    assert raw["histories"]["42"][0]["user"] == "hi"
    assert raw["models"]["42"] == "gemma3:4b"
    assert raw["temps"]["42"] == 0.7
    assert raw["stats"]["total_messages"] == 2

    # Wipe state, then reload and assert it matches.
    _reset_state()
    assert persistence.user_models == {}
    assert persistence.stats == {"total_messages": 0, "user_ids": set()}

    persistence.load(str(data_file))

    assert dict(persistence.chat_histories) == {
        42: [
            {"user": "hi", "assistant": "hello"},
            {"user": "ping", "assistant": "pong"},
        ]
    }
    assert persistence.user_models == {42: "gemma3:4b"}
    assert persistence.user_temps == {42: 0.7}
    assert persistence.stats == {"total_messages": 2, "user_ids": {42}}


def test_load_missing_file_starts_fresh(tmp_path: Path) -> None:
    persistence.user_models[7] = "preserved-in-memory"
    missing = tmp_path / "does_not_exist.json"

    # Should not raise — FileNotFoundError is handled internally.
    persistence.load(str(missing))

    # In-memory state survives a no-op load.
    assert persistence.user_models == {7: "preserved-in-memory"}


def test_load_corrupt_json_recovers(tmp_path: Path) -> None:
    """Corrupt data file should be backed up and the bot should start fresh.

    NOTE: as of this commit, `bot.persistence.load` does NOT implement
    backup-and-recover — it lets `json.JSONDecodeError` propagate. This test
    is marked `xfail` so it serves as a living spec for the desired
    behavior (see OPUS-014 and related notes in the bug report). Flip the
    `xfail` to a hard assertion once the recovery path lands.
    """
    data_file = tmp_path / "bot_data.json"
    data_file.write_text("{ this is not valid json")

    pre_models = dict(persistence.user_models)

    try:
        persistence.load(str(data_file))
    except json.JSONDecodeError:
        pytest.xfail(
            "persistence.load does not yet recover from corrupt JSON. "
            "Expected behavior: rename the bad file to *.corrupt.bak and "
            "leave module state untouched."
        )

    # If load() returns normally we DO want to assert recovery semantics.
    backups = list(tmp_path.glob("*bak*")) + list(tmp_path.glob("*corrupt*"))
    assert backups, "expected a backup of the corrupt file"
    assert persistence.user_models == pre_models


def test_track_user_dedupes_and_counts() -> None:
    persistence.track_user(1)
    persistence.track_user(1)
    persistence.track_user(2)

    assert persistence.stats["total_messages"] == 3
    # OPUS-004 fix: user_ids is now a set for O(1) membership.
    assert persistence.stats["user_ids"] == {1, 2}


def test_save_atomic_keys_present(tmp_path: Path) -> None:
    """Every expected top-level key should be written, even when empty."""
    data_file = tmp_path / "bot_data.json"
    persistence.save(str(data_file))
    payload = json.loads(data_file.read_text())
    for key in (
        "histories",
        "prompts",
        "models",
        "temps",
        "agent",
        "agent_history",
        "docs",
        "personas",
        "stats",
    ):
        assert key in payload, f"missing key {key!r} in saved payload"
    # Stats default shape preserved (user_ids serialized as sorted list).
    assert payload["stats"] == {"total_messages": 0, "user_ids": []}
    # schema_version field present (OPUS-014 fix).
    assert payload.get("schema_version") == 1


def test_data_file_path_is_string(tmp_path: Path) -> None:
    """Ensure `save`/`load` accept plain string paths (not just Path objects)."""
    data_file = os.path.join(str(tmp_path), "bot_data.json")
    persistence.user_models[1] = "x"
    persistence.save(data_file)
    _reset_state()
    persistence.load(data_file)
    assert persistence.user_models == {1: "x"}
