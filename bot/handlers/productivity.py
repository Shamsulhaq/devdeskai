import io
from html import escape

from telegram import Update, InputFile
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import config, persistence
from bot.ollama import generate, reply_long

DDGS_AVAILABLE = False
PYMUPDF_AVAILABLE = False

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

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            await update.message.reply_text("No results found.")
            return

        ctx = f"Search results for query: {query}\n\n"
        for r in results:
            ctx += (
                f"- {r.get('title', '')}: {r.get('href', '')}\n"
                f"  {r.get('body', '')}\n\n"
            )
        ctx += "\nAnswer the user's question based on the above search results."

        reply = await generate(uid, ctx)
        persistence.track_user(uid)
        persistence.chat_histories[uid].append(
            {"user": f"[web search] {query}", "assistant": reply}
        )
        persistence.save(config.DATA_FILE)
        await reply_long(update, escape(reply))
    except Exception as e:
        await update.message.reply_text(f"Search error: {e}")


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

    MAX_DOC_CHARS = 8000
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
        persistence.track_user(uid)
        persistence.chat_histories[uid].append(
            {"user": f"[doc] {question}", "assistant": reply}
        )
        persistence.save(config.DATA_FILE)
        await reply_long(update, escape(reply))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


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
