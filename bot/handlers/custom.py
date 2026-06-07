from html import escape

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import config, persistence
from bot.ollama import generate, reply_long


async def handle_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    cmd = update.message.text.split()[0].lstrip("/").lower()
    prompt_text = config.CUSTOM_COMMANDS.get(cmd, "")
    user_msg = " ".join(context.args) if context.args else ""
    full_prompt = f"{prompt_text}\n\n{user_msg}" if user_msg else prompt_text

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )
    try:
        reply = await generate(uid, full_prompt)
        persistence.track_user(uid)
        persistence.chat_histories[uid].append(
            {"user": f"[{cmd}] {user_msg}", "assistant": reply}
        )
        persistence.save(config.DATA_FILE)
        await reply_long(update, escape(reply))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
