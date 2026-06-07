import io
import os
import base64
import logging
import time
from html import escape
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import config, persistence
from bot.ollama import generate, generate_and_reply, reply_long

logger = logging.getLogger(__name__)

WHISPER_AVAILABLE = False
_whisper_model = None
_whisper_is_faster = False

try:
    from faster_whisper import WhisperModel as _WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    try:
        import whisper as _whisper
        WHISPER_AVAILABLE = True
    except ImportError:
        pass


def _get_whisper():
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id

    # Agent mode
    agent_name = persistence.user_agent.get(uid)
    if agent_name:
        text = update.message.text or update.message.caption or ""
        if not text:
            return
        from bot import agents as agent_system
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action=ChatAction.TYPING
        )
        workspace = os.path.join(config.WORKSPACE_DIR, str(uid))
        os.makedirs(workspace, exist_ok=True)
        out = await agent_system.run_cli(agent_name, text, workspace)
        persistence.user_agent_history[uid].append(
            {"prompt": text, "output": out}
        )
        persistence.save(config.DATA_FILE)
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

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )
    try:
        await generate_and_reply(update, uid, user_text, image_data)
    except Exception as e:
        logger.error("Chat error: %s", e)
        await update.message.reply_text("Error. Is Ollama running?")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    wh, is_faster = _get_whisper()
    if wh is None:
        await update.message.reply_text(
            "Voice transcription not available. Install:\n"
            "`pip install faster-whisper`  (recommended, lightweight)\n"
            "or: `pip install openai-whisper`"
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )
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

        await update.message.reply_text(f"Transcribed: _{text}_")
        reply = await generate(uid, text)
        persistence.chat_histories[uid].append(
            {"user": f"[voice] {text}", "assistant": reply}
        )
        persistence.track_user(uid)
        persistence.save(config.DATA_FILE)
        await reply_long(update, escape(reply))
    except Exception as e:
        logger.error("Voice error: %s", e)
        await update.message.reply_text(f"Voice processing error: {e}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    doc = update.message.document
    if not doc:
        return

    file_name = doc.file_name or ""
    ext = Path(file_name).suffix.lower()
    if ext not in (".txt", ".pdf", ".md", ".csv", ".json"):
        await update.message.reply_text(
            "Supported formats: .txt, .pdf, .md, .csv, .json"
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )
    try:
        file = await doc.get_file()
        raw = io.BytesIO()
        await file.download_to_memory(raw)
        raw.seek(0)

        text = ""
        if ext == ".pdf":
            try:
                import fitz
            except ImportError:
                await update.message.reply_text(
                    "PDF support requires PyMuPDF: `pip install PyMuPDF`"
                )
                return
            pdf_doc = fitz.open(stream=raw.read(), filetype="pdf")
            for page in pdf_doc:
                text += page.get_text() + "\n"
            pdf_doc.close()
        else:
            text = raw.read().decode("utf-8", errors="replace")

        if not text.strip():
            await update.message.reply_text(
                "No text could be extracted from the document."
            )
            return

        persistence.user_docs[uid] = text
        persistence.save(config.DATA_FILE)
        await update.message.reply_text(
            f"Document saved ({len(text)} chars). "
            "Use /ask &lt;question&gt; to query it."
        )
    except Exception as e:
        await update.message.reply_text(f"Document error: {e}")


async def handle_group_mention(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not config.BOT_USERNAME:
        return
    uid = update.effective_user.id
    text = update.message.text or ""
    cleaned = text.replace(f"@{config.BOT_USERNAME}", "").strip()
    if not cleaned:
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )
    try:
        await generate_and_reply(update, uid, cleaned)
    except Exception as e:
        logger.error("Group error: %s", e)
        await update.message.reply_text("Error.")
