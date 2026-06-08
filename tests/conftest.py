# MUST run before any `bot.*` import — `bot.config` raises at import time
# if BOT_TOKEN is not set.
import os

os.environ.setdefault("BOT_TOKEN", "test")

# Neutralise a developer's local `.env` so tests see a deterministic
# environment. Without this, `dotenv.load_dotenv()` (called at the top of
# `bot/config.py`) walks up from cwd and clobbers test-set values on reload.
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *a, **kw: False  # type: ignore[assignment]

import pytest  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _ensure_bot_token():
    """Belt-and-braces: keep BOT_TOKEN set for the full session."""
    os.environ.setdefault("BOT_TOKEN", "test")
    yield
