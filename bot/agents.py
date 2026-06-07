from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
import time

logger = logging.getLogger(__name__)

AGENT_TIMEOUT = 300  # seconds

AGENTS_CONFIG: dict[str, dict] = {
    "claude": {
        "label": "Claude (Anthropic)",
        "check": "claude",
        "run_cmd": "claude -p",
    },
    "opencode": {
        "label": "OpenCode",
        "check": "opencode",
        "run_cmd": "opencode",
    },
    "codex": {
        "label": "Codex (OpenAI)",
        "check": "codex",
        "run_cmd": "codex",
    },
    "qwen": {
        "label": "Qwen (Alibaba)",
        "check": "qwen",
        "run_cmd": "qwen",
    },
    "gemini": {
        "label": "Gemini (Google)",
        "check": "gemini",
        "run_cmd": "gemini",
    },
    "copilot": {
        "label": "GitHub Copilot",
        "check": "copilot",
        "run_cmd": "copilot",
    },
}

_agents: dict[str, dict] = {}


def detect() -> dict[str, dict]:
    for name, info in AGENTS_CONFIG.items():
        cmd = info["check"].split()[0]
        found = shutil.which(cmd) is not None
        _agents[name] = {**info, "available": found}
        if found:
            logger.info("Agent '%s' detected at %s", name, shutil.which(cmd))
    return _agents


def get_available() -> dict[str, dict]:
    return _agents


async def run_cli(agent_name: str, prompt: str, workspace: str) -> str:
    info = _agents.get(agent_name)
    if not info or not info["available"]:
        return f"Agent '{agent_name}' is not available."

    # Build argv safely — no shell interpolation (BUG-001 fix)
    try:
        run_parts = shlex.split(info["run_cmd"])
    except ValueError as e:
        return f"Agent misconfigured (bad run_cmd): {e}"
    argv = [*run_parts, prompt]

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=AGENT_TIMEOUT
            )
        except asyncio.TimeoutError:
            # BUG-005 fix: kill the subprocess, don't leave it running
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return f"Agent timed out ({AGENT_TIMEOUT}s)."

        elapsed = time.monotonic() - start
        logger.info("Agent %s completed in %.1fs", agent_name, elapsed)
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        return (out + f"\n\n[stderr]\n{err}") if err else out
    except FileNotFoundError:
        return f"Agent '{agent_name}' binary not found."
    except Exception as e:
        logger.exception("Agent error")
        return f"Agent error: {e}"
