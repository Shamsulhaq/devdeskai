import os
import time
from typing import Callable, Coroutine

from bot import config, persistence
from bot.workflow.models import Workflow, StageType, Artifact
from bot.workflow import brain, orchestrator

Notify = Callable[[str, list[dict]], Coroutine]

TODO_PLAN = [
    {"id": "prd",   "label": "Brain creates PRD"},
    {"id": "code",  "label": "opencode builds project files"},
    {"id": "tests", "label": "codex writes test cases"},
    {"id": "verify","label": "claude verifies code + tests"},
    {"id": "fix",   "label": "Fix issues (if needed)"},
    {"id": "docs",  "label": "claude creates documentation"},
    {"id": "done",  "label": "Build complete"},
]


async def create_workflow(user_id: int, task: str) -> Workflow:
    wf = Workflow(
        id=f"wf_{int(time.time())}_{user_id}",
        user_id=user_id,
        task=task,
        created_at=time.time(),
        updated_at=time.time(),
    )
    persistence.workflows[user_id] = wf
    await persistence.save_async(config.DATA_FILE)
    return wf


async def run_brain_build(wf: Workflow, notify: Notify | None = None) -> dict:
    """Brain-driven autonomous build with live todo progress updates."""
    workspace = wf.workspace_dir
    os.makedirs(workspace, exist_ok=True)

    todo = [dict(t, status="pending") for t in TODO_PLAN]
    if notify:
        await notify("🧠 **Brain engaged!** Starting build...", todo)

    steps_log = []
    max_fix = 5
    fix_attempts: list[dict] = []

    # ── Step 1: Brain generates PRD ──
    # PRD saved to brain_dir (brain-only root) AND workspace (agent project folder)
    todo[0]["status"] = "in_progress"
    if notify:
        await notify(f"**Step 1/7:** {todo[0]['label']}", todo)

    prd = await brain.generate_prd(wf.task, wf.user_id)
    brain_dir = Workflow.brain_dir(wf.user_id)
    _write_file(brain_dir, f"{wf.id}-PRD.md", prd)   # brain root access
    _write_file(workspace, "PRD.md", prd)              # agent project access
    wf.artifacts.append(Artifact(name="PRD.md", agent="brain", content=prd[:200], stage="prd"))
    wf.action_history.append({"action": "generate_prd", "result": "PRD created"})
    wf.current_stage = StageType.CODING
    wf.updated_at = time.time()
    await persistence.save_async(config.DATA_FILE)
    steps_log.append("1. ✅ Brain created the PRD")
    todo[0]["status"] = "completed"

    # ── Step 2: Parallel dispatch (opencode → code, codex → tests) ──
    todo[1]["status"] = "in_progress"
    todo[2]["status"] = "in_progress"
    if notify:
        await notify("**Step 2/7:** Building project + writing tests in parallel...", todo)

    code_prompt = brain.format_code_prompt(prd)
    tests_prompt = brain.format_tests_prompt(prd)

    code_result, tests_result = await orchestrator.run_parallel(
        [{"agent": "opencode", "prompt": code_prompt},
         {"agent": "codex", "prompt": tests_prompt}],
        workspace, wf.user_id,
    )

    if isinstance(code_result, Exception):
        todo[1]["status"] = "failed"
        if notify:
            await notify(f"❌ **Error:** opencode failed — {code_result}", todo)
        return {"error": f"opencode failed: {code_result}", "steps": steps_log}
    if isinstance(tests_result, Exception):
        todo[2]["status"] = "failed"
        if notify:
            await notify(f"❌ **Error:** codex failed — {tests_result}", todo)
        return {"error": f"codex failed: {tests_result}", "steps": steps_log}

    code_files = brain.parse_file_markers(code_result)
    test_files = brain.parse_file_markers(tests_result)
    for name, content in code_files.items():
        _write_file(workspace, name, content)
    for name, content in test_files.items():
        _write_file(workspace, name, content)

    wf.action_history.append({"action": "dispatch_code", "result": f"opencode: {len(code_files)} files"})
    wf.action_history.append({"action": "dispatch_tests", "result": f"codex: {len(test_files)} files"})
    await persistence.save_async(config.DATA_FILE)

    code_count = len([n for n in _list_workspace_files(workspace) if n != "PRD.md" and not n.startswith(".")])
    steps_log.append(f"2. ✅ opencode built project | codex wrote tests")
    todo[1]["status"] = "completed"
    todo[2]["status"] = "completed"
    if notify:
        await notify(f"**Step 2/7 Complete:** {code_count} files in workspace", todo)

    # ── Step 3–4: Fix-verify loop ──
    for cycle in range(max_fix):
        wf.current_stage = StageType.VERIFY
        wf.updated_at = time.time()
        await persistence.save_async(config.DATA_FILE)

        file_contents = _read_project_files(workspace)
        if not file_contents:
            steps_log.append("3. ⚠️  No project files found")
            break

        todo[3]["status"] = "in_progress"
        todo[3]["label"] = f"claude verifies (cycle {cycle + 1})"
        if notify:
            await notify(f"**Step 3/7:** {todo[3]['label']}", todo)

        verify_prompt = brain.format_verify_prompt(file_contents)
        verify_result = await orchestrator.run_brain_step(
            "claude", verify_prompt, workspace, wf.user_id
        )

        wf.action_history.append({"action": "verify", "result": verify_result[:300]})
        wf.artifacts.append(Artifact(
            name=f"verify_report_{cycle}.txt", agent="claude",
            content=verify_result[:200], stage="verify"
        ))
        await persistence.save_async(config.DATA_FILE)

        evaluation = await brain.evaluate_verification(verify_result, wf.user_id)

        if evaluation.get("verdict") == "pass":
            steps_log.append(f"3. ✅ claude verified — all good (cycle {cycle + 1})")
            wf.fix_count = cycle
            todo[3]["status"] = "completed"
            if notify:
                await notify("✅ **Verification passed!** Moving to documentation...", todo)
            break

        issues = evaluation.get("issues", "Verification failed")
        steps_log.append(f"3. ⚠️  Issues found (cycle {cycle + 1}/{max_fix})")

        todo[3]["status"] = "failed"
        if notify:
            await notify(f"⚠️ **Verification failed** (cycle {cycle + 1}/{max_fix})", todo)

        # Root cause analysis
        todo[4]["status"] = "in_progress"
        todo[4]["label"] = f"Brain analyzing root cause (cycle {cycle + 1})"
        if notify:
            await notify(f"**Step 4/7:** {todo[4]['label']}", todo)

        analysis = await brain.analyze_root_cause(
            verify_result, file_contents, fix_attempts, wf.user_id
        )
        root_cause = analysis.get("root_cause", issues)
        approach = analysis.get("approach", "Fix the issues")

        fix_attempts.append({
            "cycle": cycle + 1, "issues": issues,
            "root_cause": root_cause, "approach": approach,
        })

        keep_going = await brain.should_retry(cycle, max_fix, fix_attempts, wf.user_id)
        if not keep_going:
            steps_log.append("4. ⚠️  Brain decided to stop")
            todo[4]["status"] = "failed"
            if notify:
                await notify("⚠️ **Brain decided to stop fixing.** Moving to documentation...", todo)
            break

        if cycle == max_fix - 1:
            steps_log.append(f"4. ⚠️  Max fix attempts ({max_fix}) reached")
            if notify:
                await notify(f"⚠️ **Max {max_fix} fix attempts reached.** Moving on...", todo)
            break

        # Apply fix
        todo[4]["label"] = f"opencode applying fix (cycle {cycle + 1})"
        if notify:
            await notify(f"**Step 4/7:** 🔧 {todo[4]['label']}", todo)

        fix_prompt = brain.format_fix_prompt(
            root_cause, approach, file_contents, fix_attempts
        )
        fix_result = await orchestrator.run_brain_step(
            "opencode", fix_prompt, workspace, wf.user_id
        )

        fixed_files = brain.parse_file_markers(fix_result)
        for name, content in fixed_files.items():
            _write_file(workspace, name, content)

        wf.action_history.append({
            "action": "fix",
            "result": f"fix cycle {cycle + 1}: {len(fixed_files)} files",
        })
        wf.fix_count = cycle + 1
        await persistence.save_async(config.DATA_FILE)
        steps_log.append(f"4. ✅ Fix applied — {len(fixed_files)} files (cycle {cycle + 1})")

        todo[3]["status"] = "pending"  # re-verify
        todo[4]["status"] = "completed"

    # ── Step 5: Documentation ──
    todo[5]["status"] = "in_progress"
    if notify:
        await notify(f"**Step 5/7:** {todo[5]['label']}", todo)

    project_files = _list_workspace_files(workspace)
    file_contents = _read_project_files(workspace)
    doc_prompt = brain.format_doc_prompt(project_files, file_contents)
    doc_result = await orchestrator.run_brain_step(
        "claude", doc_prompt, workspace, wf.user_id
    )

    doc_files = brain.parse_file_markers(doc_result)
    for name, content in doc_files.items():
        _write_file(workspace, name, content)

    wf.action_history.append({"action": "document", "result": "README created"})
    wf.artifacts.append(Artifact(name="README.md", agent="claude", content="documentation generated", stage="docs"))
    steps_log.append("5. 📝 claude created documentation")
    todo[5]["status"] = "completed"

    # ── Complete ──
    project_files = _list_workspace_files(workspace)
    wf.summary = await brain.generate_summary(wf, wf.user_id)
    wf.current_stage = StageType.COMPLETED
    wf.status = "completed"
    wf.updated_at = time.time()
    await persistence.save_async(config.DATA_FILE)
    steps_log.append("6. ✅ Build complete!")

    for item in todo:
        if item["status"] in ("pending", "in_progress"):
            item["status"] = "completed"
    todo[6]["status"] = "completed"
    if notify:
        files_block = "\n".join(f"• {f}" for f in project_files[:15])
        await notify(
            f"🎉 **Build Complete!**\n\n"
            f"📁 `{workspace}`\n"
            f"{files_block}",
            todo,
        )

    return {
        "steps": steps_log,
        "files": project_files,
        "summary": wf.summary,
    }


def cancel_workflow(wf: Workflow) -> None:
    wf.current_stage = StageType.CANCELLED
    wf.status = "cancelled"
    wf.updated_at = time.time()


def _write_file(workspace: str, name: str, content: str) -> str:
    path = os.path.join(workspace, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return path


def _read_project_files(workspace: str) -> dict[str, str]:
    files = {}
    for root, _dirs, names in os.walk(workspace):
        for name in names:
            if name.startswith(".") or name == "PRD.md":
                continue
            path = os.path.join(root, name)
            try:
                with open(path) as f:
                    content = f.read()
                rel = os.path.relpath(path, workspace)
                files[rel] = content
            except Exception:
                pass
    return files


def _list_workspace_files(workspace: str) -> list[str]:
    if not os.path.isdir(workspace):
        return []
    files = []
    for root, _dirs, names in os.walk(workspace):
        for name in names:
            if name.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(root, name), workspace)
            files.append(rel)
    return sorted(files)
