import os
import io
import json
import logging
import asyncio
import base64
import shutil
import time
from collections import defaultdict
from html import escape
from pathlib import Path

from dotenv import load_dotenv
import ollama
from telegram import Update, InputFile
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# === OPTIONAL FEATURES ===

WHISPER_AVAILABLE = False
DDGS_AVAILABLE = False
PYMUPDF_AVAILABLE = False

try:
    from faster_whisper import WhisperModel as _WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    try:
        import whisper as _whisper
        WHISPER_AVAILABLE = True
    except ImportError:
        pass

try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    pass

try:
    import fitz
    PYMUPDF_AVAILABLE = True
except ImportError:
    pass
_whisper_model = None
_whisper_is_faster = False

def get_whisper():
    global _whisper_model, _whisper_is_faster
    if _whisper_model is not None:
        return _whisper_model, _whisper_is_faster
    if not WHISPER_AVAILABLE:
        return None, False
    try:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("base", device="auto")
        _whisper_is_faster = True
    except ImportError:
        import whisper
        _whisper_model = whisper.load_model("base")
        _whisper_is_faster = False
    return _whisper_model, _whisper_is_faster

# === CONFIG ===

BOT_TOKEN = os.getenv("BOT_TOKEN")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.getenv("MODEL", "gemma4:e4b")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are DevDeskAI, an AI assistant. Respond conversationally and concisely.",
)
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
ADMIN_IDS = set()
admin_str = os.getenv("ADMIN_IDS", "")
if admin_str:
    ADMIN_IDS = {int(x.strip()) for x in admin_str.split(",") if x.strip()}
DATA_FILE = "bot_data.json"
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", os.path.join(os.getcwd(), "workspace"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in .env file")

client = ollama.Client(host=OLLAMA_HOST)

# === PERSISTENT DATA ===

chat_histories: dict[int, list[dict]] = defaultdict(list)
user_prompts: dict[int, str] = {}
user_models: dict[int, str] = {}
user_temps: dict[int, float] = {}
user_agent: dict[int, str] = {}
user_agent_history: dict[int, list[dict]] = defaultdict(list)
user_docs: dict[int, str] = {}
user_personas: dict[int, str] = {}
stats: dict = {"total_messages": 0, "user_ids": []}

# === PERSONAS ===

PERSONAS = {
    "default": SYSTEM_PROMPT,
    "coder": "You are an expert software engineer. Write clean, efficient, well-documented code. Think step by step and explain your reasoning.",
    "poet": "You are a poet and creative writer. Respond with rich imagery, emotion, and literary flair.",
    "friendly": "You are a warm, friendly companion. Be supportive, encouraging, and casual.",
    "concise": "You are a direct, no-nonsense assistant. Answer in as few words as possible while being accurate.",
    "socratic": "You are a Socratic tutor. Don't give answers directly—guide the user with questions to help them discover the answer themselves.",
    "pirate": "Arr! Ye be talkin' to a pirate AI. Speak like a swashbuckler of the high seas, matey!",
}

# === CUSTOM COMMANDS FROM ENV ===

CUSTOM_COMMANDS = {}
for key, val in os.environ.items():
    if key.startswith("CUSTOM_CMD_") and not key.endswith("_PROMPT"):
        name = key[len("CUSTOM_CMD_"):].lower()
        prompt_key = f"CUSTOM_CMD_{name.upper()}_PROMPT"
        prompt = os.getenv(prompt_key, val)
        CUSTOM_COMMANDS[name] = prompt

# === AGENTS ===

AGENTS = {}

def detect_agents():
    for name, info in AGENTS_CONFIG.items():
        cmd = info["check"].split()[0]
        found = shutil.which(cmd) is not None
        AGENTS[name] = {**info, "available": found}

