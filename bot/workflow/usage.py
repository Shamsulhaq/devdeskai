"""Agent usage tracking, limit monitoring, and brain-driven routing."""
import os
import time
from dataclasses import dataclass

AGENT_LIMITS = {
    "opencode": {"rph": int(os.getenv("OPENCODE_RPH", "50")), "tph": int(os.getenv("OPENCODE_TPH", "500000"))},
    "codex": {"rph": int(os.getenv("CODEX_RPH", "50")), "tph": int(os.getenv("CODEX_TPH", "500000"))},
    "claude": {"rph": int(os.getenv("CLAUDE_RPH", "50")), "tph": int(os.getenv("CLAUDE_TPH", "500000"))},
    "qwen": {"rph": int(os.getenv("QWEN_RPH", "100")), "tph": int(os.getenv("QWEN_TPH", "1000000"))},
    "gemini": {"rph": int(os.getenv("GEMINI_RPH", "60")), "tph": int(os.getenv("GEMINI_TPH", "1000000"))},
    "copilot": {"rph": int(os.getenv("COPILOT_RPH", "30")), "tph": int(os.getenv("COPILOT_TPH", "300000"))},
}

AGENT_FALLBACK_ORDER = ["opencode", "codex", "claude", "qwen", "gemini", "copilot"]

# Error threshold before declaring agent dead — brain decides, not hardcoded
MAX_CONSECUTIVE_FAILURES = int(os.getenv("MAX_AGENT_FAILURES", "3"))


@dataclass
class AgentUsage:
    agent: str
    user_id: int
    request_count: int = 0
    token_count: int = 0
    window_start: float = 0.0
    consecutive_failures: int = 0
    last_error_time: float = 0.0
    is_dead: bool = False
    total_requests: int = 0
    total_errors: int = 0


class UsageManager:
    def __init__(self):
        self._usages: dict[str, AgentUsage] = {}

    def _key(self, agent: str, user_id: int) -> str:
        return f"{agent}:{user_id}"

    def _get_or_create(self, agent: str, user_id: int) -> AgentUsage:
        key = self._key(agent, user_id)
        if key not in self._usages:
            self._usages[key] = AgentUsage(
                agent=agent, user_id=user_id, window_start=time.time()
            )
        return self._usages[key]

    @staticmethod
    def _reset_window_if_needed(usage: AgentUsage):
        if time.time() - usage.window_start > 3600:
            usage.request_count = 0
            usage.token_count = 0
            usage.window_start = time.time()

    def can_use(self, agent: str, user_id: int) -> bool:
        usage = self._get_or_create(agent, user_id)
        self._reset_window_if_needed(usage)
        if usage.is_dead:
            return False
        limits = AGENT_LIMITS.get(agent, {"rph": 50, "tph": 500000})
        if usage.request_count >= limits["rph"]:
            return False
        if usage.token_count >= limits["tph"]:
            return False
        return True

    def record_success(self, agent: str, user_id: int, tokens: int = 0):
        usage = self._get_or_create(agent, user_id)
        self._reset_window_if_needed(usage)
        usage.request_count += 1
        usage.token_count += tokens
        usage.consecutive_failures = 0
        usage.is_dead = False
        usage.total_requests += 1

    def record_failure(self, agent: str, user_id: int):
        usage = self._get_or_create(agent, user_id)
        usage.consecutive_failures += 1
        usage.last_error_time = time.time()
        usage.total_errors += 1
        if usage.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            usage.is_dead = True

    def get_available(self, user_id: int) -> list[str]:
        return [a for a in AGENT_FALLBACK_ORDER if self.can_use(a, user_id)]

    def get_usage(self, agent: str, user_id: int) -> AgentUsage | None:
        return self._usages.get(self._key(agent, user_id))

    def get_fallback_order(self, user_id: int) -> list[str]:
        """Return available agents sorted by remaining capacity."""
        scored = []
        for agent in AGENT_FALLBACK_ORDER:
            usage = self._get_or_create(agent, user_id)
            limits = AGENT_LIMITS.get(agent, {"rph": 50})
            remaining = limits["rph"] - usage.request_count
            scored.append((remaining, agent))
        scored.sort(key=lambda x: -x[0])
        return [a for _, a in scored]

    def get_status(self, user_id: int) -> dict:
        status = {}
        for agent in AGENT_FALLBACK_ORDER:
            usage = self._get_or_create(agent, user_id)
            limits = AGENT_LIMITS.get(agent, {})
            remaining = limits.get("rph", 50) - usage.request_count if not usage.is_dead else 0
            status[agent] = {
                "available": self.can_use(agent, user_id),
                "remaining": max(0, remaining),
                "limit_rph": limits.get("rph", 50),
                "requests": usage.request_count,
                "total_requests": usage.total_requests,
                "errors": usage.consecutive_failures,
                "total_errors": usage.total_errors,
                "dead": usage.is_dead,
            }
        return status

    def to_dict(self) -> dict:
        return {k: {
            "agent": v.agent, "user_id": v.user_id,
            "request_count": v.request_count, "token_count": v.token_count,
            "window_start": v.window_start, "consecutive_failures": v.consecutive_failures,
            "last_error_time": v.last_error_time, "is_dead": v.is_dead,
            "total_requests": v.total_requests, "total_errors": v.total_errors,
        } for k, v in self._usages.items()}

    def from_dict(self, data: dict):
        self._usages = {}
        for k, v in data.items():
            self._usages[k] = AgentUsage(**v)


usage_manager = UsageManager()
