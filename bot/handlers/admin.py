from telegram import Update
from telegram.ext import ContextTypes

from bot import config, persistence


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"**DevDeskAI Stats**\n"
        f"Messages: {persistence.stats['total_messages']}\n"
        f"Users: {len(persistence.stats['user_ids'])}"
    )


async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if config.ADMIN_IDS and uid not in config.ADMIN_IDS:
        await update.message.reply_text("Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /announce &lt;msg&gt;")
        return
    msg = " ".join(context.args)
    sent = 0
    for uid2 in persistence.stats.get("user_ids", []):
        try:
            await context.bot.send_message(
                chat_id=uid2, text=f"Announcement:\n{msg}"
            )
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"Sent to {sent} users.")