AGENTS_CONFIG = {
    "claude": {"label": "Claude (Anthropic)", "check": "claude", "run_cmd": "claude -p"},
    "opencode": {"label": "OpenCode", "check": "opencode", "run_cmd": "opencode"},
    "codex": {"label": "Codex (OpenAI)", "check": "codex", "run_cmd": "codex"},
    "qwen": {"label": "Qwen (Alibaba)", "check": "qwen", "run_cmd": "qwen"},
    "gemini": {"label": "Gemini (Google)", "check": "gemini", "run_cmd": "gemini"},
    "copilot": {"label": "GitHub Copilot", "check": "copilot", "run_cmd": "copilot"},
}

# === DATA PERSISTENCE ===

def load_data():
    global chat_histories, user_prompts, user_models, user_temps, user_agent
    global user_agent_history, user_docs, user_personas, stats
    try:
        with open(DATA_FILE) as f:
            data = json.load(f)
        chat_histories = defaultdict(list, {int(k): v for k, v in data.get("histories", {}).items()})
        user_prompts = {int(k): v for k, v in data.get("prompts", {}).items()}
        user_models = {int(k): v for k, v in data.get("models", {}).items()}
        user_temps = {int(k): v for k, v in data.get("temps", {}).items()}
        user_agent = {int(k): v for k, v in data.get("agent", {}).items()}
        user_agent_history = defaultdict(list, {int(k): v for k, v in data.get("agent_history", {}).items()})
        user_docs = {int(k): v for k, v in data.get("docs", {}).items()}
        user_personas = {int(k): v for k, v in data.get("personas", {}).items()}
        stats = data.get("stats", {"total_messages": 0, "user_ids": []})
        logger.info(f"Loaded data from {DATA_FILE}")
    except FileNotFoundError:
        logger.info("No existing data file, starting fresh")

def save_data():
    data = {
        "histories": {str(k): v for k, v in chat_histories.items()},
        "prompts": {str(k): v for k, v in user_prompts.items()},
        "models": {str(k): v for k, v in user_models.items()},
        "temps": {str(k): v for k, v in user_temps.items()},
        "agent": {str(k): v for k, v in user_agent.items()},
        "agent_history": {str(k): v for k, v in user_agent_history.items()},
        "docs": {str(k): v for k, v in user_docs.items()},
        "personas": {str(k): v for k, v in user_personas.items()},
        "stats": stats,
    }
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# === HELPERS ===

def get_system_prompt(user_id: int) -> str:
    persona_key = user_personas.get(user_id)
    if persona_key and persona_key in PERSONAS:
        return PERSONAS[persona_key]
    return user_prompts.get(user_id, SYSTEM_PROMPT)

def get_model_for_user(user_id: int) -> str:
    return user_models.get(user_id, MODEL)

def get_temp_for_user(user_id: int) -> float | None:
    return user_temps.get(user_id)

def get_user_workspace(user_id: int) -> str:
    path = os.path.join(WORKSPACE_DIR, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path

def build_messages(user_id: int, user_message: str, image_data: str | None = None) -> list[dict]:
    history = chat_histories[user_id]
    messages = [{"role": "system", "content": get_system_prompt(user_id)}]
    for entry in history[-MAX_HISTORY:]:
        messages.append({"role": "user", "content": entry["user"]})
        messages.append({"role": "assistant", "content": entry["assistant"]})
    user_msg = {"role": "user", "content": user_message}
    if image_data:
        user_msg["images"] = [image_data]
    messages.append(user_msg)
    return messages

async def reply_long(update_or_msg, text: str):
    MAX_LEN = 4096
    reply = update_or_msg.reply_text if hasattr(update_or_msg, "reply_text") else update_or_msg
    if len(text) <= MAX_LEN:
        await reply(text, parse_mode=ParseMode.HTML)
        return
    parts = []
    remaining = text
    while remaining:
        if len(remaining) <= MAX_LEN:
            parts.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, MAX_LEN)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, MAX_LEN)
        if split_at == -1:
            split_at = MAX_LEN
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:].strip()
    for part in parts:
        await reply(part, parse_mode=ParseMode.HTML)

