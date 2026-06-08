import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot import config, persistence
from bot import agents as agent_system
from bot.handlers import (
    core,
    productivity,
    admin,
    agents,
    media,
    custom,
)
from bot.handlers.media import load_whisper_async

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # log only update_id (full Update contains PII).
    update_id = update.update_id if isinstance(update, Update) else "non-update"
    if context.error is not None:
        logger.exception(
            "Update %s caused error %s", update_id, context.error,
            exc_info=context.error,
        )
    else:
        logger.error("Update %s caused error (no exception attached)", update_id)


async def _post_init(application: Application) -> None:
    """BUG-004 fix: eager-load heavy resources on startup, not on first use."""
    await load_whisper_async()


def main() -> None:
    persistence.load(config.DATA_FILE)
    # warn loudly when no admins are configured.
    if not config.ADMIN_IDS:
        logger.warning(
            "ADMIN_IDS is empty; /announce will refuse requests. "
            "Set ADMIN_IDS to enable admin commands."
        )
    agent_system.detect()

    app = Application.builder().token(config.BOT_TOKEN).post_init(_post_init).build()

    # Core commands
    app.add_handler(CommandHandler("start", core.start))
    app.add_handler(CommandHandler("reset", core.reset))
    app.add_handler(CommandHandler("model", core.show_model))
    app.add_handler(CommandHandler("models", core.list_models))
    app.add_handler(CommandHandler("switch", core.switch_model))
    app.add_handler(CommandHandler("temp", core.set_temperature))
    app.add_handler(CommandHandler("prompt", core.set_prompt))
    app.add_handler(CommandHandler("resetprompt", core.reset_prompt))
    app.add_handler(CommandHandler("persona", core.set_persona))

    # Productivity commands
    app.add_handler(CommandHandler("search", productivity.web_search))
    app.add_handler(CommandHandler("ask", productivity.ask_document))
    app.add_handler(CommandHandler("export", productivity.export_chat))

    # Admin commands
    app.add_handler(CommandHandler("stats", admin.show_stats))
    app.add_handler(CommandHandler("announce", admin.announce))

    # Agent commands
    app.add_handler(CommandHandler("agents", agents.list_agents))
    app.add_handler(CommandHandler("exit", agents.exit_agent))
    app.add_handler(CommandHandler("back", agents.exit_agent))
    for name in agent_system.AGENTS_CONFIG:
        app.add_handler(CommandHandler(name, agents.enter_agent))

    # Custom commands from env
    for name in config.CUSTOM_COMMANDS:
        app.add_handler(CommandHandler(name, custom.handle_custom))

    # Message handlers (order matters)
    app.add_handler(MessageHandler(filters.VOICE, media.handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, media.handle_document))

    # Group mention
    if config.BOT_USERNAME:
        app.add_handler(
            MessageHandler(
                filters.TEXT
                & filters.ChatType.GROUPS
                & filters.Entity("mention"),
                media.handle_group_mention,
            )
        )

    # Text and photo
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, media.handle_message)
    )
    app.add_handler(MessageHandler(filters.PHOTO, media.handle_message))
    app.add_error_handler(error_handler)

    # Webhook or polling
    if config.WEBHOOK_URL:
        logger.info("Starting webhook on port %s", config.WEBHOOK_PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=config.WEBHOOK_PORT,
            url_path=config.BOT_TOKEN,
            webhook_url=f"{config.WEBHOOK_URL}/{config.BOT_TOKEN}",
            secret_token=config.WEBHOOK_SECRET or None,
        )
    else:
        logger.info("Starting polling with model: %s", config.MODEL)
        app.run_polling(allowed_updates=Update.ALL_TYPES)
