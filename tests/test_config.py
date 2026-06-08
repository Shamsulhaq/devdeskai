"""Tests for `bot.config` env parsing — especially `CUSTOM_COMMANDS`.

`bot.config` is import-time: env vars are read into module globals when the
module is first loaded. We use `importlib.reload` after mutating `os.environ`
so each scenario sees a fresh parse. The session-wide `BOT_TOKEN=test`
default from `conftest.py` keeps the import-time guard satisfied.
"""
from __future__ import annotations

import importlib
import os
from typing import Iterator

import pytest

import bot.config as config_module


@pytest.fixture
def clean_custom_env() -> Iterator[None]:
    """Snapshot and restore CUSTOM_CMD_* env vars around each test.

    `bot.config` is import-time, so we reload it inside tests to re-parse env.
    `conftest.py` neutralises `dotenv.load_dotenv`, so reload only sees
    `os.environ` — no surprise values from a developer's local `.env`.
    """
    saved = {
        k: v for k, v in os.environ.items() if k.startswith("CUSTOM_CMD_")
    }
    for k in saved:
        del os.environ[k]
    yield
    # Wipe anything tests added, then restore originals.
    for k in [k for k in os.environ if k.startswith("CUSTOM_CMD_")]:
        del os.environ[k]
    os.environ.update(saved)
    importlib.reload(config_module)


def _reload() -> None:
    importlib.reload(config_module)


def test_custom_commands_empty_by_default(clean_custom_env: None) -> None:
    _reload()
    assert config_module.CUSTOM_COMMANDS == {}


def test_custom_commands_parses_single_command(clean_custom_env: None) -> None:
    os.environ["CUSTOM_CMD_REVIEW"] = "foo"
    _reload()
    assert config_module.CUSTOM_COMMANDS == {"review": "foo"}


def test_custom_commands_conflicting_values_raise(clean_custom_env: None) -> None:
    """OPUS-022 fix: conflicting CUSTOM_CMD_X and CUSTOM_CMD_X_PROMPT raise ValueError.

    Matching values or only-one-set are accepted (covered in test_parses_single_command).
    """
    os.environ["CUSTOM_CMD_REVIEW"] = "foo"
    os.environ["CUSTOM_CMD_REVIEW_PROMPT"] = "bar"
    with pytest.raises(ValueError):
        _reload()


def test_custom_commands_matching_values_accepted(clean_custom_env: None) -> None:
    os.environ["CUSTOM_CMD_REVIEW"] = "same"
    os.environ["CUSTOM_CMD_REVIEW_PROMPT"] = "same"
    _reload()
    assert config_module.CUSTOM_COMMANDS == {"review": "same"}


def test_custom_commands_multiple(clean_custom_env: None) -> None:
    os.environ["CUSTOM_CMD_REVIEW"] = "review prompt"
    os.environ["CUSTOM_CMD_TRANSLATE"] = "translate prompt"
    _reload()
    assert config_module.CUSTOM_COMMANDS == {
        "review": "review prompt",
        "translate": "translate prompt",
    }


def test_bot_token_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """BOT_TOKEN must be present after import (otherwise config raises).

    The exact value depends on whether a local `.env` is present — dotenv
    will populate from there in dev environments. We only assert truthiness.
    """
    monkeypatch.setenv("BOT_TOKEN", "explicit-value")
    _reload()
    assert config_module.BOT_TOKEN == "explicit-value"


def test_max_history_defaults_to_20(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_HISTORY", raising=False)
    _reload()
    assert config_module.MAX_HISTORY == 20


def test_max_history_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_HISTORY", "5")
    _reload()
    assert config_module.MAX_HISTORY == 5
    # Restore default for downstream tests.
    monkeypatch.delenv("MAX_HISTORY", raising=False)
    _reload()


def test_admin_ids_parses_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "1, 2 ,3")
    _reload()
    assert config_module.ADMIN_IDS == {1, 2, 3}
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    _reload()


def test_admin_ids_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    _reload()
    assert config_module.ADMIN_IDS == set()
