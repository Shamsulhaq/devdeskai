# DevDeskAI

> A Telegram AI assistant powered by Ollama — with multi-model chat, coding agents, web search, document Q&A, voice transcription, and more.

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=fff)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-2CA5E0?logo=telegram&logoColor=fff)](https://t.me/botfather)
[![Ollama](https://img.shields.io/badge/Ollama-Local-5B5BD6?logo=ollama&logoColor=fff)](https://ollama.ai)

---

## Features

- **AI Chat** — Conversational AI via local Ollama models (Gemma, Llama, Mistral, etc.)
- **Multi-Model Switching** — Swap models on the fly per user (`/switch`)
- **Temperature Control** — Fine-tune creativity per user (`/temp`)
- **Persona Presets** — 7 built-in personalities: coder, poet, socratic, pirate, and more
- **Image Understanding** — Send photos for vision-capable models to describe
- **Voice Messages** — Transcribe and reply using Whisper (optional)
- **Web Search** — Real-time DuckDuckGo search + AI answer (`/search`)
- **Document Q&A** — Upload PDFs/txt files and query them (`/ask`)
- **Coding Agents** — Route tasks to Claude, OpenCode, Copilot, Codex, Gemini, Qwen CLI tools
- **Group Chat** — Mention the bot in groups for AI responses
- **Chat Export** — Download conversation history (`/export`)
- **Custom Commands** — Define your own shortcuts in `.env`
- **Admin Broadcast** — Announce messages to all users
- **Docker Support** — Containerized deployment ready
- **Webhook Mode** — Production-ready webhook support

---

## Table of Contents

- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [Commands](#commands)
- [Optional Features](#optional-features)
- [Project Structure](#project-structure)
- [Docker](#docker)
- [Production Deployment](#production-deployment)
- [Contributing](#contributing)
- [License](#license)

---

## Quick Start

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.ai) installed and running
- A Telegram bot token from [@BotFather](https://t.me/botfather)

### Installation

```bash
# Clone the repository
git clone https://github.com/Shamsulhaq/devdeskai.git
cd devdeskai

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure the bot
cp .env.example .env
# Edit .env — set your BOT_TOKEN (required)

# Pull an Ollama model (if you haven't already)
ollama pull gemma4:e4b

# Run the bot
python3 main.py
```

---

## Configuration

All configuration is done through the `.env` file. Copy `.env.example` to `.env` and adjust.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `BOT_TOKEN` | — | **Yes** | Telegram bot token from @BotFather |
| `OLLAMA_HOST` | `http://localhost:11434` | No | Ollama server URL |
| `MODEL` | `gemma4:e4b` | No | Default Ollama model |
| `SYSTEM_PROMPT` | `You are DevDeskAI...` | No | Default system prompt |
| `MAX_HISTORY` | `20` | No | Conversation pairs kept per user |
| `ADMIN_IDS` | — | No | Comma-separated Telegram user IDs for `/announce` |
| `WORKSPACE_DIR` | `./workspace` | No | Directory for coding agent tasks |
| `BOT_USERNAME` | — | No | Bot username for group mention support |
| `WEBHOOK_URL` | — | No | Public URL for webhook mode |
| `WEBHOOK_PORT` | `8443` | No | Webhook listen port |
| `WEBHOOK_SECRET` | — | No | Webhook secret token |

### Custom Commands

Define custom slash commands in `.env`:

```env
CUSTOM_CMD_REVIEW=Review the following code for bugs and improvements
CUSTOM_CMD_TRANSLATE=Translate the following text to Spanish
CUSTOM_CMD_EXPLAIN=Explain this concept simply
```

Each `CUSTOM_CMD_<NAME>` creates `/<name>`. The prompt text is prepended to your message when you use the command.

---

## Usage

Run the bot:

```bash
python3 main.py
# or
python3 -m bot
```

The bot starts in polling mode by default. Set `WEBHOOK_URL` in `.env` to switch to webhook mode.

---

## Commands

### General

| Command | Description |
|---------|-------------|
| `/start` | Welcome message with all features |
| `/reset` | Clear your conversation history |
| `/model` | Show current model and temperature |
| `/models` | List all available Ollama models |
| `/switch <model>` | Switch to a different model |
| `/temp <0-2>` | Set AI temperature |
| `/persona` | View available personas |
| `/persona <name>` | Switch to a persona (coder, poet, socratic, pirate, etc.) |
| `/prompt` | View current system prompt |
| `/prompt <text>` | Set a custom system prompt |
| `/resetprompt` | Reset system prompt to default |

### Productivity

| Command | Description |
|---------|-------------|
| `/search <query>` | Web search via DuckDuckGo + AI answer |
| `/ask <question>` | Ask a question about an uploaded document |
| `/export` | Download conversation as `.txt` file |

### Coding Agents

| Command | Description |
|---------|-------------|
| `/agents` | List available coding agents |
| `/claude [prompt]` | Chat with Claude CLI |
| `/opencode [prompt]` | Chat with OpenCode CLI |
| `/codex [prompt]` | Chat with Codex CLI |
| `/copilot [prompt]` | Chat with GitHub Copilot CLI |
| `/qwen [prompt]` | Chat with Qwen CLI |
| `/gemini [prompt]` | Chat with Gemini CLI |
| `/exit` | Exit current agent mode |

Use `/<agent> <prompt>` for one-shot, or just `/<agent>` for interactive mode (send multiple messages).

### Admin

| Command | Description |
|---------|-------------|
| `/stats` | Show total messages and user count |
| `/announce <msg>` | Broadcast to all users (requires `ADMIN_IDS`) |

---

## Optional Features

### Voice Transcription

```bash
pip install faster-whisper    # Lightweight (recommended)
# or
pip install openai-whisper    # Heavier fallback
```

The `base` Whisper model (~150MB) downloads on first use.

### Web Search

Included by default (`duckduckgo_search` in requirements.txt). No API key needed.

### Document Q&A

Included by default (`PyMuPDF` in requirements.txt) for PDF support. Plain text files work without it.

---

## Project Structure

```
devdeskai/
  .env.example        # Configuration template
  .gitignore
  DOCS.md             # Full user manual
  Dockerfile          # Container deployment
  LICENSE
  README.md           # This file
  requirements.txt    # Python dependencies
  main.py             # Entry point (backward-compatible wrapper)

  bot/                # Application package
    __init__.py
    __main__.py       # Entry point (python -m bot)
    main.py           # App setup, handler registration
    config.py         # Environment configuration
    persistence.py    # JSON data persistence layer
    ollama.py         # Ollama client wrapper, helpers, personas
    agents.py         # Agent detection and CLI execution
    handlers/
      core.py         # General commands (start, model, switch, temp, persona...)
      productivity.py # Search, document Q&A, export
      admin.py        # Stats, announce
      agents.py       # Agent mode enter/exit handlers
      media.py        # Text, voice, document, photo, group handlers
      custom.py       # Dynamic custom command handler
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

`--network host` lets the container reach Ollama on `localhost:11434`.

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

## Production Deployment

### Webhook Mode

For production, switch from polling to webhook:

```env
WEBHOOK_URL=https://your-domain.com
WEBHOOK_PORT=8443
WEBHOOK_SECRET=your_secret
```

Supported webhook ports: 443, 80, 88, 8443.

### Systemd Service

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
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## BotFather Command List

Set these in BotFather via `/setcommands` for autocomplete suggestions:

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

## Contributing

Contributions are welcome! Here's how to get started:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run a lint/import check: `python3 -m bot` (will fail due to missing BOT_TOKEN but imports must succeed)
5. Commit: `git commit -m "Add my feature"`
6. Push: `git push origin feature/my-feature`
7. Open a Pull Request

### Guidelines

- Keep the modular structure under `bot/`
- Optional dependencies should be conditionally imported with graceful fallbacks
- All user-facing text should use HTML-safe output (`escape()`)
- Commands and features should be self-documenting via `/start`

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

## Acknowledgments

- [Ollama](https://ollama.ai) for local LLM serving
- [python-telegram-bot](https://python-telegram-bot.org/) for the Telegram framework
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) for lightweight transcription
- [DuckDuckGo Search](https://pypi.org/project/duckduckgo-search/) for web search