async def generate(user_id: int, prompt: str, image_data: str | None = None) -> str:
    model = get_model_for_user(user_id)
    messages = build_messages(user_id, prompt, image_data)
    opts = {}
    temp = get_temp_for_user(user_id)
    if temp is not None:
        opts["temperature"] = temp
    stream = client.chat(model=model, messages=messages, stream=True, options=opts or None)
    full = ""
    for chunk in stream:
        content = chunk.get("message", {}).get("content", "") or ""
        full += content
    return full or "No response generated."

def track_user(user_id: int):
    if user_id not in stats["user_ids"]:
        stats["user_ids"].append(user_id)
    stats["total_messages"] += 1
    save_data()

async def run_agent_cli(agent_name: str, prompt: str, workspace: str) -> str:
    info = AGENTS.get(agent_name)
    if not info or not info["available"]:
        return f"Agent '{agent_name}' is not available."
    full_cmd = f'{info["run_cmd"]} "{prompt}"'
    try:
        proc = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        return (out + f"\n\n[stderr]\n{err}") if err else out
    except asyncio.TimeoutError:
        return "Agent timed out (300s)."
    except Exception as e:
        return f"Agent error: {e}"

# === COMMAND HANDLERS ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    in_agent = user_agent.get(user_id)
    if in_agent:
        await update.message.reply_text(
            f"You are in **{AGENTS.get(in_agent, {}).get('label', in_agent)}** mode.\n"
            f"Type /exit to return."
        )
        return
    agent_list = "\n".join(
        f"  /{n} - {i['label']}" + (" ✅" if i["available"] else " ❌")
        for n, i in AGENTS.items()
    )
    persona_list = ", ".join(f"`{p}`" for p in PERSONAS)
    custom_list = ", ".join(f"`{n}`" for n in CUSTOM_COMMANDS)
    await update.message.reply_text(
        f"Hello! I'm **DevDeskAI**, powered by {get_model_for_user(user_id)} on Ollama.\n\n"
        f"**AI Agents:**\n{agent_list}\n\n"
        f"**Commands:**\n"
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
        + (f"\n**Custom commands:** {custom_list}\n" if CUSTOM_COMMANDS else "")
        + "\nSend me a message, photo, voice, or document!"
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in chat_histories:
        del chat_histories[user_id]
        save_data()
    await update.message.reply_text("History cleared.")

async def show_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    t = get_temp_for_user(uid)
    extra = f", temperature: {t}" if t is not None else ""
    await update.message.reply_text(f"Model: **{get_model_for_user(uid)}**{extra}")

async def list_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        data = client.list()
        names = [m["name"] for m in data.get("models", [])]
        if not names:
            await update.message.reply_text("No models found.")
            return
        await update.message.reply_text("**Available models:**\n" + "\n".join(f"- {m}" for m in names))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def switch_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text(f"Usage: /switch &lt;model&gt;\nCurrent: {get_model_for_user(uid)}")
        return
    name = context.args[0]
    try:
        data = client.list()
        avail = {m["name"] for m in data.get("models", [])}
        if name not in avail:
            await update.message.reply_text(f"Model '{name}' not found. Use /models.")
            return
        user_models[uid] = name
        save_data()
        await update.message.reply_text(f"Switched to **{name}**")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def set_temp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not context.args:
        cur = user_temps.get(uid)
        await update.message.reply_text(f"Temperature: **{cur}**" if cur is not None else "Temperature not set (model default).")
        return
    try:
        val = float(context.args[0])
        if val < 0 or val > 2:
            await update.message.reply_text("Temperature must be between 0 and 2.")
            return
        user_temps[uid] = val
        save_data()
        await update.message.reply_text(f"Temperature set to **{val}**")
    except ValueError:
        await update.message.reply_text("Usage: /temp &lt;0-2&gt;")

async def set_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if uid in user_personas:
        del user_personas[uid]
    if not context.args:
        cur = user_prompts.get(uid, SYSTEM_PROMPT)
        await update.message.reply_text(f"Current system prompt:\n{escape(cur)}")
        return
    prompt = " ".join(context.args)
    user_prompts[uid] = prompt
    save_data()
    await update.message.reply_text("System prompt updated!")

async def reset_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    for d in (user_prompts, user_personas):
        d.pop(uid, None)
    save_data()
    await update.message.reply_text("Prompt reset to default.")

async def persona(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not context.args:
        cur = user_personas.get(uid)
        named = f" ({cur})" if cur else ""
        list_s = ", ".join(f"`{p}`" for p in PERSONAS)
        await update.message.reply_text(f"Personas: {list_s}\nCurrent{': **' + cur + '**' if cur else ': default'}")
        return
    name = context.args[0].lower()
    if name not in PERSONAS:
        await update.message.reply_text(f"Unknown persona '{name}'. Options: {', '.join(PERSONAS)}")
        return
    user_personas[uid] = name
    user_prompts.pop(uid, None)
    save_data()
    await update.message.reply_text(f"Switched to **{name}** persona.")

async def web_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not DDGS_AVAILABLE:
        await update.message.reply_text("Web search not available. Install `duckduckgo_search`.")
        return
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /search &lt;query&gt;")
        return
    uid = update.effective_user.id
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            await update.message.reply_text("No results found.")
            return
        ctx = "Search results for query: " + query + "\n\n"
        for r in results:
            ctx += f"- {r.get('title', '')}: {r.get('href', '')}\n  {r.get('body', '')}\n\n"
        ctx += "\nAnswer the user's question based on the above search results."
        reply = await generate(uid, ctx)
        track_user(uid)
        chat_histories[uid].append({"user": f"[web search] {query}", "assistant": reply})
        save_data()
        await reply_long(update, escape(reply))
    except Exception as e:
        await update.message.reply_text(f"Search error: {e}")

async def ask_doc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    doc = user_docs.get(uid)
    if not doc:
        await update.message.reply_text("No document uploaded. Send me a PDF or .txt file first.")
        return
    question = " ".join(context.args) if context.args else ""
    if not question:
        await update.message.reply_text("Usage: /ask &lt;question&gt;")
        return
    # Truncate doc if too long
    MAX_DOC_CHARS = 8000
    truncated = doc[:MAX_DOC_CHARS] + ("\n\n[doc truncated]" if len(doc) > MAX_DOC_CHARS else "")
    prompt = f"Document content:\n{truncated}\n\nQuestion: {question}\n\nAnswer based on the document."
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await generate(uid, prompt)
        track_user(uid)
        chat_histories[uid].append({"user": f"[doc] {question}", "assistant": reply})
        save_data()
        await reply_long(update, escape(reply))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def export_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    history = chat_histories.get(uid, [])
    if not history:
        await update.message.reply_text("No conversation history to export.")
        return
    lines = []
    for entry in history:
        lines.append(f"User: {entry['user']}")
        lines.append(f"Assistant: {entry['assistant']}")
        lines.append("---")
    text = "\n".join(lines)
    buf = io.BytesIO(text.encode("utf-8"))
    buf.name = f"chat_export_{uid}.txt"
    await update.message.reply_document(document=InputFile(buf), caption="Your chat export.")

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"**DevDeskAI Stats**\n"
        f"Messages: {stats['total_messages']}\n"
        f"Users: {len(stats['user_ids'])}"
    )

async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if ADMIN_IDS and uid not in ADMIN_IDS:
        await update.message.reply_text("Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /announce &lt;msg&gt;")
        return
    msg = " ".join(context.args)
    sent = 0
    for uid2 in stats.get("user_ids", []):
        try:
            await context.bot.send_message(chat_id=uid2, text=f"Announcement:\n{msg}")
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"Sent to {sent} users.")

