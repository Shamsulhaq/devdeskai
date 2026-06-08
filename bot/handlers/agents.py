import os
from html import escape

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import config, persistence
from bot import agents as agent_system
from bot.ollama import reply_long


async def list_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agents = agent_system.get_available()
    lines = [
        f"/{n} - {i['label']} {'✅' if i['available'] else '❌'}"
        for n, i in agents.items()
    ]
    await update.message.reply_text("Agents:\n" + "\n".join(lines))


async def enter_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    # strip @<botname> from first token (group chat form
    # `/claude@MyBotName some prompt`) before treating it as agent name.
    first_token = update.message.text.split()[0]
    if "@" in first_token:
        first_token = first_token.split("@", 1)[0]
    agent_name = first_token.lstrip("/").lower()
    info = agent_system.get_available().get(agent_name)
    if not info:
        return
    if not info["available"]:
        await update.message.reply_text(
            f"{info['label']} is not installed."
        )
        return

    persistence.user_agent[uid] = agent_name
    persistence.save(config.DATA_FILE)

    prompt = " ".join(context.args) if context.args else ""
    if prompt:
        await update.message.reply_text(
            f"{info['label']} running..."
        )
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action=ChatAction.TYPING
        )
        workspace = os.path.join(config.WORKSPACE_DIR, str(uid))
        os.makedirs(workspace, exist_ok=True)
        out = await agent_system.run_cli(agent_name, prompt, workspace)
        persistence.user_agent_history[uid].append(
            {"prompt": prompt, "output": out}
        )
        persistence.save(config.DATA_FILE)
        await reply_long(update, escape(out))
    else:
        await update.message.reply_text(
            f"{info['label']} mode. Send messages to forward. "
            "/exit to stop."
        )


async def exit_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    # skip the save() when the user wasn't in agent mode;
    # otherwise pop is a no-op but we still rewrite the entire JSON file.
    if uid not in persistence.user_agent:
        await update.message.reply_text("Not in agent mode.")
        return
    agent_name = persistence.user_agent.pop(uid, None)
    persistence.save(config.DATA_FILE)
    info = agent_system.get_available().get(agent_name, {})
    await update.message.reply_text(
        f"Exited {info.get('label', agent_name)} mode."
    )
