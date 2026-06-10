import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.getenv("MODEL", "gemma4:e4b")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT") or (
    "You are DevDeskAI, an AI assistant. Respond conversationally and concisely."
)
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))

ADMIN_IDS: set[int] = set()
admin_raw = os.getenv("ADMIN_IDS", "")
if admin_raw:
    ADMIN_IDS = {int(x.strip()) for x in admin_raw.split(",") if x.strip()}

DATA_FILE = os.getenv("DATA_FILE", "bot_data.json")

# resolve and validate WORKSPACE_DIR
WORKSPACE_DIR = os.path.abspath(
    os.getenv("WORKSPACE_DIR", os.path.join(os.getcwd(), "workspace"))
)
os.makedirs(WORKSPACE_DIR, exist_ok=True)

BOT_USERNAME = os.getenv("BOT_USERNAME", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Brain fallback models for try_with_model_swap. Comma-separated.
# When the current model produces low-quality output, the brain cycles
# through these before giving up.
FALLBACK_MODELS = os.getenv("FALLBACK_MODELS", "")

# Confidence threshold (0-1) above which the brain runs tests itself
# rather than delegating to a test agent.
TEST_CONFIDENCE_THRESHOLD = float(os.getenv("TEST_CONFIDENCE_THRESHOLD", "0.7"))

# validate webhook config
if WEBHOOK_URL:
    if not WEBHOOK_URL.startswith("https://"):
        raise ValueError("WEBHOOK_URL must be HTTPS for Telegram webhooks")
    if WEBHOOK_PORT not in (443, 80, 88, 8443):
        raise ValueError(
            f"Invalid WEBHOOK_PORT {WEBHOOK_PORT}. "
            "Allowed: 443, 80, 88, 8443"
        )

# CUSTOM_CMD_<NAME> defines a custom command /<name>. Its value is the prompt
# template, UNLESS CUSTOM_CMD_<NAME>_PROMPT is also set, in which case the
# _PROMPT variant takes precedence. To avoid silent overrides, setting both
# with different values is a startup error.
CUSTOM_COMMANDS: dict[str, str] = {}
for key, val in os.environ.items():
    if key.startswith("CUSTOM_CMD_") and not key.endswith("_PROMPT"):
        name = key[len("CUSTOM_CMD_"):].lower()
        prompt_key = f"CUSTOM_CMD_{name.upper()}_PROMPT"
        prompt_override = os.getenv(prompt_key)
        if prompt_override is not None and prompt_override != val:
            raise ValueError(
                f"{key} and {prompt_key} are both set with different values; "
                f"unset one or make them match."
            )
        prompt = prompt_override if prompt_override is not None else val
        CUSTOM_COMMANDS[name] = prompt

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in .env file")
