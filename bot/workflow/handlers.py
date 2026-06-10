from html import escape

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import config, persistence
from bot.ollama import reply_long
from bot.workflow import engine
from bot.workflow.usage import usage_manager

STATUS_ICON = {
    "pending": "⬜",
    "in_progress": "⏳",
    "completed": "✅",
    "failed": "❌",
}


def _render_todo(todo: list[dict], header: str) -> str:
    lines = [header, ""]
    for item in todo:
        icon = STATUS_ICON.get(item["status"], "⬜")
        label = escape(item["label"])
        lines.append(f"{icon} {label}")
    return "\n".join(lines)


async def start_build(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    task = " ".join(context.args) if context.args else ""

    if not task:
        await update.message.reply_text(
            "Usage: /build <project description>\n"
            "Example: /build develop a personal website for a software engineer"
        )
        return

    existing = persistence.workflows.get(uid)
    if existing and existing.status == "active":
        engine.cancel_workflow(existing)
        await persistence.save_async(config.DATA_FILE)

    wf = await engine.create_workflow(uid, task)

    # Track the progress message so we can edit it
    progress_msg = await update.message.reply_text("🧠 Brain engaged...")

    async def notify(header: str, todo: list[dict]) -> None:
        text = _render_todo(todo, header)
        try:
            await progress_msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            pass

    await notify(
        f"🧠 **Brain engaged!**\n\nTask: _{escape(task)}_",
        [dict(t, status="pending") for t in engine.TODO_PLAN],
    )

    try:
        result = await engine.run_brain_build(wf, notify=notify)
    except Exception as e:
        wf.status = "cancelled"
        await persistence.save_async(config.DATA_FILE)
        await progress_msg.edit_text(f"❌ Build failed: {escape(str(e))}")
        return

    if "error" in result:
        await progress_msg.edit_text(f"❌ {escape(result['error'])}")
        return

    project_files = result.get("files", [])
    files_block = "\n".join(f"• {escape(f)}" for f in project_files[:15]) if project_files else ""
    summary = result.get("summary", "")[:800]

    msg = (
        f"🎉 **Build Complete!**\n\n"
        f"**Task:** {escape(wf.task)}\n"
        f"**Files:**\n{files_block}\n\n"
        f"**Summary:**\n{escape(summary)}\n\n"
        f"📁 `{escape(wf.workspace_dir)}`"
    )
    await reply_long(update, msg)


async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    wf = persistence.workflows.get(uid)
    if not wf:
        await update.message.reply_text("No workflow found. Use /build to start one.")
        return

    stage_labels = {
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
        f"**Status:** `{wf.status}`\n"
        f"**Stage:** {stage_labels.get(wf.current_stage.value, wf.current_stage.value)}\n"
        f"**Fix cycles:** {wf.fix_count}\n\n"
    )
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
        rem_bar = "█" * (s["remaining"] * 20 // max(s["limit_rph"], 1)) if s["limit_rph"] else ""
        lines.append(
            f"{icon} **{agent}**\n"
            f"   └ Requests: {s['requests']}/{s['limit_rph']} per hour "
            f"({s['remaining']} remaining)\n"
            f"   └ Total: {s['total_requests']} ok, {s['total_errors']} errors"
            f"{' (💀 exhausted)' if s['dead'] else ''}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