# === AGENT HANDLERS ===

async def enter_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    agent_name = update.message.text.split()[0].lstrip("/").lower()
    info = AGENTS.get(agent_name)
    if not info:
        return
    if not info["available"]:
        await update.message.reply_text(f"**{info['label']}** is not installed.")
        return
    user_agent[uid] = agent_name
    save_data()
    prompt = " ".join(context.args) if context.args else ""
    if prompt:
        await update.message.reply_text(f"**{info['label']}** running...")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        out = await run_agent_cli(agent_name, prompt, get_user_workspace(uid))
        user_agent_history[uid].append({"prompt": prompt, "output": out})
        save_data()
        await reply_long(update, escape(out))
    else:
        await update.message.reply_text(
            f"**{info['label']}** mode. Send messages to forward. /exit to stop."
        )

async def exit_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    agent_name = user_agent.pop(uid, None)
    save_data()
    if agent_name:
        info = AGENTS.get(agent_name, {})
        await update.message.reply_text(f"Exited **{info.get('label', agent_name)}** mode.")
    else:
        await update.message.reply_text("Not in agent mode.")

async def list_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [f"/{n} - {i['label']} {'✅' if i['available'] else '❌'}" for n, i in AGENTS.items()]
    await update.message.reply_text("**Agents:**\n" + "\n".join(lines))

