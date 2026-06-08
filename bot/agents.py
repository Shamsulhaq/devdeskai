from __future__ import annotations

import asyncio
import logging
import shutil

logger = logging.getLogger(__name__)

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
    full_cmd = f'{info["run_cmd"]} "{prompt}"'
    try:
        proc = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        # only surface stderr on non-zero exit; many CLIs
        # write progress bars / telemetry to stderr on success.
        if proc.returncode != 0 and err:
            return out + f"\n\n[stderr]\n{err}"
        return out
    except asyncio.TimeoutError:
        return "Agent timed out (300s)."
    except Exception as e:
        return f"Agent error: {e}"
