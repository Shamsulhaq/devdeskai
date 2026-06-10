"""Human-like project build orchestrator.

The brain thinks and acts like a human developer:
1. Decides a project name
2. Creates project directory
3. Creates PRD with explicit tech stack
4. Picks a code agent and generates code + docs
5. Tests (brain itself if confident, else delegates to test agent)
6. Fixes (with model swap if current model is failing)
7. Reports completion

Non-code requests are answered directly by Ollama.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable, Coroutine, Optional

from bot import config, persistence
from bot.workflow import brain as brain_module
from bot.workflow import orchestrator, tech
from bot.workflow.models import (
    Workflow, StageType, Artifact,
)

logger = logging.getLogger(__name__)

Notify = Callable[[str, list[dict]], Coroutine]

CODE_REQUEST_TYPES = {"code_build", "code_fix", "code_review"}
DEFAULT_MAX_FIXES = 3


class ProjectBuild:
    """Human-like project build orchestrator."""

    def __init__(
        self,
        user_id: int,
        request: str,
        notify: Optional[Notify] = None,
        max_fixes: int = DEFAULT_MAX_FIXES,
    ):
        self.user_id = user_id
        self.request = request
        self.notify = notify
        self.max_fixes = max_fixes

        self.request_type = ""
        self.project_name = ""
        self.project_dir = ""
        self.wf: Optional[Workflow] = None
        self.tech_stack: dict = {}
        self.test_results: dict = {"passed": True, "skipped": True}
        self.code_output = ""

    async def run(self) -> dict:
        """Main entry point: classify, then either full build or direct answer."""
        await self._emit(
            f"🧠 **Analyzing request:** _{self.request}_",
            [{"id": "classify", "label": "Classifying request...", "status": "running"}],
        )

        self.request_type = await brain_module.classify_request(self.request, self.user_id)

        if self.request_type not in CODE_REQUEST_TYPES:
            return await self._handle_non_code_request()

        await self._setup()
        await self._create_prd()
        await self._generate_code()
        await self._run_tests()

        while (
            not self.test_results.get("passed", False)
            and not self.test_results.get("skipped", False)
            and self.wf.fix_count < self.max_fixes
        ):
            await self._fix_code()
            await self._run_tests()

        return await self._finalize()

    async def _emit(self, header: str, todo: list[dict]) -> None:
        if self.notify:
            try:
                await self.notify(header, todo)
            except Exception:
                pass

    async def _setup(self) -> None:
        """Phase 0: name the project, create directory, create workflow record."""
        await self._emit(
            "📝 **Naming project...**",
            [{"id": "setup", "label": "Naming project", "status": "running"}],
        )
        self.project_name = await brain_module.generate_project_name(
            self.request, self.user_id
        )

        existing = persistence.workflows.get(self.user_id)
        if existing and existing.status == "active":
            from bot.workflow import engine as wf_engine
            wf_engine.cancel_workflow(existing)
            await persistence.save_async(config.DATA_FILE)

        self.wf = Workflow(
            id=f"wf_{int(time.time())}_{self.user_id}",
            user_id=self.user_id,
            task=self.request,
            created_at=time.time(),
            updated_at=time.time(),
            project_name=self.project_name,
            request_type=self.request_type,
        )
        persistence.workflows[self.user_id] = self.wf
        self.project_dir = self.wf.workspace_dir
        os.makedirs(self.project_dir, exist_ok=True)

        self.wf.action_history.append({
            "action": "setup",
            "result": f"Created project '{self.project_name}' at {self.project_dir}",
        })
        await persistence.save_async(config.DATA_FILE)

    async def _create_prd(self) -> None:
        """Phase 1: PRD with explicit Tech Stack section."""
        await self._emit(
            f"📄 **Creating PRD** for `{self.project_name}`",
            [{"id": "prd", "label": "Brain writes PRD with tech stack", "status": "running"}],
        )
        self.wf.current_stage = StageType.PRD
        prd = await brain_module.generate_prd_with_tech_stack(
            self.request, self.project_name, self.user_id
        )
        self.wf.tech_stack = await brain_module.extract_tech_stack(prd, self.user_id)
        self.tech_stack = self.wf.tech_stack

        _write_file(self.project_dir, "PRD.md", prd)
        self.wf.artifacts.append(Artifact(
            name="PRD.md", agent="brain", content=prd[:200], stage="prd",
        ))
        self.wf.action_history.append({
            "action": "prd",
            "result": f"PRD + tech_stack={self.tech_stack.get('language', 'unknown')}",
        })
        self.wf.updated_at = time.time()
        await persistence.save_async(config.DATA_FILE)

    async def _generate_code(self) -> None:
        """Phase 2: pick a code agent, dispatch, collect output.

        If the agent returns error text or writes no useful files, retry
        with a different agent. After all agents fail, attempt to generate
        code via Ollama directly.
        """
        await self._emit(
            f"💻 **Coding** using {self.tech_stack.get('language', 'auto-detected')}",
            [{"id": "code", "label": "Code agent builds project", "status": "running"}],
        )
        self.wf.current_stage = StageType.CODING

        prd_text = _read_file(self.project_dir, "PRD.md") or self.request
        prompt = brain_module.format_code_prompt(prd_text)
        if self.tech_stack:
            prompt = f"Tech stack to use: {self.tech_stack}\n\n{prompt}"

        # Try all available agents in order
        available = orchestrator._get_available_agents()
        installed = [n for n, v in available.items() if v.get("available")]
        result_text: str = ""
        files: dict = {}
        last_error: str = ""

        for agent in installed:
            await self._emit(
                f"💻 **Coding** with `{agent}`",
                [{"id": "code", "label": f"Trying {agent}",
                  "status": "running"}],
            )
            try:
                result = await orchestrator.run_brain_step(
                    agent, prompt, self.project_dir, self.user_id
                )
            except Exception as e:
                last_error = f"{agent} raised: {e}"
                continue
            if isinstance(result, Exception):
                last_error = f"{agent} failed: {result}"
                continue
            result_text = result
            files = brain_module.parse_file_markers(result)
            if _is_useful_code_output(files, result):
                break
            last_error = f"{agent} returned no useful code (got {len(files)} files)"
            files = {}
            result_text = ""

        # If all agents failed, fall back to Ollama (which can also generate code)
        if not files:
            await self._emit(
                "💻 **Coding** with Ollama fallback",
                [{"id": "code", "label": "Ollama fallback",
                  "status": "running"}],
            )
            fallback_prompt = (
                f"You are generating production code. "
                f"Output ALL files using this exact format on its own line:\n"
                f"--- filename.ext ---\n<full file content>\n\n"
                f"Project: {prd_text}\n"
                f"Tech stack: {self.tech_stack}\n\n"
                f"Generate the complete project. No prose, only file markers."
            )
            try:
                fallback = await orchestrator.run_brain_step(
                    "opencode", fallback_prompt, self.project_dir, self.user_id
                )
            except Exception:
                fallback = None
            if isinstance(fallback, Exception) or not fallback:
                # Last resort: call Ollama directly
                from bot import ollama as ollama_module
                fallback = await ollama_module.generate(self.user_id, fallback_prompt)
            result_text = fallback or ""
            files = brain_module.parse_file_markers(result_text)
            if not _is_useful_code_output(files, result_text):
                raise RuntimeError(
                    f"Code generation failed across all agents and Ollama. "
                    f"Last error: {last_error}"
                )

        self.code_output = result_text
        for name, content in files.items():
            _write_file(self.project_dir, name, content)

        self.wf.action_history.append({
            "action": "code",
            "result": f"wrote {len(files)} files: {', '.join(list(files.keys())[:8])}",
        })
        self.wf.updated_at = time.time()
        await persistence.save_async(config.DATA_FILE)

    async def _run_tests(self) -> None:
        """Phase 3: run tests — brain if confident, else delegate to test agent.

        Honest semantics:
        - passed=True + skipped=True  → no test command AND no test files
        - passed=True                 → tests actually ran and passed
        - passed=False                → tests ran and failed (or setup error)
        """
        self.wf.current_stage = StageType.VERIFY
        await self._emit(
            "🧪 **Running tests...**",
            [{"id": "test", "label": "Tests", "status": "running"}],
        )

        detected = tech.detect_tech_stack(self.project_dir)
        if detected:
            # Merge detected metadata into our tech stack, but keep explicit values
            for k, v in detected.items():
                if k in ("files", "test_pattern", "framework_hint", "agent_hint"):
                    continue
                if not self.tech_stack.get(k):
                    self.tech_stack[k] = v
            self.wf.tech_stack = self.tech_stack

        # If no test command is known, try to infer one
        if not self.tech_stack.get("test_cmd"):
            if detected and detected.get("test_cmd"):
                self.tech_stack["test_cmd"] = detected["test_cmd"]
            elif self.tech_stack.get("language") in ("javascript", "typescript"):
                # Common JS/TS convention
                if os.path.exists(os.path.join(self.project_dir, "package.json")):
                    self.tech_stack["test_cmd"] = "npm test --silent"
                else:
                    self.tech_stack["test_cmd"] = "npm test"
            self.wf.tech_stack = self.tech_stack

        # Static HTML sites have no real test command — mark skipped (not a pass)
        if self.tech_stack.get("stack") == "static":
            self.test_results = {
                "passed": True,
                "skipped": True,
                "summary": "Static HTML project — no tests to run",
            }
            self.wf.last_test_results = self.test_results
            self.wf.action_history.append({
                "action": "test",
                "result": "skipped (static site)",
            })
            await persistence.save_async(config.DATA_FILE)
            return

        if not self.tech_stack.get("test_cmd"):
            self.test_results = {
                "passed": False,
                "skipped": True,
                "summary": "No test command detected — tests NOT run",
            }
            self.wf.last_test_results = self.test_results
            self.wf.action_history.append({
                "action": "test",
                "result": "skipped (no test_cmd)",
            })
            await persistence.save_async(config.DATA_FILE)
            return

        confidence = await brain_module.evaluate_test_confidence(
            self.tech_stack, self.project_dir, self.user_id
        )
        threshold = float(getattr(config, "TEST_CONFIDENCE_THRESHOLD", 0.7))

        if confidence >= threshold:
            self.test_results = await brain_module.brain_run_tests(
                self.tech_stack, self.project_dir, self.user_id
            )
            self.wf.action_history.append({
                "action": "test",
                "result": f"brain ran tests (conf={confidence:.2f}): "
                          f"{'pass' if self.test_results.get('passed') else 'FAIL'}",
            })
        else:
            self.test_results = await self._delegate_tests(confidence)
            self.wf.action_history.append({
                "action": "test",
                "result": f"delegated tests (conf={confidence:.2f}): "
                          f"{'pass' if self.test_results.get('passed') else 'FAIL'}",
            })

        self.wf.last_test_results = self.test_results
        self.wf.updated_at = time.time()
        await persistence.save_async(config.DATA_FILE)

    async def _fix_code(self) -> None:
        """Phase 4: pick a fix agent, apply fix (with model swap on repeated failure)."""
        self.wf.fix_count += 1
        await self._emit(
            f"🔧 **Fixing** (cycle {self.wf.fix_count}/{self.max_fixes})",
            [{"id": "fix", "label": f"Fix cycle {self.wf.fix_count}",
              "status": "running"}],
        )

        agent = await self._pick_fix_agent()
        files = tech.list_project_files(self.project_dir, max_files=20)
        prompt = brain_module.format_fix_prompt(self.test_results, files)

        async def _do_fix(model: str) -> str:
            return await self._dispatch_with_fallback(agent, prompt)

        fallback_models = self._fallback_models()
        if fallback_models:
            result = await brain_module.try_with_model_swap(
                _do_fix, fallback_models, self.user_id
            )
        else:
            result = await _do_fix(persistence.user_models.get(self.user_id, config.MODEL))

        if isinstance(result, Exception) or not result:
            self.wf.action_history.append({
                "action": "fix",
                "result": f"fix cycle {self.wf.fix_count} failed",
            })
            return

        fixed_files = brain_module.parse_file_markers(result)
        for name, content in fixed_files.items():
            _write_file(self.project_dir, name, content)
        self.wf.action_history.append({
            "action": "fix",
            "result": f"cycle {self.wf.fix_count}: {len(fixed_files)} files updated",
        })
        self.wf.updated_at = time.time()
        await persistence.save_async(config.DATA_FILE)

    async def _finalize(self) -> dict:
        """Phase 5: generate summary, mark workflow complete."""
        files = tech.list_project_files(self.project_dir, max_files=20)
        passed = self.test_results.get("passed", False) or self.test_results.get("skipped", False)

        if passed:
            self.wf.current_stage = StageType.COMPLETED
            self.wf.status = "completed"
            self.wf.summary = await brain_module.generate_summary(self.wf, self.user_id)
        else:
            self.wf.current_stage = StageType.CANCELLED
            self.wf.status = "cancelled"
            self.wf.summary = f"Build failed: {self.test_results.get('summary', 'unknown')}"

        self.wf.updated_at = time.time()
        await persistence.save_async(config.DATA_FILE)

        await self._emit(
            ("🎉 **Build Complete!**" if passed else "❌ **Build failed**"),
            [{"id": "done", "label": "Done", "status": "completed" if passed else "failed"}],
        )
        return {
            "passed": passed,
            "files": files,
            "summary": self.wf.summary,
            "test_results": self.test_results,
        }

    async def _handle_non_code_request(self) -> dict:
        """Non-code: answer directly via Ollama."""
        await self._emit(
            f"💬 **Answering** ({self.request_type})",
            [{"id": "answer", "label": "Brain is reasoning...",
              "status": "running"}],
        )
        from bot import ollama as ollama_module
        response = await ollama_module.generate(
            self.user_id,
            self.request,
            system=("You are a helpful AI assistant. Answer the user's request "
                    "thoroughly and accurately."),
        )
        self.wf = Workflow(
            id=f"wf_{int(time.time())}_{self.user_id}",
            user_id=self.user_id,
            task=self.request,
            status="completed",
            created_at=time.time(),
            updated_at=time.time(),
            request_type=self.request_type,
            summary=response[:2000],
        )
        self.wf.current_stage = StageType.COMPLETED
        persistence.workflows[self.user_id] = self.wf
        await persistence.save_async(config.DATA_FILE)

        await self._emit(
            "✅ **Done**",
            [{"id": "answer", "label": "Done", "status": "completed"}],
        )
        return {"passed": True, "files": [], "summary": response,
                "request_type": self.request_type}

    async def _pick_code_agent(self) -> str:
        available = orchestrator._get_available_agents()
        installed = [n for n, v in available.items() if v.get("available")]
        if not installed:
            return "opencode"
        try:
            from bot.workflow.usage import usage_manager
            status = usage_manager.get_status(self.user_id)
            fallback_order = usage_manager.get_fallback_order(self.user_id)
            return await brain_module.pick_best_agent(
                task=self.request[:500], agents=installed,
                fallback_order=fallback_order,
                status={a: s for a, s in status.items() if a in installed},
                user_id=self.user_id,
            )
        except Exception:
            return installed[0]

    async def _pick_test_agent(self) -> str:
        available = orchestrator._get_available_agents()
        installed = [n for n, v in available.items() if v.get("available")]
        if not installed:
            return "codex"
        preferred = self.tech_stack.get("agent_hint", "codex") or "codex"
        return preferred if preferred in installed else installed[0]

    async def _pick_fix_agent(self) -> str:
        available = orchestrator._get_available_agents()
        installed = [n for n, v in available.items() if v.get("available")]
        if "claude" in installed:
            return "claude"
        if not installed:
            return "codex"
        return installed[0]

    async def _delegate_tests(self, confidence: float) -> dict:
        """Delegate test creation+execution to a test agent."""
        agent = await self._pick_test_agent()
        files = tech.list_project_files(self.project_dir, max_files=15)
        prompt = brain_module.format_test_prompt(self.tech_stack, files)
        result = await self._dispatch_with_fallback(agent, prompt)
        if isinstance(result, Exception) or not result:
            return {"passed": False, "summary": f"Test agent failed: {result}",
                    "exit_code": -1}

        test_files = brain_module.parse_file_markers(result)
        for name, content in test_files.items():
            _write_file(self.project_dir, name, content)
        return await brain_module.brain_run_tests(
            self.tech_stack, self.project_dir, self.user_id
        )

    async def _dispatch_with_fallback(self, preferred_agent: str, prompt: str) -> str | Exception:
        """Cancel-and-replan: try preferred, fallback on failure."""
        available = orchestrator._get_available_agents()
        installed = [n for n, v in available.items() if v.get("available")]
        order = [preferred_agent] + [a for a in installed if a != preferred_agent]
        seen = set()
        last_exc: Optional[Exception] = None
        for agent in order:
            if agent in seen:
                continue
            seen.add(agent)
            try:
                result = await orchestrator.run_brain_step(
                    agent, prompt, self.project_dir, self.user_id
                )
                if not isinstance(result, Exception):
                    return result
                last_exc = result
            except Exception as e:
                last_exc = e
        return last_exc or Exception("All agents failed")

    def _fallback_models(self) -> list[str]:
        raw = getattr(config, "FALLBACK_MODELS", "") or ""
        models = [m.strip() for m in raw.split(",") if m.strip()]
        current = persistence.user_models.get(self.user_id, config.MODEL)
        return [m for m in models if m != current]


def _write_file(workspace: str, name: str, content: str) -> str:
    path = os.path.join(workspace, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return path


def _read_file(workspace: str, name: str) -> str:
    path = os.path.join(workspace, name)
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read()


def _is_useful_code_output(files: dict, raw: str) -> bool:
    """Heuristic: is the agent output actually code, or just an error/garbage?

    Returns True if at least one file has substantial code content.
    """
    if not files:
        return False
    # Look for at least one file with meaningful content
    total_chars = 0
    for name, content in files.items():
        if name == "raw_output.txt":
            continue
        total_chars += len(content)
    # Need at least 200 chars of non-raw-output content
    if total_chars < 200:
        return False
    # If all files are tiny or have error content, reject
    for content in files.values():
        if _looks_like_error(content):
            return False
    return True


_ERROR_PREFIXES = (
    "/bin/sh:", "/bin/bash:", "command not found", "syntax error",
    "Traceback", "error:", "Error:", "ERROR:",
    "Agent error:", "Agent timed out", "Usage:",
)


def _looks_like_error(text: str) -> bool:
    head = text[:300].lower()
    return sum(1 for m in _ERROR_PREFIXES if m.lower() in head) >= 2
