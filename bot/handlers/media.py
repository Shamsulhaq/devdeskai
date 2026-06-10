import asyncio
import base64
import io
import logging
import tempfile
from html import escape
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import config, persistence
from bot.ollama import OllamaError, generate, generate_and_reply, reply_long

logger = logging.getLogger(__name__)

MAX_DOC_BYTES = 20 * 1024 * 1024  # 20MB
MAX_DOC_CHARS_STORED = 50_000

# Telegram sends 3+ sizes; index 1 is ~160px, 2 is ~320px, 3 is ~800px, -1 is original
PREFERRED_PHOTO_INDEX = 2  # ~800px is enough for vision models

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


async def load_whisper_async() -> None:
    """Eager-load Whisper model in a thread (BUG-004 fix).

    Call at startup so the first voice message doesn't block the event loop.
    """
    global _whisper_model, _whisper_is_faster
    if _whisper_model is not None or not WHISPER_AVAILABLE:
        return
    try:
        from faster_whisper import WhisperModel
        _whisper_model = await asyncio.to_thread(
            WhisperModel, "base", device="auto"
        )
        _whisper_is_faster = True
    except ImportError:
        import whisper
        _whisper_model = await asyncio.to_thread(whisper.load_model, "base")
        _whisper_is_faster = False
    logger.info("Whisper model loaded (faster=%s)", _whisper_is_faster)


def _get_whisper_sync():
    """Lazy fallback if not preloaded."""
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


async def _get_whisper():
    """Async-safe getter: loads in thread if not yet loaded."""
    if _whisper_model is not None:
        return _whisper_model, _whisper_is_faster
    if not WHISPER_AVAILABLE:
        return None, False
    await load_whisper_async()
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
        workspace = _safe_workspace(uid)
        out = await agent_system.run_cli(agent_name, text, workspace)
        persistence.user_agent_history[uid].append(
            {"prompt": text, "output": out}
        )
        await persistence.save_async(config.DATA_FILE)
        await reply_long(update, escape(out))
        return

    # Normal chat
    user_text = update.message.text or update.message.caption or ""
    image_data = None

    if update.message.photo:
        # use a smaller photo size, not the full-resolution one
        photos = update.message.photo
        photo = photos[min(PREFERRED_PHOTO_INDEX, len(photos) - 1)]
        file = await photo.get_file()
        img_bytes = await file.download_as_bytearray()
        # base64-encode multi-MB photo off the loop thread
        encoded = await asyncio.to_thread(base64.b64encode, bytes(img_bytes))
        image_data = encoded.decode("utf-8")
        if not user_text:
            user_text = "Describe this image"

    if not user_text and not image_data:
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )
    try:
        await generate_and_reply(update, uid, user_text, image_data)
    except Exception:
        # log full exception, send generic reply
        logger.exception("chat handler failed")
        await update.message.reply_text("Error. Is Ollama running?")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    wh, is_faster = await _get_whisper()
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
    # use NamedTemporaryFile, clean up in finally
    ogg_path = None
    try:
        file = await update.message.voice.get_file()
        # download straight to disk, skip in-memory copy
        with tempfile.NamedTemporaryFile(
            suffix=".ogg", delete=False
        ) as tf:
            ogg_path = tf.name
        await file.download_to_drive(custom_path=ogg_path)

        def _transcribe():
            if is_faster:
                segments, _ = wh.transcribe(ogg_path)
                return " ".join(seg.text for seg in segments)
            else:
                result = wh.transcribe(ogg_path)
                return result.get("text", "").strip()

        # Run sync Whisper in a thread so it doesn't block the loop
        text = await asyncio.to_thread(_transcribe)

        if not text:
            await update.message.reply_text("Could not transcribe voice.")
            return

        await update.message.reply_text(f"Transcribed: _{text}_")
        try:
            reply = await generate(uid, text)
        except OllamaError:
            # do not persist on backend failure.
            await update.message.reply_text("AI is unreachable, please try again.")
            return
        persistence.chat_histories[uid].append(
            {"user": f"[voice] {text}", "assistant": reply}
        )
        persistence.track_user(uid)
        await persistence.save_async(config.DATA_FILE)
        await reply_long(update, escape(reply))
    except Exception:
        # log full exception, send generic reply
        logger.exception("voice handler failed")
        await update.message.reply_text(
            "Could not process the voice message. Please try again."
        )
    finally:
        if ogg_path:
            try:
                Path(ogg_path).unlink(missing_ok=True)
            except OSError:
                pass


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

    if doc.file_size and doc.file_size > MAX_DOC_BYTES:
        await update.message.reply_text(
            f"File too large ({doc.file_size // 1024 // 1024}MB). "
            f"Max is {MAX_DOC_BYTES // 1024 // 1024}MB."
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
            # context manager guarantees close()
            with fitz.open(stream=raw.read(), filetype="pdf") as pdf_doc:
                for page in pdf_doc:
                    text += page.get_text() + "\n"
        else:
            # try common encodings in order; latin-1 always succeeds
            raw_bytes = raw.read()
            for enc in ("utf-8", "utf-8-sig", "cp1252"):
                try:
                    text = raw_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = raw_bytes.decode("latin-1")

        if not text.strip():
            await update.message.reply_text(
                "No text could be extracted from the document."
            )
            return

        truncated = False
        if len(text) > MAX_DOC_CHARS_STORED:
            text = text[:MAX_DOC_CHARS_STORED]
            truncated = True

        persistence.user_docs[uid] = text
        await persistence.save_async(config.DATA_FILE)
        msg = f"Document saved ({len(text)} chars"
        if truncated:
            msg += ", truncated from original"
        msg += "). Use /ask <question> to query it."
        await update.message.reply_text(msg)
    except Exception:
        # log full exception, send generic reply
        logger.exception("doc handler failed")
        await update.message.reply_text(
            "Could not process the document. Please try again."
        )


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
    except Exception:
        # log full exception, send generic reply
        logger.exception("group mention handler failed")
        await update.message.reply_text("Error.")


def _safe_workspace(user_id: int) -> str:
    """BUG-017 fix: ensure workspace stays inside WORKSPACE_DIR."""
    base = Path(config.WORKSPACE_DIR).resolve()
    user_dir = (base / str(user_id)).resolve()
    if not user_dir.is_relative_to(base):
        raise ValueError(f"Unsafe workspace path: {user_dir}")
    user_dir.mkdir(parents=True, exist_ok=True)
    return str(user_dir)
