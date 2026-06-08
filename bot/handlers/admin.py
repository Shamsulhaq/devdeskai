import asyncio
import logging

from telegram import Update
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.ext import ContextTypes

from bot import config, persistence

logger = logging.getLogger(__name__)


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"DevDeskAI Stats\n"
        f"Messages: {persistence.stats['total_messages']}\n"
        f"Users: {len(persistence.stats['user_ids'])}"
    )


async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    # fail closed when ADMIN_IDS is empty.
    if not config.ADMIN_IDS:
        await update.message.reply_text("Admin not configured.")
        return
    if uid not in config.ADMIN_IDS:
        await update.message.reply_text("Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /announce &lt;msg&gt;")
        return
    msg = " ".join(context.args)
    sent = 0
    failed = 0
    dropped: list[int] = []
    for uid2 in list(persistence.stats.get("user_ids", [])):
        try:
            await context.bot.send_message(
                chat_id=uid2, text=f"Announcement:\n{msg}"
            )
            sent += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await context.bot.send_message(
                    chat_id=uid2, text=f"Announcement:\n{msg}"
                )
                sent += 1
            except Exception:
                failed += 1
        except Forbidden:
            dropped.append(uid2)
        except BadRequest:
            failed += 1
        except Exception:
            failed += 1
        # respect Telegram global flood limit (~30 msg/s).
        await asyncio.sleep(0.05)
    await update.message.reply_text(
        f"Sent to {sent} users. Failed: {failed}. Dropped: {len(dropped)}."
    )
