"""Tests for `bot.agents.run_cli`.

`run_cli` uses subprocess_exec with an argv list (no shell) to invoke
external CLIs (claude, opencode, codex, …). These tests mock
`asyncio.create_subprocess_exec` so no real process is ever spawned.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from bot import agents


@pytest.fixture(autouse=True)
def reset_agents_registry():
    agents._agents = {}
    yield
    agents._agents = {}


def _register(name: str, available: bool = True, run_cmd: str = "fake-cli") -> None:
    agents._agents[name] = {
        "label": f"{name} (test)",
        "check": name,
        "run_cmd": run_cmd,
        "available": available,
    }


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"", delay: float = 0.0):
        self._stdout = stdout
        self._stderr = stderr
        self._delay = delay
        self.killed = False
        self.returncode = 0

    async def communicate(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return 0


async def test_run_cli_unavailable_short_circuits() -> None:
    _register("claude", available=False)
    out = await agents.run_cli("claude", "do a thing", workspace=".")
    assert "not available" in out.lower()


async def test_run_cli_unknown_agent_short_circuits() -> None:
    out = await agents.run_cli("nope", "anything", workspace=".")
    assert "not available" in out.lower()


async def test_run_cli_success_returns_stdout() -> None:
    _register("claude", available=True, run_cmd="claude -p")
    fake = _FakeProc(stdout=b"hello from claude\n")

    with patch(
        "bot.agents.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake),
    ) as mock_spawn:
        result = await agents.run_cli("claude", "review main.py", workspace="/tmp")

    assert result == "hello from claude"
    mock_spawn.assert_awaited_once()
    # The argv passed to subprocess_exec should contain the agent run cmd
    # and the prompt as separate elements (no shell).
    args, kwargs = mock_spawn.call_args
    argv = list(args)
    assert argv[0] == "claude"
    assert argv[1] == "-p"
    assert "review main.py" in argv
    assert kwargs.get("cwd") == "/tmp"


async def test_run_cli_appends_stderr_only_on_failure() -> None:
    """OPUS-020 fix: stderr is suppressed on success, included on non-zero exit."""
    _register("opencode", available=True, run_cmd="opencode")

    # Success: stderr suppressed even when present (progress bars etc).
    success = _FakeProc(stdout=b"stdout text", stderr=b"warn: thing")
    success.returncode = 0
    with patch(
        "bot.agents.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=success),
    ):
        result = await agents.run_cli("opencode", "x", workspace=".")
    assert "stdout text" in result
    assert "[stderr]" not in result

    # Failure: stderr included.
    failure = _FakeProc(stdout=b"stdout text", stderr=b"real error")
    failure.returncode = 1
    with patch(
        "bot.agents.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=failure),
    ):
        result = await agents.run_cli("opencode", "x", workspace=".")
    assert "stdout text" in result
    assert "[stderr]" in result
    assert "real error" in result


async def test_run_cli_timeout_returns_timeout_message() -> None:
    _register("claude", available=True, run_cmd="claude -p")

    async def _raise_timeout(*_args, **_kwargs):
        raise asyncio.TimeoutError

    fake = _FakeProc(stdout=b"")

    with patch(
        "bot.agents.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake),
    ), patch("bot.agents.asyncio.wait_for", new=AsyncMock(side_effect=_raise_timeout)):
        result = await agents.run_cli("claude", "long task", workspace=".")

    assert "timed out" in result.lower()


async def test_run_cli_unexpected_exception_is_caught() -> None:
    _register("claude", available=True, run_cmd="claude -p")

    with patch(
        "bot.agents.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await agents.run_cli("claude", "x", workspace=".")

    assert "Agent error" in result
    assert "boom" in result


async def test_run_cli_no_shell_injection() -> None:
    """Prompt with backticks / newlines / shell metachars must not break
    the agent invocation. subprocess_exec passes argv as a list, so
    no shell parsing occurs.
    """
    _register("claude", available=True, run_cmd="claude -p")
    fake = _FakeProc(stdout=b"ok\n")
    dangerous = (
        "Review this code:\n```html\n<!DOCTYPE html><html>...</html>\n```\n"
        "Includes `backticks` and $variables and ; semicolons."
    )
    with patch(
        "bot.agents.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake),
    ) as mock_spawn:
        result = await agents.run_cli("claude", dangerous, workspace="/tmp")

    assert result == "ok"
    args, _ = mock_spawn.call_args
    argv = list(args)
    # The prompt must be passed as a single argv element, not interpreted
    assert argv[-1] == dangerous


def test_detect_populates_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agents.shutil,
        "which",
        lambda cmd: "/usr/bin/" + cmd if cmd == "claude" else None,
    )
    found = agents.detect()
    assert "claude" in found
    assert found["claude"]["available"] is True
    # Anything not stubbed-in must report unavailable.
    assert found["opencode"]["available"] is False


def test_get_available_returns_internal_registry() -> None:
    _register("claude", available=True)
    assert agents.get_available() is agents._agents
