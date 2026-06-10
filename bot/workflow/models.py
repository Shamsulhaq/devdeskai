from dataclasses import dataclass, field, asdict
from enum import Enum
import os


class StageType(str, Enum):
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
class Workflow:
    id: str
    user_id: int
    task: str
    current_stage: StageType = StageType.PRD
    status: str = "active"
    artifacts: list[Artifact] = field(default_factory=list)
    action_history: list[dict] = field(default_factory=list)
    fix_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    summary: str = ""

    @property
    def workspace_dir(self) -> str:
        """Agent workspace — per-project, isolated. Agents only see their project."""
        from bot import config
        base = os.path.join(config.WORKSPACE_DIR, str(self.user_id))
        target = os.path.realpath(os.path.join(base, "projects", self.id))
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
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Workflow":
        data = dict(data)
        data["current_stage"] = StageType(data["current_stage"])
        data["artifacts"] = [Artifact(**a) for a in data.get("artifacts", [])]
        data["action_history"] = list(data.get("action_history", []))
        return cls(**data)
