import os
import time
from typing import Callable, Coroutine

from bot import config, persistence
from bot.workflow.models import Workflow, StageType, Step
from bot.workflow import planner, brain as brain_module, orchestrator

Notify = Callable[[str, list[dict]], Coroutine]


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
    """Generic DAG executor: plan → execute steps → review → complete.

    Steps run respecting dependency order. Independent steps execute in
    parallel. Steps with retry policies go through a review gate.
    """
    workspace = wf.workspace_dir
    os.makedirs(workspace, exist_ok=True)

    available = {
        name: info.get("available", False)
        for name, info in orchestrator._get_available_agents().items()
    }
    work_plan = await planner.decompose_task(wf.task, wf.user_id, available)
    wf.plan = work_plan
    wf.current_stage = StageType.PLANNING
    wf.updated_at = time.time()
    await persistence.save_async(config.DATA_FILE)

    steps = work_plan.steps
    step_map: dict[str, Step] = {s.id: s for s in steps}

    if notify:
        await notify(
            f"🧠 **Plan ready!** {len(steps)} steps\n_{work_plan.summary}_",
            _todo_list(steps),
        )

    steps_log = []
    all_files = set()
    max_parallel = 3

    while True:
        ready = _ready_steps(steps, step_map)
        if not ready:
            remaining = [s for s in steps if s.status == "pending"]
            if not remaining:
                break
            waiting = [s.id for s in remaining]
            steps_log.append(f"⏳ Deadlock or waiting on: {', '.join(waiting)}")
            break

        batch = ready[:max_parallel]
        for s in batch:
            s.status = "running"
        if notify:
            await notify("**Executing steps...**", _todo_list(steps))

        results = await _execute_batch(batch, wf, workspace)

        for step, result in zip(batch, results):
            if isinstance(result, Exception):
                step.status = "failed"
                steps_log.append(f"❌ {step.label} failed: {result}")
                if notify:
                    await notify(f"❌ **{step.label}** failed", _todo_list(steps))
                continue

            if step.retry and step.retry.max_retries > 0:
                passed, issues = await _review_step(wf, step, result)
                if passed:
                    step.status = "completed"
                    steps_log.append(f"✅ {step.label} passed review")
                else:
                    fixed = await _rework_step(wf, step, result, issues, workspace)
                    if fixed:
                        step.status = "completed"
                        steps_log.append(f"✅ {step.label} fixed after rework")
                    else:
                        step.status = "failed"
                        steps_log.append(f"❌ {step.label} failed review")
            else:
                step.status = "completed"
                steps_log.append(f"✅ {step.label}")

            if step.status == "completed" and step.agent != "brain":
                step_files = brain_module.parse_file_markers(step.output)
                for name, content in step_files.items():
                    _write_file(workspace, name, content)
                    all_files.add(name)

            wf.action_history.append({
                "action": step.id,
                "result": f"{step.label}: {step.status}",
            })
            wf.updated_at = time.time()
            await persistence.save_async(config.DATA_FILE)

        if notify:
            await notify(
                f"**Progress:** {sum(1 for s in steps if s.status == 'completed')}/{len(steps)} steps",
                _todo_list(steps),
            )

    failed = [s for s in steps if s.status == "failed"]
    if failed:
        wf.current_stage = StageType.CANCELLED
        wf.status = "cancelled"
        wf.updated_at = time.time()
        await persistence.save_async(config.DATA_FILE)
        return {
            "error": f"Failed steps: {', '.join(s.id for s in failed)}",
            "steps": steps_log,
            "files": sorted(all_files),
        }

    wf.summary = await brain_module.generate_summary(wf, wf.user_id)
    wf.current_stage = StageType.COMPLETED
    wf.status = "completed"
    wf.updated_at = time.time()
    await persistence.save_async(config.DATA_FILE)

    if notify:
        await notify(
            f"🎉 **Build Complete!**\n\n{wf.summary[:500]}\n\n📁 {workspace}",
            _todo_list(steps),
        )

    return {
        "steps": steps_log,
        "files": sorted(all_files),
        "summary": wf.summary,
    }


