from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from html import escape

from telegram import Update
from telegram.constants import ParseMode

from bot import config, persistence

logger = logging.getLogger(__name__)

MAX_PREDICT_TOKENS = int(os.environ.get("MAX_PREDICT_TOKENS", "1024"))

# Throttle Telegram edits during streaming. Telegram rate-limits edits to
# roughly 1/sec per chat; stay safely under that.
STREAM_EDIT_INTERVAL = float(os.environ.get("STREAM_EDIT_INTERVAL", "1.2"))
# Cap streaming edits below Telegram's 4096-char limit; once exceeded we
# stop editing the placeholder and finalize as split messages.
STREAM_EDIT_MAX_LEN = 3800

_client = None

# per-user semaphore to prevent one user queueing many
# concurrent generations against Ollama.
_user_semaphores: dict[int, asyncio.Semaphore] = {}


class OllamaError(Exception):
    """Raised when an Ollama backend call fails. Callers must NOT persist
    the error message as an assistant turn."""
    pass


def get_client():
    global _client
    if _client is None:
        import ollama
        _client = ollama.Client(host=config.OLLAMA_HOST)
    return _client


def get_system_prompt(user_id: int) -> str:
    persona_key = persistence.user_personas.get(user_id)
    if persona_key and persona_key in PERSONAS:
        return PERSONAS[persona_key]
    return persistence.user_prompts.get(user_id, config.SYSTEM_PROMPT)


def get_model(user_id: int) -> str:
    return persistence.user_models.get(user_id, config.MODEL)


def get_temperature(user_id: int) -> float | None:
    return persistence.user_temps.get(user_id)


def build_messages(
    user_id: int,
    user_message: str,
    image_data: str | None = None,
    system: str | None = None,
) -> list[dict]:
    history = persistence.chat_histories[user_id]
    system_prompt = system if system is not None else get_system_prompt(user_id)
    messages = [{"role": "system", "content": system_prompt}]
    for entry in history[-config.MAX_HISTORY :]:
        messages.append({"role": "user", "content": entry["user"]})
        messages.append({"role": "assistant", "content": entry["assistant"]})
    user_msg = {"role": "user", "content": user_message}
    if image_data:
        user_msg["images"] = [image_data]
    messages.append(user_msg)
    return messages


def _build_chat_kwargs(user_id: int, prompt: str, image_data: str | None, system: str | None = None) -> dict:
    opts: dict = {"num_predict": MAX_PREDICT_TOKENS}
    temp = get_temperature(user_id)
    if temp is not None:
        opts["temperature"] = temp
    return {
        "model": get_model(user_id),
        "messages": build_messages(user_id, prompt, image_data, system=system),
        "options": opts,
    }


async def generate(
    user_id: int,
    prompt: str,
    image_data: str | None = None,
    system: str | None = None,
) -> str:
    client = get_client()
    kwargs = _build_chat_kwargs(user_id, prompt, image_data, system=system)

    # serialize concurrent generations per user.
    sem = _user_semaphores.setdefault(user_id, asyncio.Semaphore(1))
    await sem.acquire()
    try:
        try:
            resp = await asyncio.to_thread(client.chat, **kwargs)
        except (ConnectionError, TimeoutError) as e:
            # retry once, then raise so callers don't persist.
            logger.warning("Ollama transient error, retrying: %s", e)
            try:
                resp = await asyncio.to_thread(client.chat, **kwargs)
            except (ConnectionError, TimeoutError) as e2:
                raise OllamaError(str(e2)) from e2
            except Exception as e2:
                raise OllamaError(str(e2)) from e2
        except Exception as e:
            # raise so callers don't persist error text.
            raise OllamaError(str(e)) from e

        content = resp.get("message", {}).get("content", "") or ""
        return content or "No response generated."
    finally:
        sem.release()


async def stream_generate(
    user_id: int,
    prompt: str,
    image_data: str | None = None,
) -> AsyncIterator[str]:
    """Yield content chunks from Ollama as they arrive.

    Bridges the sync ollama streaming iterator onto the event loop via a
    background thread + asyncio.Queue. Per-user semaphore still applies so
    one user cannot queue many concurrent generations.
    """
    client = get_client()
    kwargs = _build_chat_kwargs(user_id, prompt, image_data)

    sem = _user_semaphores.setdefault(user_id, asyncio.Semaphore(1))
    await sem.acquire()
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    _SENTINEL = object()

    def _producer() -> None:
        try:
            stream = client.chat(stream=True, **kwargs)
            for part in stream:
                chunk = (part.get("message") or {}).get("content") or ""
                if chunk:
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

    thread = threading.Thread(target=_producer, daemon=True)
    thread.start()
    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                return
            if isinstance(item, Exception):
                raise OllamaError(str(item)) from item
            yield item
    finally:
        sem.release()


def _split_for_telegram(text: str, max_len: int = 4096) -> list[str]:
    """Split pre-escaped text into Telegram-sized chunks at whitespace.

    OPUS-006 fix: callers escape the text BEFORE passing it in, so the only
    occurrences of `<` in `text` are inside `&lt;` and similar HTML entities,
    never inside real tags. Splitting on whitespace therefore cannot bisect
    a tag.
    """
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            parts.append(remaining)
            break
        # Prefer newline boundary, then space; force whitespace splits only.
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, max_len)
        if split_at == -1:
            # No whitespace in the window — fall back to hard cut. Since
            # the input is HTML-escaped, this cannot split a real tag.
            split_at = max_len
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    return parts


