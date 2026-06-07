# DevDeskAI — Telegram Bot User Manual

AI-powered Telegram bot with Ollama brain, coding agents, web search, document Q&A, voice support, and more.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Configuration](#configuration)
3. [Commands Reference](#commands-reference)
4. [AI Chat](#ai-chat)
5. [Personas](#personas)
6. [Temperature Control](#temperature-control)
7. [Web Search](#web-search)
8. [Document Q&A](#document-qa)
9. [Image Understanding](#image-understanding)
10. [Voice Messages](#voice-messages)
11. [Group Chat](#group-chat)
12. [Coding Agents](#coding-agents)
13. [Custom Commands](#custom-commands)
14. [Chat Export](#chat-export)
15. [Admin Commands](#admin-commands)
16. [Data Persistence](#data-persistence)
17. [Production Deployment](#production-deployment)
18. [Docker](#docker)

---

## Quick Start

### Prerequisites
- Python 3.10+
- [Ollama](https://ollama.ai) running locally with a model pulled (e.g., `gemma4:e4b`)
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### Setup

```bash
# 1. Clone and enter project
cd devdeskai

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# Edit .env — set your BOT_TOKEN

# 5. Run
python3 main.py
```

---

## Configuration

All configuration is done via `.env` file (copy from `.env.example`).

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_TOKEN` | _(required)_ | Telegram bot token from BotFather |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `MODEL` | `gemma4:e4b` | Default Ollama model |
| `SYSTEM_PROMPT` | `You are DevDeskAI...` | Default system prompt |
| `MAX_HISTORY` | `20` | Conversation pairs kept per user |
| `ADMIN_IDS` | `` | Comma-separated Telegram user IDs for admin commands |
| `WORKSPACE_DIR` | `./workspace` | Directory for coding agent tasks |
| `BOT_USERNAME` | `` | Bot username (required for group mentions) |
| `WEBHOOK_URL` | `` | Public URL for webhook mode |
| `WEBHOOK_PORT` | `8443` | Webhook listen port |
| `WEBHOOK_SECRET` | `` | Webhook secret token |

### Custom Commands

```env
CUSTOM_CMD_REVIEW=Review this code for bugs and improvements
CUSTOM_CMD_TRANSLATE=Translate the following to Spanish
```

Each `CUSTOM_CMD_<NAME>` creates `/name` that prepends the prompt text to your message.

---

## Commands Reference

### General

| Command | Description |
|---------|-------------|
| `/start` | Welcome message with all features listed |
| `/reset` | Clear your conversation history |
| `/model` | Show current model and temperature |
| `/models` | List all available Ollama models |
| `/switch <model>` | Switch to a different Ollama model |
| `/temp <0-2>` | Set AI temperature (0 = precise, 2 = creative) |
| `/prompt [text]` | View or set a custom system prompt |
| `/resetprompt` | Reset system prompt to default |

### Personas

| Command | Description |
|---------|-------------|
| `/persona` | List all available personas |
| `/persona <name>` | Switch to a persona |

Built-in personas: `default`, `coder`, `poet`, `friendly`, `concise`, `socratic`, `pirate`.

### Productivity

| Command | Description |
|---------|-------------|
| `/search <query>` | Web search via DuckDuckGo + AI answer |
| `/ask <question>` | Ask a question about an uploaded document |
| `/export` | Download your conversation as `.txt` file |

### Coding Agents

| Command | Description |
|---------|-------------|
| `/agents` | List available coding agents |
| `/claude [prompt]` | Enter Claude agent mode or send one-shot prompt |
| `/opencode [prompt]` | Enter OpenCode agent mode |
| `/codex [prompt]` | Enter Codex agent mode |
| `/copilot [prompt]` | Enter GitHub Copilot agent mode |
| `/qwen [prompt]` | Enter Qwen agent mode |
| `/gemini [prompt]` | Enter Gemini agent mode |
| `/exit` or `/back` | Exit current agent mode |

Agents auto-detect installation — only installed tools show as available.

### Admin

| Command | Description |
|---------|-------------|
| `/stats` | Show total messages and user count |
| `/announce <msg>` | Broadcast message to all users |

### Custom

Any command defined as `CUSTOM_CMD_<NAME>` in `.env` becomes `/<name>`.

---

## AI Chat

Just send a message to the bot. It uses the configured Ollama model to generate replies.

**Features:**
- Maintains per-user conversation history (up to `MAX_HISTORY` exchanges)
- Supports streaming — replies appear as they're generated
- Long messages are automatically split at 4096 characters (Telegram limit)
- HTML-safe output

### Multi-Model Switching

```text
/switch gemma4:e4b     → Switch to Gemma 4
/switch llama3.2:3b    → Switch to Llama 3.2
/models                → See all available models
```

Each user has their own model preference (persisted across restarts).

---

## Personas

Personas are curated system prompts that change the bot's behavior instantly.

```text
/persona coder     → Expert programmer, writes clean code
/persona poet      → Creative, literary responses
/persona friendly  → Warm and supportive
/persona concise   → Short and direct answers
/persona socratic  → Guides with questions, not answers
/persona pirate    → Arr, talks like a pirate!
/persona default   → Back to normal
```

Setting a persona overrides your custom `/prompt`. Use `/resetprompt` to clear both.

---

## Temperature Control

Temperature controls randomness (0.0–2.0):

- **0.0–0.3**: Deterministic, focused responses (good for code)
- **0.5–0.7**: Balanced (suitable for most conversations)
- **0.8–1.5**: Creative, varied responses
- **1.5–2.0**: Very random, experimental

```text
/temp 0.2    → Precise, factual
/temp 0.8    → Creative
/temp        → Show current temperature
```

Temperature is stored per user and persists across restarts.

---

## Web Search

Fetch real-time information from the web and get an AI-generated answer.

```text
/search latest AI news 2026
/search weather in Dhaka
/search python async vs sync performance
```

How it works:
1. Queries DuckDuckGo for top 5 results
2. Feeds the results to the AI model
3. Returns a synthesized answer based on those results

Requires `duckduckgo_search` package (included in `requirements.txt`).

---

## Document Q&A

Upload a document and ask questions about its content.

### Supported formats
- `.txt` — Plain text
- `.pdf` — PDF documents (requires `PyMuPDF`)
- `.md` — Markdown
- `.csv` — CSV data
- `.json` — JSON data

### Usage

1. Send the document to the bot
2. Bot confirms: "Document saved (X chars). Use /ask to query it."
3. Ask questions:

```text
/ask What is the main topic?
/ask Summarize this document
/ask List all key findings
```

Documents are stored per user and persist across restarts. Documents longer than 8000 chars are truncated.

---

## Image Understanding

Send a photo to the bot and it will describe or analyze it.

```text
[send photo]          → "Describe this image" (default)
[send photo with caption] → Uses your caption
```

Works with any vision-capable Ollama model (Gemma 4, LLaVA, etc.).

---

## Voice Messages

Send a voice message and the bot will transcribe and reply.

### Installation

```bash
pip install faster-whisper    # Lightweight (recommended)
# OR
pip install openai-whisper    # Heavy (includes PyTorch)
```

### Usage

```text
[send 🎤 voice message]
→ Bot: Transcribed: "What is the capital of France?"
→ Bot: The capital of France is Paris.
```

The Whisper `base` model is downloaded automatically on first voice message (~150MB). The bot tries `faster-whisper` first, falls back to `openai-whisper`.

---

## Group Chat

Add the bot to a Telegram group and mention it by username.

### Setup

1. Add `BOT_USERNAME=your_bot_username` to `.env` (without `@`)
2. Add the bot to your group
3. Mention it: `@your_bot_username what is Python?`

The bot will reply with the AI-generated answer. Each user in the group gets their own conversation history.

---

## Coding Agents

Coding agents let you route messages to external AI coding CLI tools installed on your system. Each agent operates in a per-user workspace directory.

### How it works

1. **Detect**: On startup, the bot checks which CLI tools are installed
2. **Connect**: Use `/<agent>` to enter that agent's mode
3. **Chat**: Every message you send is piped to the agent CLI
4. **Exit**: Type `/exit` to return to normal chat

### One-shot mode

```text
/claude write a python script to sort a list
/copilot explain this bash command: grep -r "TODO" .
```

Enter agent mode for multi-turn:

```text
/copilot
→ Entered GitHub Copilot mode.
how do I optimize this SQL query?
→ [Copilot responds]
now add an index
→ [Copilot responds]
/exit
→ Exited Copilot mode.
```

### Supported Agents

| Agent | CLI Tool | Command |
|-------|----------|---------|
| Claude | `claude` | `/claude` |
| OpenCode | `opencode` | `/opencode` |
| Codex | `codex` | `/codex` |
| Qwen | `qwen` | `/qwen` |
| Gemini | `gemini` | `/gemini` |
| GitHub Copilot | `copilot` | `/copilot` |

### Workspace

Each user has an isolated workspace at `workspace/<user_id>/` where agent commands are executed. This allows agents to create and modify files safely.

---

## Custom Commands

Define your own commands in `.env` to create shortcuts for common tasks.

### Syntax

```env
CUSTOM_CMD_REVIEW=Review the following code for bugs and improvements
CUSTOM_CMD_TRANSLATE=Translate the following text to Spanish
CUSTOM_CMD_EXPLAIN=Explain this concept like I'm 5 years old
```

Each `CUSTOM_CMD_<NAME>` creates:
- A `/name` command (e.g., `/review`)
- Prepends the prompt text before your message
- If no additional message, just the prompt is sent

### Usage

```text
/review def foo():\n    pass
/translate Hello, how are you?
/explain
```

---

## Chat Export

Download your entire conversation history as a text file.

```text
/export
→ Bot sends: chat_export_123456.txt
```

Each export includes all user messages and bot replies in chronological order.

---

## Admin Commands

### Setup

Add your Telegram user IDs to `ADMIN_IDS` in `.env`:

```env
ADMIN_IDS=123456789,987654321
```

Find your user ID by messaging [@userinfobot](https://t.me/userinfobot) on Telegram.

### Commands

```text
/stats
→ DevDeskAI Stats
  Messages: 152
  Users: 3

/announce Bot will be down for maintenance at 2PM
→ Announcement sent to 3 users.
```

---

## Data Persistence

All data is saved to `bot_data.json` and survives bot restarts.

**Persisted per user:**
- Conversation history (last `MAX_HISTORY` exchanges)
- Custom system prompt
- Model preference
- Temperature setting
- Agent mode state
- Uploaded document text
- Persona selection

**Global stats:**
- Total messages
- Unique user IDs

---

## Production Deployment

### Webhook Mode (instead of polling)

For production, use a webhook so Telegram pushes updates to your server.

```env
WEBHOOK_URL=https://your-domain.com
WEBHOOK_PORT=8443
WEBHOOK_SECRET=your_secret_here
```

Requirements:
- Public domain or static IP with SSL
- Port accessible (8443 recommended)
- Telegram supports ports: 443, 80, 88, 8443

### Using systemd (Linux)

```ini
[Unit]
Description=DevDeskAI Telegram Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/devdeskai
ExecStart=/path/to/devdeskai/.venv/bin/python3 main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

---

## Docker

### Build

```bash
docker build -t devdeskai .
```

### Run

```bash
docker run -d \
  --name devdeskai \
  --env-file .env \
  -v ./bot_data.json:/app/bot_data.json \
  -v ./workspace:/app/workspace \
  --network host \
  devdeskai
```

The `--network host` flag allows the container to reach Ollama on `localhost:11434`. Use a custom network if Ollama is containerized separately.

### Docker Compose

```yaml
version: "3"
services:
  devdeskai:
    build: .
    env_file: .env
    volumes:
      - ./bot_data.json:/app/bot_data.json
      - ./workspace:/app/workspace
    network_mode: host
```

---

## BotFather Command List

Copy this into BotFather `/setcommands`:

```
start - Welcome + list all commands
reset - Clear conversation history
model - Show current model
models - List available Ollama models
switch - Switch to a different model
temp - Set temperature 0-2
persona - Switch persona preset
prompt - Set custom system prompt
resetprompt - Reset prompt to default
search - Web search with AI answer
ask - Ask about uploaded document
export - Download chat as txt file
agents - List available coding agents
claude - Chat with Claude CLI agent
opencode - Chat with OpenCode CLI agent
codex - Chat with Codex CLI agent
copilot - Chat with GitHub Copilot CLI agent
qwen - Chat with Qwen CLI agent
gemini - Chat with Gemini CLI agent
exit - Exit agent mode
stats - Show usage stats
announce - Broadcast to all users (admin)
```

---

## Requirements

### Required
- `python-telegram-bot` — Telegram Bot API
- `ollama` — Ollama API client
- `python-dotenv` — Environment config

### Optional
- `faster-whisper` — Voice transcription (lightweight, recommended)
- `openai-whisper` — Voice transcription (heavy, fallback)
- `duckduckgo_search` — Web search functionality
- `PyMuPDF` — PDF document parsing

---

## File Structure

```
devdeskai/
  .env              ← Your configuration (git-ignored)
  .env.example      ← Configuration template
  .gitignore
  bot_data.json     ← Persistent data (auto-created)
  Dockerfile        ← Container deployment
  DOCS.md           ← This file
  main.py           ← Bot source code
  requirements.txt  ← Python dependencies
  workspace/        ← Agent workspaces (auto-created)
    └── <user_id>/
```

---

## Troubleshooting

### Bot doesn't respond
- Check `BOT_TOKEN` is correct in `.env`
- Ensure Ollama is running: `ollama serve`
- Check the terminal output for error messages

### "Model not found"
- Pull the model: `ollama pull gemma4:e4b`
- Or use /models to see what's available

### Voice not working
- Install a transcriber: `pip install faster-whisper`
- First use downloads the model (~150MB)

### Agent not showing as available
- Ensure the CLI tool is installed and in your PATH
- Restart the bot after installing

### Group mentions not working
- Set `BOT_USERNAME` in `.env` (without @)
- Ensure the bot is added to the group
- Bot must be allowed to read group messages (disable privacy mode via BotFather: `/setprivacy` → Disable)
