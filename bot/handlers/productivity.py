import asyncio
import io
import logging
from html import escape

from telegram import Update, InputFile
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import config, persistence
from bot.ollama import OllamaError, generate, reply_long

logger = logging.getLogger(__name__)

DDGS_AVAILABLE = False
PYMUPDF_AVAILABLE = False

try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    pass

# DDG occasionally serves an anti-bot "protection / privacy peace of mind"
# splash instead of results. Detect that and tell the user to retry rather
# than dumping the raw exception.
_DDG_PROTECTION_MARKERS = (
    "duckduckgo-protection",
    "privacy, simplified",
    "peace of mind",
    "anomaly",
    "ratelimit",
    "rate limit",
    "202 ratelimit",
)


def _looks_like_ddg_protection(err: BaseException) -> bool:
    msg = str(err).lower()
    return any(m in msg for m in _DDG_PROTECTION_MARKERS)

try:
    import fitz
    PYMUPDF_AVAILABLE = True
except ImportError:
    pass


async def web_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not DDGS_AVAILABLE:
        await update.message.reply_text(
            "Web search not available. Install `duckduckgo_search`."
        )
        return
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /search &lt;query&gt;")
        return

    uid = update.effective_user.id
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    # DDG frequently serves an anti-bot splash from one backend while
    # others still work. Try each in order before giving up.
    def _ddg_search(q: str):
        last_err: Exception | None = None
        for backend in ("api", "html", "lite"):
            try:
                with DDGS() as ddgs:
                    return list(
                        ddgs.text(q, max_results=5, backend=backend)
                    )
            except TypeError:
                # Older/newer DDGS versions don't accept `backend=`; one
                # attempt without it is the best we can do.
                try:
                    with DDGS() as ddgs:
                        return list(ddgs.text(q, max_results=5))
                except Exception as e:
                    last_err = e
                    break
            except Exception as e:
                last_err = e
                logger.warning("DDG backend %s failed: %s", backend, e)
                continue
        if last_err is not None:
            raise last_err
        return []

    try:
        results = await asyncio.to_thread(_ddg_search, query)
    except Exception as e:
        logger.warning("DDG search failed for %r: %s", query, e)
        if _looks_like_ddg_protection(e):
            await update.message.reply_text(
                "DuckDuckGo is rate-limiting search right now. "
                "Please try again in a minute."
            )
        else:
            await update.message.reply_text(f"Search error: {escape(str(e))}")
        return

    if not results:
        await update.message.reply_text(
            "No results found (DuckDuckGo may be rate-limiting; try again shortly)."
        )
        return

    ctx = f"Search results for query: {query}\n\n"
    for r in results:
        ctx += (
            f"- {r.get('title', '')}: {r.get('href', '')}\n"
            f"  {r.get('body', '')}\n\n"
        )
    ctx += "\nAnswer the user's question based on the above search results."

    try:
        reply = await generate(uid, ctx)
    except OllamaError:
        # do not persist on backend failure.
        await update.message.reply_text("AI is unreachable, please try again.")
        return

    persistence.track_user(uid)
    persistence.chat_histories[uid].append(
        {"user": f"[web search] {query}", "assistant": reply}
    )
    await persistence.save_async(config.DATA_FILE)
    await reply_long(update, escape(reply))


async def ask_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    doc = persistence.user_docs.get(uid)
    if not doc:
        await update.message.reply_text(
            "No document uploaded. Send me a PDF or .txt file first."
        )
        return
    question = " ".join(context.args) if context.args else ""
    if not question:
        await update.message.reply_text("Usage: /ask &lt;question&gt;")
        return

    # reuse the storage cap so /ask sees the full stored doc.
    from bot.handlers.media import MAX_DOC_CHARS_STORED as MAX_DOC_CHARS
    truncated = doc[:MAX_DOC_CHARS] + (
        "\n\n[doc truncated]" if len(doc) > MAX_DOC_CHARS else ""
    )
    prompt = (
        f"Document content:\n{truncated}\n\n"
        f"Question: {question}\n\nAnswer based on the document."
    )

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )
    try:
        reply = await generate(uid, prompt)
    except OllamaError:
        await update.message.reply_text("AI is unreachable, please try again.")
        return

    persistence.track_user(uid)
    persistence.chat_histories[uid].append(
        {"user": f"[doc] {question}", "assistant": reply}
    )
    # privacy — drop the uploaded document once answered.
    persistence.user_docs.pop(uid, None)
    await persistence.save_async(config.DATA_FILE)
    await reply_long(update, escape(reply) + "\n\n_(document cleared)_")


async def export_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    history = persistence.chat_histories.get(uid, [])
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
    await update.message.reply_document(
        document=InputFile(buf), caption="Your chat export."
    )
