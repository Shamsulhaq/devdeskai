from html import escape

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import config, persistence
from bot.ollama import OllamaError, generate, reply_long


async def handle_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    # guard against None text (commands sent as captions / edited messages).
    text = update.message.text or ""
    if not text:
        return
    # strip @<botname> from `/cmd@MyBotName ...` group-chat form.
    first_token = text.split()[0]
    if "@" in first_token:
        first_token = first_token.split("@", 1)[0]
    cmd = first_token.lstrip("/").lower()
    prompt_text = config.CUSTOM_COMMANDS.get(cmd, "")
    user_msg = " ".join(context.args) if context.args else ""
    full_prompt = f"{prompt_text}\n\n{user_msg}" if user_msg else prompt_text

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )
    try:
        reply = await generate(uid, full_prompt)
    except OllamaError:
        # do not persist on backend failure.
        await update.message.reply_text("AI is unreachable, please try again.")
        return
    persistence.track_user(uid)
    persistence.chat_histories[uid].append(
        {"user": f"[{cmd}] {user_msg}", "assistant": reply}
    )
    await persistence.save_async(config.DATA_FILE)
    await reply_long(update, escape(reply))