def cancel_workflow(wf: Workflow) -> None:
    wf.current_stage = StageType.CANCELLED
    wf.status = "cancelled"
    wf.updated_at = time.time()


def _todo_list(steps: list[Step]) -> list[dict]:
    return [
        {"id": s.id, "label": s.label, "status": s.status}
        for s in steps
    ]


def _ready_steps(steps: list[Step], step_map: dict[str, Step]) -> list[Step]:
    """Return steps whose dependencies are all completed."""
    ready = []
    for s in steps:
        if s.status != "pending":
            continue
        if all(step_map.get(dep, Step("", "", "", "")).status == "completed"
               for dep in s.depends_on):
            ready.append(s)
    return ready


async def _execute_batch(batch: list[Step], wf: Workflow, workspace: str) -> list:
    """Execute a batch of steps in parallel via orchestrator."""
    from bot.workflow import orchestrator as orch
    tasks = []
    for step in batch:
        if step.agent == "brain":
            tasks.append(brain_module.execute_brain_step(step, wf.user_id))
        else:
            tasks.append(orch.run_brain_step(
                step.agent, step.prompt, workspace, wf.user_id
            ))
    return await asyncio_gather_safe(tasks)


async def asyncio_gather_safe(tasks: list) -> list:
    """Gather with return_exceptions, importing asyncio locally."""
    import asyncio
    return await asyncio.gather(*tasks, return_exceptions=True)


async def _review_step(wf: Workflow, step: Step, output: str) -> tuple[bool, str]:
    """Review step output via brain. Returns (passed, issues)."""
    criteria = step.success_criteria or "Verify correctness, completeness, and quality"
    evaluation = await brain_module.evaluate_step(
        step_id=step.id,
        output=output,
        criteria=criteria,
        user_id=wf.user_id,
    )
    if evaluation.get("verdict") == "pass":
        return True, ""
    return False, evaluation.get("issues", "Verification failed")


async def _rework_step(
    wf: Workflow, step: Step, output: str, issues: str, workspace: str,
) -> bool:
    """Retry a step up to its max_retries, with brain-driven root cause analysis."""
    max_retries = step.retry.max_retries if step.retry else 2
    attempt = 0
    fix_attempts: list[dict] = []
    current_output = output

    while attempt < max_retries:
        attempt += 1
        analysis = await brain_module.analyze_root_cause(
            verify_report=issues,
            files={},
            previous_attempts=fix_attempts,
            user_id=wf.user_id,
        )
        fix_attempts.append({
            "cycle": attempt,
            "issues": issues,
            "root_cause": analysis.get("root_cause", issues),
            "approach": analysis.get("approach", "Fix the issues"),
        })
        fix_prompt = (
            f"Previous attempt had these issues:\n{issues}\n\n"
            f"Root cause: {analysis.get('root_cause', 'Unknown')}\n"
            f"Approach: {analysis.get('approach', 'Fix the issues')}\n\n"
            f"Original task:\n{step.prompt}\n\n"
            f"Previous output:\n{current_output[:3000]}\n\n"
            "Output the corrected version."
        )
        result = await orchestrator.run_brain_step(
            step.agent, fix_prompt, workspace, wf.user_id
        )
        if isinstance(result, Exception):
            continue

        evaluation = await brain_module.evaluate_step(
            step_id=step.id,
            output=result,
            criteria=step.success_criteria or "Verify the fix is correct",
            user_id=wf.user_id,
        )
        if evaluation.get("verdict") == "pass":
            step.output = result
            wf.fix_count += 1
            wf.action_history.append({
                "action": f"rework_{step.id}",
                "result": f"Fixed after {attempt} rework(s)",
            })
            return True

        issues = evaluation.get("issues", "Still failing")
        current_output = result

    wf.action_history.append({
        "action": f"rework_{step.id}",
        "result": f"Failed after {attempt} rework(s)",
    })
    return False


def _write_file(workspace: str, name: str, content: str) -> str:
    path = os.path.join(workspace, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return path
