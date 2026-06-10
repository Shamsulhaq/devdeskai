import json
import re

from bot import ollama
from bot.workflow.models import WorkflowPlan, Step, RetryPolicy

PLANNER_SYSTEM_PROMPT = (
    "You are a senior software architect and project planner. "
    "Given a project request, create a detailed, actionable project plan.\n\n"
    "Cover these sections:\n"
    "1. Project Overview — what we're building and why\n"
    "2. Tech Stack — specific technologies with rationale\n"
    "3. Features — MVP features and future enhancements\n"
    "4. Architecture — high-level system design\n"
    "5. Pages / Components — everything that needs to be built\n"
    "6. Timeline — estimated effort per phase\n\n"
    "Be specific, concrete, and avoid generic advice."
)

DECOMPOSITION_SYSTEM_PROMPT = (
    "You are a senior architect that decomposes project requests into "
    "executable workflow plans. Output ONLY valid JSON matching this schema:\n"
    "{\n"
    '  "summary": "Brief plan overview",\n'
    '  "steps": [\n'
    "    {\n"
    '      "id": "unique-step-id",\n'
    '      "label": "human-readable step name",\n'
    '      "agent": "brain | opencode | codex | claude | qwen | gemini | copilot",\n'
    '      "prompt": "detailed instruction for the agent",\n'
    '      "depends_on": ["step-id-that-must-complete-first"],\n'
    '      "success_criteria": "what to check before moving on",\n'
    '      "retry": {"max_retries": 2, "review_agent": "claude"}\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "Rules:\n"
    "- Use 'brain' for steps that need Ollama thinking (planning, review).\n"
    "- Use agent CLI names for implementation steps.\n"
    "- Set depends_on to empty list for root steps.\n"
    "- Only set retry on steps that need verification.\n"
    "- Prefer parallel-friendly structure: independent steps can run concurrently.\n"
    "- Keep step prompts self-contained and detailed.\n"
    "- Limit to 8 steps maximum.\n"
)

DEFAULT_FALLBACK_PLAN = WorkflowPlan(
    summary="Standard build: PRD → code+tests → verify → fix → docs",
    steps=[
        Step(id="prd", label="Brain creates PRD", agent="brain",
             prompt="Create a detailed PRD for the project", depends_on=[]),
        Step(id="code", label="opencode builds project files", agent="opencode",
             prompt="Build the complete project based on the PRD", depends_on=["prd"],
             retry=RetryPolicy(max_retries=2, review_agent="claude")),
        Step(id="tests", label="codex writes test cases", agent="codex",
             prompt="Write comprehensive tests based on the PRD", depends_on=["prd"]),
        Step(id="verify", label="claude verifies code + tests", agent="claude",
             prompt="Review all project files for correctness", depends_on=["code", "tests"]),
        Step(id="docs", label="claude creates documentation", agent="claude",
             prompt="Write README and documentation", depends_on=["verify"]),
    ],
)

_AGENT_CAPABILITIES = {
    "opencode": "general coding, building full projects from scratch, file generation",
    "codex": "writing test cases, backend code, API implementations",
    "claude": "code review, verification, documentation, analysis",
    "qwen": "general coding, Q&A, quick scripts",
    "gemini": "general coding, research, analysis",
    "copilot": "quick coding tasks, boilerplate generation",
}


def _build_agents_context(available_agents: dict[str, bool]) -> str:
    lines = ["Available agents:"]
    for name, available in available_agents.items():
        cap = _AGENT_CAPABILITIES.get(name, "general purpose")
        status = "available" if available else "NOT installed"
        lines.append(f"  - {name} ({status}): {cap}")
    return "\n".join(lines)


async def research_and_plan(task: str, user_id: int, feedback: str = "") -> str:
    """Legacy: returns prose plan."""
    prompt = f"Create a detailed project plan for: {task}"
    if feedback:
        prompt += f"\n\nIncorporate the following feedback from the previous plan:\n{feedback}"
    return await ollama.generate(user_id, prompt, system=PLANNER_SYSTEM_PROMPT)


async def decompose_task(
    task: str,
    user_id: int,
    available_agents: dict[str, bool] | None = None,
) -> WorkflowPlan:
    """Decompose a task into structured WorkflowPlan via Ollama."""
    agents_context = _build_agents_context(available_agents or {})

    prompt = (
        f"Decompose this project request into a structured workflow plan.\n\n"
        f"Project request: {task}\n\n"
        f"{agents_context}\n\n"
        "Output ONLY valid JSON matching the schema. No markdown, no explanation."
    )

    try:
        response = await ollama.generate(user_id, prompt, system=DECOMPOSITION_SYSTEM_PROMPT)
        plan = _parse_plan_response(response, task)
        if plan and plan.steps:
            return plan
    except Exception:
        pass

    return DEFAULT_FALLBACK_PLAN


def _parse_plan_response(response: str, task: str) -> WorkflowPlan | None:
    """Parse Ollama's JSON response into a WorkflowPlan."""
    json_str = _extract_json_block(response)
    if not json_str:
        return None

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None

    steps_data = data.get("steps", [])
    if not steps_data or not isinstance(steps_data, list):
        return None

    steps = []
    for s in steps_data:
        if not isinstance(s, dict) or "id" not in s:
            continue
        retry_data = s.get("retry")
        retry = None
        if retry_data and isinstance(retry_data, dict):
            retry = RetryPolicy(
                max_retries=retry_data.get("max_retries", 2),
                review_agent=retry_data.get("review_agent", "claude"),
            )
        step = Step(
            id=s["id"],
            label=s.get("label", s["id"]),
            agent=s.get("agent", "brain"),
            prompt=s.get("prompt", ""),
            depends_on=s.get("depends_on", []),
            retry=retry,
            success_criteria=s.get("success_criteria", ""),
        )
        steps.append(step)

    if not steps:
        return None

    return WorkflowPlan(
        steps=steps,
        summary=data.get("summary", f"Plan for: {task[:100]}"),
    )


def _extract_json_block(text: str) -> str | None:
    """Extract JSON from model output, handling markdown fences."""
    patterns = [
        r"```(?:json)?\s*\n?(.*?)\n?```",
        r"\{[^{}]*\"steps\"[^{}]*\[.*\]\s*\}",
    ]
    for pat in patterns:
        match = re.search(pat, text, re.DOTALL)
        if match:
            return match.group(1) if pat.startswith("```") else match.group()
    return None
