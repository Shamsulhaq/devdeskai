from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, persistence
from bot.ollama import reply_long
from bot.workflow import engine
from bot.workflow.builder import ProjectBuild
from bot.workflow.usage import usage_manager

STATUS_ICON = {
    "pending": "⬜",
    "running": "⏳",
    "completed": "✅",
    "failed": "❌",
}


def _render_todo(todo: list[dict], header: str) -> str:
    lines = [header, ""]
    for item in todo:
        icon = STATUS_ICON.get(item["status"], "⬜")
        label = escape(str(item.get("label", item.get("id", "?"))))
        lines.append(f"{icon} {label}")
    return "\n".join(lines)


def _steps_to_todo(wf) -> list[dict]:
    if wf.plan and wf.plan.steps:
        return [
            {"id": s.id, "label": s.label, "status": s.status}
            for s in wf.plan.steps
        ]
    return []


async def start_build(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    task = " ".join(context.args) if context.args else ""

    if not task:
        await update.message.reply_text(
            "Usage: /build <request>\n"
            "Examples:\n"
            "  /build develop a personal website for a software engineer\n"
            "  /build write me a Python CLI tool that converts CSV to JSON\n"
            "  /build explain the difference between async and sync Python\n"
            "  /build write a poem about the ocean"
        )
        return

    progress_msg = await update.message.reply_text("🧠 Brain engaged...")

    async def notify(header: str, todo: list[dict]) -> None:
        text = _render_todo(todo, header)
        try:
            await progress_msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            pass

    await notify(
        f"🧠 **Brain engaged!**\n\nTask: _{escape(task)}_",
        [{"id": "classify", "label": "Classifying request...", "status": "running"}],
    )

    try:
        builder = ProjectBuild(uid, task, notify=notify)
        result = await builder.run()
    except Exception as e:
        await progress_msg.edit_text(f"❌ Build failed: {escape(str(e))}")
        return

    await _render_completion(update, progress_msg, result, task)


async def _render_completion(update, progress_msg, result: dict, task: str) -> None:
    """Render final completion message (or pass-through for non-code)."""
    if result.get("request_type") and result.get("request_type") != "code_build":
        msg = (
            f"💡 **Answer:**\n\n{escape(result.get('summary', ''))}"
        )
        await reply_long(update, msg)
        return

    if not result.get("passed", False):
        await progress_msg.edit_text(
            f"❌ **Build failed**\n\n{escape(result.get('summary', 'unknown error'))}"
        )
        return

    project_files = result.get("files", [])
    files_block = "\n".join(f"• {escape(f)}" for f in project_files[:15]) if project_files else ""
    summary = result.get("summary", "")[:800]
    wf = persistence.workflows.get(update.effective_user.id)
    workspace = wf.workspace_dir if wf else ""

    msg = (
        f"🎉 **Build Complete!**\n\n"
        f"**Task:** {escape(task)}\n"
        f"**Files:**\n{files_block}\n\n"
        f"**Summary:** {escape(summary)}\n\n"
        f"📁 `{escape(workspace)}`"
    )
    await reply_long(update, msg)


async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    wf = persistence.workflows.get(uid)
    if not wf:
        await update.message.reply_text("No workflow found. Use /build to start one.")
        return

    stage_labels = {
        "planning": "🧠 Planning",
        "prd": "📄 Creating PRD",
        "coding": "💻 Coding + Tests",
        "verify": "🔍 Verifying",
        "docs": "📝 Documentation",
        "completed": "✅ Completed",
        "cancelled": "❌ Cancelled",
    }

    history = ""
    if wf.action_history:
        history = "\n".join(f"• {h['action']}" for h in wf.action_history[-10:])

    artifact_list = ""
    if wf.artifacts:
        artifact_list = "\n".join(f"• {escape(a.name)} ({a.agent})" for a in wf.artifacts)

    msg = (
        f"📊 **Workflow Status**\n\n"
        f"**Task:** {escape(wf.task)}\n"
        f"**Type:** `{wf.request_type or 'code_build'}`\n"
        f"**Project:** `{wf.project_name or wf.id}`\n"
        f"**Status:** `{wf.status}`\n"
        f"**Stage:** {stage_labels.get(wf.current_stage.value, wf.current_stage.value)}\n"
        f"**Fix cycles:** {wf.fix_count}\n\n"
    )

    if wf.tech_stack:
        ts = wf.tech_stack
        msg += f"**Tech Stack:** {ts.get('language', '?')} ({ts.get('framework', 'none')})\n"
        if ts.get("test_cmd"):
            msg += f"**Test cmd:** `{escape(ts['test_cmd'])}`\n"
        msg += "\n"

    if wf.last_test_results:
        tr = wf.last_test_results
        passed = "✅ pass" if tr.get("passed") else "❌ fail"
        msg += f"**Last test:** {passed} — {escape(tr.get('summary', '')[:200])}\n\n"

    if wf.plan and wf.plan.steps:
        todo_lines = []
        for s in wf.plan.steps:
            icon = STATUS_ICON.get(s.status, "⬜")
            todo_lines.append(f"{icon} {escape(s.label)}")
        msg += "**Steps:**\n" + "\n".join(todo_lines) + "\n\n"

    if history:
        msg += f"**Actions:**\n{history}\n\n"
    if artifact_list:
        msg += f"**Artifacts:**\n{artifact_list}\n\n"
    msg += f"📁 `{escape(wf.workspace_dir)}`"

    await reply_long(update, msg)


async def cancel_workflow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    wf = persistence.workflows.get(uid)
    if not wf or wf.status != "active":
        await update.message.reply_text("No active workflow to cancel.")
        return

    engine.cancel_workflow(wf)
    await persistence.save_async(config.DATA_FILE)
    await update.message.reply_text("❌ Workflow cancelled.")


async def show_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    status = usage_manager.get_status(uid)
    lines = [
        "📊 **Agent Usage Status**",
        "_(resets every hour)_\n",
    ]
    for agent, s in status.items():
        icon = "✅" if s["available"] else ("💀" if s["dead"] else "⚠️")
        lines.append(
            f"{icon} **{agent}**\n"
            f"   └ Requests: {s['requests']}/{s['limit_rph']} per hour "
            f"({s['remaining']} remaining)\n"
            f"   └ Total: {s['total_requests']} ok, {s['total_errors']} errors"
            f"{' (💀 exhausted)' if s['dead'] else ''}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