# === CUSTOM COMMAND HANDLER ===

async def handle_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    cmd = update.message.text.split()[0].lstrip("/").lower()
    prompt_text = CUSTOM_COMMANDS.get(cmd, "")
    user_msg = " ".join(context.args) if context.args else ""
    full_prompt = f"{prompt_text}\n\n{user_msg}" if user_msg else prompt_text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await generate(uid, full_prompt)
        track_user(uid)
        chat_histories[uid].append({"user": f"[{cmd}] {user_msg}", "assistant": reply})
        save_data()
        await reply_long(update, escape(reply))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# === MESSAGE HANDLERS ===

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    wh, is_faster = get_whisper()
    if wh is None:
        await update.message.reply_text(
            "Voice transcription not available. Install:\n"
            "`pip install faster-whisper`  (recommended, lightweight)\n"
            "or: `pip install openai-whisper`"
        )
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        file = await update.message.voice.get_file()
        ogg_bytes = io.BytesIO()
        await file.download_to_memory(ogg_bytes)
        ogg_path = f"/tmp/voice_{uid}_{int(time.time())}.ogg"
        with open(ogg_path, "wb") as f:
            f.write(ogg_bytes.getvalue())

        if is_faster:
            segments, _ = wh.transcribe(ogg_path)
            text = " ".join(seg.text for seg in segments)
        else:
            result = wh.transcribe(ogg_path)
            text = result.get("text", "").strip()

        os.remove(ogg_path)
        if not text:
            await update.message.reply_text("Could not transcribe voice.")
            return
        await update.message.reply_text(f"Transcribed: _\u2060{text}_")
        track_user(uid)
        reply = await generate(uid, text)
        chat_histories[uid].append({"user": f"[voice] {text}", "assistant": reply})
        save_data()
        await reply_long(update, escape(reply))
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text(f"Voice processing error: {e}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    doc = update.message.document
    if not doc:
        return
    file_name = doc.file_name or ""
    ext = Path(file_name).suffix.lower()

    if ext not in (".txt", ".pdf", ".md", ".csv", ".json"):
        await update.message.reply_text("Supported formats: .txt, .pdf, .md, .csv, .json")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        file = await doc.get_file()
        raw = io.BytesIO()
        await file.download_to_memory(raw)
        raw.seek(0)

        text = ""
        if ext == ".pdf":
            if not PYMUPDF_AVAILABLE:
                await update.message.reply_text("PDF support requires PyMuPDF: `pip install PyMuPDF`")
                return
            pdf_doc = fitz.open(stream=raw.read(), filetype="pdf")
            for page in pdf_doc:
                text += page.get_text() + "\n"
            pdf_doc.close()
        else:
            text = raw.read().decode("utf-8", errors="replace")

        if not text.strip():
            await update.message.reply_text("No text could be extracted from the document.")
            return

        user_docs[uid] = text
        save_data()
        await update.message.reply_text(
            f"Document saved ({len(text)} chars). Use /ask &lt;question&gt; to query it."
        )
    except Exception as e:
        await update.message.reply_text(f"Document error: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id

    # Agent mode
    agent_name = user_agent.get(uid)
    if agent_name:
        text = update.message.text or update.message.caption or ""
        if not text:
            return
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        out = await run_agent_cli(agent_name, text, get_user_workspace(uid))
        user_agent_history[uid].append({"prompt": text, "output": out})
        save_data()
        await reply_long(update, escape(out))
        return

    # Normal chat
    user_text = update.message.text or update.message.caption or ""
    image_data = None

    if update.message.photo:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        img_bytes = await file.download_as_bytearray()
        image_data = base64.b64encode(img_bytes).decode("utf-8")
        if not user_text:
            user_text = "Describe this image"

    if not user_text and not image_data:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        track_user(uid)
        reply = await generate(uid, user_text, image_data)
        chat_histories[uid].append({"user": user_text, "assistant": reply})
        save_data()
        await reply_long(update, escape(reply))
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("Error. Is Ollama running?")

async def handle_group_mention(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not BOT_USERNAME:
        return
    uid = update.effective_user.id
    text = update.message.text or ""
    # Remove @mention
    cleaned = text.replace(f"@{BOT_USERNAME}", "").strip()
    if not cleaned:
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        track_user(uid)
        reply = await generate(uid, cleaned)
        chat_histories[uid].append({"user": cleaned, "assistant": reply})
        save_data()
        await reply_long(update, escape(reply))
    except Exception as e:
        logger.error(f"Group error: {e}")
        await update.message.reply_text("Error.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Update {update} caused error {context.error}")

# === MAIN ===

def main() -> None:
    load_data()
    detect_agents()

    app = Application.builder().token(BOT_TOKEN).build()

    # Core
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("model", show_model))
    app.add_handler(CommandHandler("models", list_models))
    app.add_handler(CommandHandler("switch", switch_model))
    app.add_handler(CommandHandler("temp", set_temp))
    app.add_handler(CommandHandler("prompt", set_prompt))
    app.add_handler(CommandHandler("resetprompt", reset_prompt))
    app.add_handler(CommandHandler("persona", persona))
    app.add_handler(CommandHandler("search", web_search))
    app.add_handler(CommandHandler("ask", ask_doc))
    app.add_handler(CommandHandler("export", export_chat))
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(CommandHandler("announce", announce))

    # Agents
    app.add_handler(CommandHandler("agents", list_agents))
    app.add_handler(CommandHandler("exit", exit_agent))
    app.add_handler(CommandHandler("back", exit_agent))
    for name in AGENTS_CONFIG:
        app.add_handler(CommandHandler(name, enter_agent))

    # Custom commands
    for name in CUSTOM_COMMANDS:
        app.add_handler(CommandHandler(name, handle_custom))

    # Message handlers (order matters)
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Group mention handler
    if BOT_USERNAME:
        app.add_handler(
            MessageHandler(
                filters.TEXT & filters.ChatType.GROUPS & filters.Entity("mention"),
                handle_group_mention,
            )
        )

    # Text and photo (must be after more specific handlers)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    app.add_error_handler(error_handler)

    # Webhook or polling
    if WEBHOOK_URL:
        logger.info(f"Starting webhook on port {WEBHOOK_PORT}")
        app.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
            secret_token=WEBHOOK_SECRET or None,
        )
    else:
        logger.info(f"Starting polling with model: {MODEL}")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
