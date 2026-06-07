import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.getenv("MODEL", "gemma4:e4b")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are DevDeskAI, an AI assistant. Respond conversationally and concisely.",
)
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))

ADMIN_IDS: set[int] = set()
admin_raw = os.getenv("ADMIN_IDS", "")
if admin_raw:
    ADMIN_IDS = {int(x.strip()) for x in admin_raw.split(",") if x.strip()}

DATA_FILE = os.getenv("DATA_FILE", "bot_data.json")
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", os.path.join(os.getcwd(), "workspace"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

CUSTOM_COMMANDS: dict[str, str] = {}
for key, val in os.environ.items():
    if key.startswith("CUSTOM_CMD_") and not key.endswith("_PROMPT"):
        name = key[len("CUSTOM_CMD_"):].lower()
        prompt_key = f"CUSTOM_CMD_{name.upper()}_PROMPT"
        prompt = os.getenv(prompt_key, val)
        CUSTOM_COMMANDS[name] = prompt

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in .env file")
