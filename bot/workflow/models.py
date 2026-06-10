from dataclasses import dataclass, field, asdict
from enum import Enum
import os
from typing import Optional


class StageType(str, Enum):
    PLANNING = "planning"
    PRD = "prd"
    CODING = "coding"
    VERIFY = "verify"
    DOCS = "docs"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class Artifact:
    name: str
    agent: str
    content: str
    stage: str


@dataclass
class RetryPolicy:
    max_retries: int = 2
    review_agent: str = "claude"


@dataclass
class Step:
    id: str
    label: str
    agent: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)
    retry: Optional[RetryPolicy] = None
    success_criteria: str = ""
    status: str = "pending"
    output: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.retry is not None:
            d["retry"] = asdict(self.retry)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        retry_data = data.pop("retry", None)
        if retry_data:
            data["retry"] = RetryPolicy(**retry_data)
        return cls(**data)


@dataclass
class WorkflowPlan:
    steps: list[Step] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowPlan":
        steps = [Step.from_dict(s) for s in data.get("steps", [])]
        return cls(steps=steps, summary=data.get("summary", ""))


@dataclass
class Workflow:
    id: str
    user_id: int
    task: str
    current_stage: StageType = StageType.PLANNING
    status: str = "active"
    artifacts: list[Artifact] = field(default_factory=list)
    action_history: list[dict] = field(default_factory=list)
    fix_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    summary: str = ""
    plan: Optional[WorkflowPlan] = None
    project_name: str = ""
    tech_stack: Optional[dict] = None
    request_type: str = ""
    model_history: list[str] = field(default_factory=list)
    last_test_results: Optional[dict] = None

    @property
    def workspace_dir(self) -> str:
        """Agent workspace — per-project, isolated. Agents only see their project."""
        from bot import config
        base = os.path.join(config.WORKSPACE_DIR, str(self.user_id))
        target = os.path.realpath(os.path.join(base, "projects", self.project_name or self.id))
        if not target.startswith(os.path.realpath(base) + os.sep):
            raise ValueError("Path traversal detected")
        os.makedirs(target, exist_ok=True)
        return target

    @staticmethod
    def brain_dir(user_id: int) -> str:
        """Brain directory — only the engine (brain's hands) accesses this."""
        from bot import config
        path = os.path.realpath(os.path.join(config.WORKSPACE_DIR, str(user_id), "brain"))
        os.makedirs(path, exist_ok=True)
        return path

    def user_root(self) -> str:
        """User workspace root — only the engine should access this."""
        from bot import config
        return os.path.realpath(os.path.join(config.WORKSPACE_DIR, str(self.user_id)))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["current_stage"] = self.current_stage.value
        if self.plan is not None:
            d["plan"] = self.plan.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Workflow":
        data = dict(data)
        data["current_stage"] = StageType(data.get("current_stage", "planning"))
        data["artifacts"] = [Artifact(**a) for a in data.get("artifacts", [])]
        data["action_history"] = list(data.get("action_history", []))
        data["model_history"] = list(data.get("model_history", []))
        plan_data = data.pop("plan", None)
        if plan_data:
            data["plan"] = WorkflowPlan.from_dict(plan_data)
        return cls(**data)
