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
        "run_cmd": "opencode run",
    },
    "codex": {
        "label": "Codex (OpenAI)",
        "check": "codex",
        "run_cmd": "codex exec --skip-git-repo-check",
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
    """Run an agent CLI with the given prompt.

    Uses subprocess_exec (no shell) with the prompt as a separate argv
    element. This avoids shell injection and quoting issues that broke
    prompts containing backticks, newlines, or HTML.
    """
    info = _agents.get(agent_name)
    if not info or not info["available"]:
        return f"Agent '{agent_name}' is not available."
    # Build argv from the run_cmd (already a list of args like
    # ["claude", "-p"]) and append the prompt as the final positional.
    base_args = info["run_cmd"].split()
    argv = base_args + [prompt]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            # On non-zero exit, surface stderr so the brain sees the error
            msg = out or f"Agent '{agent_name}' failed (exit {proc.returncode})"
            if err:
                msg += f"\n\n[stderr]\n{err}"
            return msg
        if err and not out:
            # Some CLIs write everything to stderr (e.g. progress + result)
            return err
        return out or err
    except asyncio.TimeoutError:
        return "Agent timed out (300s)."
    except FileNotFoundError:
        return f"Agent error: command not found ({base_args[0] if base_args else '?'})"
    except Exception as e:
        return f"Agent error: {e}"