async def reply_long(update: Update, text: str) -> None:
    """Reply to an Update with text that may exceed Telegram's 4096 limit.

    OPUS-023 fix: split from the Callable overload into a dedicated
    Update-based function. Callers escape text before calling.
    """
    reply = update.message.reply_text
    for part in _split_for_telegram(text):
        await reply(part, parse_mode=ParseMode.HTML)


async def send_long(send_fn: Callable, text: str) -> None:
    """Send text via an arbitrary send callable (e.g. `bot.send_message` or
    a bound `reply_text`). OPUS-023 fix: counterpart to `reply_long`."""
    for part in _split_for_telegram(text):
        await send_fn(part, parse_mode=ParseMode.HTML)


async def _typing_keepalive(bot, chat_id: int, stop: asyncio.Event) -> None:
    """Re-send `typing` chat action every ~4s until stop is set.

    Telegram's typing indicator lasts ~5s; this keeps it visible while
    we wait for the first streamed chunk from Ollama.
    """
    from telegram.constants import ChatAction
    try:
        while not stop.is_set():
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception:
                # Best-effort; don't let a transient send failure kill the reply.
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        return


async def generate_and_reply(
    update: Update,
    user_id: int,
    user_text: str,
    image_data: str | None = None,
) -> str | None:
    """Stream the response into a single Telegram message, then finalize.

    Sends a placeholder reply immediately, edits it as chunks arrive
    (throttled to one edit per STREAM_EDIT_INTERVAL seconds), and once
    the streamed text would exceed STREAM_EDIT_MAX_LEN, stops editing
    and sends the rest as follow-up messages via reply_long.
    """
    chat_id = update.effective_chat.id
    bot = update.get_bot()

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        _typing_keepalive(bot, chat_id, stop_typing)
    )

    placeholder = None
    accumulated = ""
    last_edit_text = ""
    last_edit_ts = 0.0
    overflowed = False

    try:
        try:
            stream = stream_generate(user_id, user_text, image_data)
            async for chunk in stream:
                accumulated += chunk
                # First chunk arrived — kill the typing keepalive and post
                # the placeholder so the user sees text immediately.
                if placeholder is None:
                    stop_typing.set()
                    placeholder = await update.message.reply_text(
                        escape(accumulated) or "…",
                        parse_mode=ParseMode.HTML,
                    )
                    last_edit_text = accumulated
                    last_edit_ts = asyncio.get_running_loop().time()
                    continue

                if overflowed:
                    continue

                if len(accumulated) > STREAM_EDIT_MAX_LEN:
                    overflowed = True
                    continue

                now = asyncio.get_running_loop().time()
                if (
                    now - last_edit_ts >= STREAM_EDIT_INTERVAL
                    and accumulated != last_edit_text
                ):
                    try:
                        await placeholder.edit_text(
                            escape(accumulated),
                            parse_mode=ParseMode.HTML,
                        )
                        last_edit_text = accumulated
                        last_edit_ts = now
                    except Exception as e:
                        # Edit failures (rate limit, network) shouldn't abort
                        # the stream; we'll catch up on the next tick.
                        logger.debug("stream edit failed: %s", e)
        except OllamaError as e:
            logger.warning("Ollama generate failed: %s", e)
            stop_typing.set()
            if placeholder is None:
                await update.message.reply_text(
                    "AI is unreachable, please try again."
                )
            else:
                try:
                    await placeholder.edit_text(
                        "AI is unreachable, please try again."
                    )
                except Exception:
                    pass
            return None
    finally:
        stop_typing.set()
        typing_task.cancel()
        try:
            await typing_task
        except (asyncio.CancelledError, Exception):
            pass

    reply = accumulated or "No response generated."

    # Finalize: ensure the placeholder shows the final text (or the
    # truncated edit-window prefix if we overflowed), then send any
    # remaining chunks as follow-up messages.
    if placeholder is None:
        # Stream produced no chunks at all (e.g. empty response).
        await reply_long(update, escape(reply))
    elif overflowed:
        head = reply[:STREAM_EDIT_MAX_LEN]
        try:
            await placeholder.edit_text(escape(head), parse_mode=ParseMode.HTML)
        except Exception:
            pass
        tail = reply[STREAM_EDIT_MAX_LEN:]
        if tail:
            await send_long(update.message.reply_text, escape(tail))
    else:
        if reply != last_edit_text:
            try:
                await placeholder.edit_text(
                    escape(reply), parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    persistence.chat_histories[user_id].append(
        {"user": user_text, "assistant": reply}
    )
    persistence.track_user(user_id)
    await persistence.save_async(config.DATA_FILE)
    return reply


PERSONAS = {
    "default": config.SYSTEM_PROMPT,
    "coder": (
        "You are an expert software engineer. Write clean, efficient, "
        "well-documented code. Think step by step and explain your reasoning."
    ),
    "poet": (
        "You are a poet and creative writer. Respond with rich imagery, "
        "emotion, and literary flair."
    ),
    "friendly": (
        "You are a warm, friendly companion. Be supportive, encouraging, "
        "and casual."
    ),
    "concise": (
        "You are a direct, no-nonsense assistant. Answer in as few words "
        "as possible while being accurate."
    ),
    "socratic": (
        "You are a Socratic tutor. Don't give answers directly—guide the "
        "user with questions to help them discover the answer themselves."
    ),
    "pirate": (
        "Arr! Ye be talkin' to a pirate AI. Speak like a swashbuckler "
        "of the high seas, matey!"
    ),
}
