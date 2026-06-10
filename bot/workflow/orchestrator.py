import asyncio
import os
import logging

from bot import agents, ollama
from bot.workflow.usage import usage_manager

logger = logging.getLogger(__name__)


def _agent_available(name: str) -> bool:
    available = agents.get_available()
    entry = available.get(name)
    return entry is not None and entry.get("available", False)


async def _try_agent(
    agent: str, prompt: str, workspace: str, user_id: int,
) -> str | Exception:
    """Run a single agent and record usage outcome."""
    if not _agent_available(agent):
        usage_manager.record_failure(agent, user_id)
        return Exception(f"Agent '{agent}' not installed")
    if not usage_manager.can_use(agent, user_id):
        return Exception(f"Agent '{agent}' rate-limited or exhausted")
    try:
        os.makedirs(workspace, exist_ok=True)
        result = await agents.run_cli(agent, prompt, workspace)
        if result.startswith("Agent error") or result.startswith("Agent timed out"):
            usage_manager.record_failure(agent, user_id)
            return Exception(result)
        usage_manager.record_success(agent, user_id, tokens=len(result))
        return result
    except Exception as e:
        usage_manager.record_failure(agent, user_id)
        return Exception(f"{agent} raised: {e}")


def _get_available_agents() -> dict[str, dict]:
    """Expose agent registry with availability for planner/engine."""
    return agents.get_available()


async def run_brain_step(agent: str, prompt: str, workspace: str, user_id: int) -> str:
    """Try preferred agent first; if limited, brain picks next available."""
    from bot.workflow import brain as brain_module

    if _agent_available(agent) and usage_manager.can_use(agent, user_id):
        result = await _try_agent(agent, prompt, workspace, user_id)
        if not isinstance(result, Exception):
            return result
        logger.warning("Preferred agent %s failed: %s", agent, result)

    # Preferred agent unavailable — brain decides fallback
    available = usage_manager.get_available(user_id)
    installed = [n for n in available if _agent_available(n)]
    if not installed:
        logger.info("No agents available, falling back to Ollama")
        return await ollama.generate(user_id, prompt)

    if len(installed) == 1:
        chosen = installed[0]
    else:
        fallback_order = usage_manager.get_fallback_order(user_id)
        status = {a: s for a, s in usage_manager.get_status(user_id).items() if a in installed}
        chosen = await brain_module.pick_best_agent(
            task=prompt[:500], agents=installed, fallback_order=fallback_order,
            status=status, user_id=user_id,
        )
        if chosen not in installed:
            chosen = installed[0]

    logger.info("Brain routed: %s -> %s", agent, chosen)
    result = await _try_agent(chosen, prompt, workspace, user_id)
    if isinstance(result, Exception):
        for fallback in installed:
            if fallback == chosen:
                continue
            result2 = await _try_agent(fallback, prompt, workspace, user_id)
            if not isinstance(result2, Exception):
                return result2
        return await ollama.generate(user_id, prompt)
    return result


async def run_parallel(steps: list[dict], workspace: str, user_id: int) -> list[str | Exception]:
    """Dispatch to multiple agents in parallel, respecting per-agent limits."""
    from bot.workflow import brain as brain_module

    tasks = []
    status = usage_manager.get_status(user_id)

    for step in steps:
        agent = step.get("agent", "claude")
        prompt = step["prompt"]
        if usage_manager.can_use(agent, user_id) and _agent_available(agent):
            tasks.append(_try_agent(agent, prompt, workspace, user_id))
        else:
            installed = [n for n in usage_manager.get_available(user_id) if _agent_available(n)]
            if installed:
                fallback_order = usage_manager.get_fallback_order(user_id)
                chosen = await brain_module.pick_best_agent(
                    task=prompt[:500], agents=installed,
                    fallback_order=fallback_order,
                    status={a: s for a, s in status.items() if a in installed},
                    user_id=user_id,
                )
                logger.info("Parallel route: %s -> %s", agent, chosen)
                tasks.append(_try_agent(chosen, prompt, workspace, user_id))
            else:
                tasks.append(ollama.generate(user_id, prompt))

    return await asyncio.gather(*tasks, return_exceptions=True)
