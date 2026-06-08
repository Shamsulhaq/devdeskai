import asyncio
import time
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, persistence
from bot.agents import get_available
from bot.ollama import PERSONAS, reply_long

# and repeated round-trips on /models.
_model_list_cache: tuple[float, list[str]] | None = None
_MODEL_LIST_TTL = 10.0


async def _get_model_names() -> list[str]:
    global _model_list_cache
    now = time.monotonic()
    if _model_list_cache and now - _model_list_cache[0] < _MODEL_LIST_TTL:
        return _model_list_cache[1]
    from bot.ollama import get_client
    # ollama client .list() is sync; offload to a thread.
    data = await asyncio.to_thread(lambda: get_client().list())
    names = [m["name"] for m in data.get("models", [])]
    _model_list_cache = (now, names)
    return names


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    in_agent = persistence.user_agent.get(user_id)
    if in_agent:
        info = get_available().get(in_agent, {})
        await update.message.reply_text(
            f"You are in {info.get('label', in_agent)} mode.\n"
            "Type /exit to return."
        )
        return

    agents = get_available()
    agent_list = "\n".join(
        f"  /{n} - {i['label']}" + (" ✅" if i["available"] else " ❌")
        for n, i in agents.items()
    )
    persona_list = ", ".join(f"`{p}`" for p in PERSONAS)
    custom_list = ", ".join(f"`{n}`" for n in config.CUSTOM_COMMANDS)

    text = (
        f"Hello! I'm DevDeskAI, powered by "
        f"{persistence.user_models.get(user_id, config.MODEL)} on Ollama.\n\n"
        f"AI Agents:\n{agent_list}\n\n"
        f"Commands:\n"
        f"/reset - Clear history\n"
        f"/model - Show model\n"
        f"/models - List models\n"
        f"/switch &lt;model&gt; - Switch model\n"
        f"/temp &lt;0-2&gt; - Set temperature\n"
        f"/persona [name] - Switch persona ({persona_list})\n"
        f"/prompt [text] - Custom system prompt\n"
        f"/resetprompt - Reset prompt\n"
        f"/search &lt;query&gt; - Web search\n"
        f"/ask &lt;question&gt; - Ask about uploaded doc\n"
        f"/export - Export chat\n"
        f"/stats - Usage stats\n"
    )
    if custom_list:
        text += f"\nCustom commands: {custom_list}\n"
    text += "\nSend me a message, photo, voice, or document!"
    await update.message.reply_text(text)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in persistence.chat_histories:
        del persistence.chat_histories[user_id]
        persistence.save(config.DATA_FILE)
    await update.message.reply_text("History cleared.")


async def show_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    model = persistence.user_models.get(user_id, config.MODEL)
    temp = persistence.user_temps.get(user_id)
    extra = f", temperature: {temp}" if temp is not None else ""
    await update.message.reply_text(f"Model: {model}{extra}")


async def list_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        names = await _get_model_names()
        if not names:
            await update.message.reply_text("No models found.")
            return
        text = "Available models:\n" + "\n".join(f"- {m}" for m in names)
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def switch_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not context.args:
        current = persistence.user_models.get(user_id, config.MODEL)
        await update.message.reply_text(
            f"Usage: /switch &lt;model&gt;\nCurrent: {current}"
        )
        return
    name = context.args[0]
    try:
        names = await _get_model_names()
        available = set(names)
        if name not in available:
            await update.message.reply_text(
                f"Model '{name}' not found. Use /models."
            )
            return
        persistence.user_models[user_id] = name
        persistence.save(config.DATA_FILE)
        await update.message.reply_text(f"Switched to {name}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not context.args:
        cur = persistence.user_temps.get(user_id)
        if cur is not None:
            await update.message.reply_text(f"Temperature: {cur}")
        else:
            await update.message.reply_text(
                "Temperature not set (model default)."
            )
        return
    try:
        val = float(context.args[0])
        if val < 0 or val > 2:
            await update.message.reply_text(
                "Temperature must be between 0 and 2."
            )
            return
        persistence.user_temps[user_id] = val
        persistence.save(config.DATA_FILE)
        await update.message.reply_text(f"Temperature set to {val}")
    except ValueError:
        await update.message.reply_text("Usage: /temp &lt;0-2&gt;")


async def set_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    persistence.user_personas.pop(user_id, None)
    if not context.args:
        cur = persistence.user_prompts.get(user_id, config.SYSTEM_PROMPT)
        await update.message.reply_text(
            f"Current system prompt:\n{escape(cur)}"
        )
        return
    prompt = " ".join(context.args)
    persistence.user_prompts[user_id] = prompt
    persistence.save(config.DATA_FILE)
    await update.message.reply_text("System prompt updated!")


async def reset_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    for d in (persistence.user_prompts, persistence.user_personas):
        d.pop(user_id, None)
    persistence.save(config.DATA_FILE)
    await update.message.reply_text("Prompt reset to default.")


async def set_persona(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not context.args:
        cur = persistence.user_personas.get(user_id)
        items = ", ".join(f"`{p}`" for p in PERSONAS)
        msg = f"Personas: {items}\nCurrent: {cur}" if cur else f"Personas: {items}\nCurrent: default"
        await update.message.reply_text(msg)
        return
    name = context.args[0].lower()
    if name not in PERSONAS:
        await update.message.reply_text(
            f"Unknown persona '{name}'. Options: {', '.join(PERSONAS)}"
        )
        return
    persistence.user_personas[user_id] = name
    persistence.user_prompts.pop(user_id, None)
    persistence.save(config.DATA_FILE)
    await update.message.reply_text(f"Switched to {name} persona.")
