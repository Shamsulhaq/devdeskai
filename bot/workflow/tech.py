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
    "typescript": {
        "files": ["tsconfig.json", "package.json"],
        "test_cmd": "npm test",
        "test_pattern": "package.json",
        "language": "typescript",
        "framework_hint": ["next", "react", "vue", "angular", "express", "nest"],
        "agent_hint": "opencode",
    },
    "nextjs": {
        "files": ["next.config.js", "next.config.mjs", "next.config.ts"],
        "test_cmd": "npm test",
        "test_pattern": "package.json",
        "language": "typescript",
        "framework_hint": ["next.js", "nextjs", "next"],
        "agent_hint": "opencode",
        "build_cmd": "npm run build",
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

# Aliases the LLM might emit in PRD tech-stack sections
_LANGUAGE_ALIASES = {
    "ts": "typescript",
    "typescript": "typescript",
    "js": "javascript",
    "javascript": "javascript",
    "node": "javascript",
    "node.js": "javascript",
    "nodejs": "javascript",
    "py": "python",
    "python": "python",
    "rs": "rust",
    "rust": "rust",
    "go": "go",
    "golang": "go",
    "java": "java",
    "rb": "ruby",
    "ruby": "ruby",
    "next": "typescript",
    "next.js": "typescript",
    "nextjs": "typescript",
}

_FRAMEWORK_TO_STACK = {
    "next": "typescript",
    "next.js": "typescript",
    "nextjs": "typescript",
    "react": "javascript",
    "vue": "javascript",
    "angular": "typescript",
    "express": "javascript",
    "nest": "typescript",
    "nestjs": "typescript",
    "flask": "python",
    "django": "python",
    "fastapi": "python",
    "actix": "rust",
    "axum": "rust",
    "rocket": "rust",
    "gin": "go",
    "echo": "go",
    "fiber": "go",
    "spring": "java",
    "rails": "ruby",
    "sinatra": "ruby",
}


def detect_tech_stack(project_dir: str) -> Optional[dict]:
    """Detect the dominant tech stack by scanning project files.

    Returns the first matching stack's metadata dict, or None.
    """
    if not os.path.isdir(project_dir):
        return None
    # Check most specific markers first
    for stack_key in ("nextjs", "typescript", "node", "python", "rust", "go", "java", "ruby", "static"):
        meta = TECH_STACKS[stack_key]
        for marker in meta["files"]:
            if os.path.exists(os.path.join(project_dir, marker)):
                return {"stack": stack_key, **meta}
    return None


def validate_tech_stack(stack_data: dict) -> dict:
    """Validate a tech_stack dict (from PRD) against known stacks.

    Fills in test_cmd, build_cmd, lint_cmd if missing. Returns a normalized dict.
    Maps free-form language strings (TypeScript, Next.js, etc.) to known stacks.
    """
    if not isinstance(stack_data, dict):
        return {}
    raw_lang = (stack_data.get("language") or "").lower().strip()
    raw_fw = (stack_data.get("framework") or "").lower().strip()

    # Map the LLM's free-form language to a canonical stack
    canonical = _LANGUAGE_ALIASES.get(raw_lang, raw_lang)
    # If language is unknown but framework is a known one, infer language
    if canonical not in {m["language"] for m in TECH_STACKS.values()} and canonical not in TECH_STACKS:
        fw_stack = _FRAMEWORK_TO_STACK.get(raw_fw)
        if fw_stack:
            canonical = TECH_STACKS[fw_stack]["language"]

    # Find the stack meta
    matched = None
    for stack_key, meta in TECH_STACKS.items():
        if canonical == meta["language"] or canonical == stack_key:
            matched = (stack_key, meta)
            break

    if matched:
        stack_key, meta = matched
        test_cmd = stack_data.get("test_cmd") or meta["test_cmd"]
        build_cmd = stack_data.get("build_cmd") or meta.get("build_cmd", "")
        return {
            "stack": stack_key,
            "language": meta["language"],
            "test_cmd": test_cmd,
            "build_cmd": build_cmd,
            "framework": stack_data.get("framework", ""),
            "lint_cmd": stack_data.get("lint_cmd", ""),
        }
    return {
        "stack": "unknown",
        "language": canonical or "unknown",
        "test_cmd": stack_data.get("test_cmd", ""),
        "build_cmd": stack_data.get("build_cmd", ""),
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
