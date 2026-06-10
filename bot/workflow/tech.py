"""Tech stack detection and validation.

Detects programming language/framework from project files and provides
canonical test commands. Used by the brain to:
- Verify a PRD's stated tech stack
- Pick the right test command at runtime
- Validate a project is buildable
"""
from __future__ import annotations

import os
from typing import Optional

TECH_STACKS: dict[str, dict] = {
    "node": {
        "files": ["package.json"],
        "test_cmd": "npm test",
        "test_pattern": "package.json",
        "language": "javascript",
        "framework_hint": ["express", "react", "vue", "next", "nest"],
        "agent_hint": "opencode",
    },
    "python": {
        "files": ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
        "test_cmd": "pytest",
        "test_pattern": "pyproject.toml",
        "language": "python",
        "framework_hint": ["flask", "django", "fastapi"],
        "agent_hint": "codex",
    },
    "rust": {
        "files": ["Cargo.toml"],
        "test_cmd": "cargo test",
        "test_pattern": "Cargo.toml",
        "language": "rust",
        "framework_hint": ["actix", "axum", "rocket"],
        "agent_hint": "codex",
    },
    "go": {
        "files": ["go.mod"],
        "test_cmd": "go test ./...",
        "test_pattern": "go.mod",
        "language": "go",
        "framework_hint": ["gin", "echo", "fiber"],
        "agent_hint": "codex",
    },
    "java": {
        "files": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "test_cmd": "mvn test",
        "test_pattern": "pom.xml",
        "language": "java",
        "framework_hint": ["spring", "quarkus"],
        "agent_hint": "codex",
    },
    "ruby": {
        "files": ["Gemfile"],
        "test_cmd": "bundle exec rspec",
        "test_pattern": "Gemfile",
        "language": "ruby",
        "framework_hint": ["rails", "sinatra"],
        "agent_hint": "codex",
    },
    "static": {
        "files": ["index.html"],
        "test_cmd": "",
        "test_pattern": "index.html",
        "language": "html",
        "framework_hint": [],
        "agent_hint": "opencode",
    },
}


def detect_tech_stack(project_dir: str) -> Optional[dict]:
    """Detect the dominant tech stack by scanning project files.

    Returns the first matching stack's metadata dict, or None.
    """
    if not os.path.isdir(project_dir):
        return None
    for stack_key, meta in TECH_STACKS.items():
        for marker in meta["files"]:
            if os.path.exists(os.path.join(project_dir, marker)):
                return {"stack": stack_key, **meta}
    return None


def validate_tech_stack(stack_data: dict) -> dict:
    """Validate a tech_stack dict (from PRD) against known stacks.

    Fills in test_cmd if missing. Returns a normalized dict.
    """
    if not isinstance(stack_data, dict):
        return {}
    language = (stack_data.get("language") or "").lower()
    for stack_key, meta in TECH_STACKS.items():
        if language == meta["language"] or stack_key in language:
            normalized = {
                "stack": stack_key,
                "language": meta["language"],
                "test_cmd": stack_data.get("test_cmd") or meta["test_cmd"],
                "framework": stack_data.get("framework", ""),
                "lint_cmd": stack_data.get("lint_cmd", ""),
            }
            return normalized
    return {
        "stack": "unknown",
        "language": language or "unknown",
        "test_cmd": stack_data.get("test_cmd", ""),
        "framework": stack_data.get("framework", ""),
        "lint_cmd": stack_data.get("lint_cmd", ""),
    }


def list_project_files(project_dir: str, max_files: int = 50) -> list[str]:
    """Return a list of project files (relative to project_dir)."""
    if not os.path.isdir(project_dir):
        return []
    files = []
    for root, _dirs, names in os.walk(project_dir):
        for name in names:
            if name.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(root, name), project_dir)
            files.append(rel)
            if len(files) >= max_files:
                return sorted(files)
    return sorted(files)


def has_external_services(tech_stack: dict) -> bool:
    """Heuristic: does this tech stack need external services to test?"""
    if not tech_stack:
        return False
    framework = (tech_stack.get("framework") or "").lower()
    markers = ["django", "rails", "spring", "postgres", "mysql", "redis", "mongo"]
    return any(m in framework for m in markers)
