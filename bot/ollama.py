from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from html import escape

from telegram import Update
from telegram.constants import ParseMode

from bot import config, persistence

logger = logging.getLogger(__name__)

MAX_PREDICT_TOKENS = 1024  # BUG-021 fix

_client = None


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
) -> list[dict]:
    history = persistence.chat_histories[user_id]
    messages = [{"role": "system", "content": get_system_prompt(user_id)}]
    for entry in history[-config.MAX_HISTORY :]:
        messages.append({"role": "user", "content": entry["user"]})
        messages.append({"role": "assistant", "content": entry["assistant"]})
    user_msg = {"role": "user", "content": user_message}
    if image_data:
        user_msg["images"] = [image_data]
    messages.append(user_msg)
    return messages


def _sync_generate(
    model: str, messages: list[dict], opts: dict | None
) -> str:
    """Synchronous Ollama call — must be run in a thread (BUG-006 fix)."""
    client = get_client()
    stream = client.chat(
        model=model,
        messages=messages,
        stream=True,
        options=opts or None,
    )
    full = ""
    for chunk in stream:
        content = chunk.get("message", {}).get("content", "") or ""
        full += content
    return full


async def generate(
    user_id: int,
    prompt: str,
    image_data: str | None = None,
) -> str:
    model = get_model(user_id)
    messages = build_messages(user_id, prompt, image_data)
    opts = {"num_predict": MAX_PREDICT_TOKENS}  # BUG-021 fix
    temp = get_temperature(user_id)
    if temp is not None:
        opts["temperature"] = temp

    # BUG-006 fix: run sync Ollama call in thread so event loop stays free
    for attempt in range(2):  # BUG-020 fix: single retry on connection error
        try:
            full = await asyncio.to_thread(_sync_generate, model, messages, opts)
            return full or "No response generated."
        except (ConnectionError, TimeoutError) as e:
            if attempt == 1:
                logger.error("Ollama connection failed: %s", e)
                return "Ollama is unreachable. Please try again."
            await asyncio.sleep(1)
        except Exception as e:
            logger.exception("Ollama generate failed")
            return f"Error: {e}"
    return "No response generated."  # unreachable


async def reply_long(
    target: Update | Callable,
    text: str,
) -> None:
    MAX_LEN = 4096
    # BUG-022 fix: explicit type check
    if callable(target) and not hasattr(target, "message"):
        reply = target
    else:
        reply = target.message.reply_text
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


async def generate_and_reply(
    update: Update,
    user_id: int,
    user_text: str,
    image_data: str | None = None,
) -> str:
    reply = await generate(user_id, user_text, image_data)
    persistence.chat_histories[user_id].append(
        {"user": user_text, "assistant": reply}
    )
    persistence.track_user(user_id)
    # BUG-015 fix: use async save with lock
    await persistence.save_async(config.DATA_FILE)
    await reply_long(update, escape(reply))
    return reply


# BUG-019 fix: default is always a non-empty string
DEFAULT_PERSONA_PROMPT = (
    "You are DevDeskAI, an AI assistant. "
    "Respond conversationally and concisely."
)
PERSONAS = {
    "default": config.SYSTEM_PROMPT or DEFAULT_PERSONA_PROMPT,
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
