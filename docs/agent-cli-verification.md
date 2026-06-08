# Agent CLI Invocation Verification (OPUS-007)

Research-only document. **Do not edit `bot/agents.py::AGENTS_CONFIG` based on this
table without first verifying against the installed binary** (`<cmd> --help`).
Each CLI is rapidly evolving and flag names may drift.

The bot wraps each entry as:

```python
full_cmd = f'{info["run_cmd"]} "{prompt}"'
```

So `run_cmd` is concatenated with a quoted prompt string. Any agent whose
correct invocation needs the prompt elsewhere (stdin, a flag, a subcommand)
will silently misbehave with the current bare-binary configs.

## Comparison table

| agent name | current `run_cmd` | proposed `run_cmd` | confidence | source URL |
| --- | --- | --- | --- | --- |
| claude   | `claude -p`   | `claude -p`   | high   | https://code.claude.com/docs/en/cli-reference |
| opencode | `opencode`    | `opencode run` | high  | https://opencode.ai/docs/cli/ |
| codex    | `codex`       | `codex exec`  | high   | https://developers.openai.com/codex/noninteractive |
| qwen     | `qwen`        | `qwen -p`     | high   | https://github.com/QwenLM/qwen-code |
| gemini   | `gemini`      | `gemini -p`   | high   | https://github.com/google-gemini/gemini-cli |
| copilot  | `copilot`     | `copilot -p`  | medium | https://docs.github.com/en/copilot/concepts/agents/about-copilot-cli |

## Per-agent notes

### claude (Anthropic Claude Code)
- The CLI reference documents `claude -p "query"` as "Query via SDK, then exit".
- Current config is already correct.

### opencode
- Docs state: `opencode run` "Run opencode in non-interactive mode by passing a
  prompt directly." Example: `opencode run "Explain how closures work in JavaScript"`.
- Bare `opencode` launches the interactive TUI.
- A `--prompt` flag exists on the default `tui` command but that opens the UI;
  the right one-shot is the `run` subcommand.

### codex (OpenAI Codex CLI)
- Docs state: `codex exec "<prompt>"` is the non-interactive form. "Codex
  streams progress to stderr and prints only the final agent message to stdout."
- Bare `codex` launches the interactive session.
- Caveat: codex `exec` requires running inside a Git repo unless
  `--skip-git-repo-check` is passed. The bot's per-user `workspace/<uid>/`
  directory is not a git repo by default, so the bot may also need to add
  `--skip-git-repo-check` (or `git init` the workspace) for codex to work
  end-to-end. Flag verification against the installed binary required.

### qwen (Qwen Code, Alibaba)
- README documents headless mode as `qwen -p "your question"`.
- No `--prompt` long form is shown in the README.

### gemini (Google gemini-cli)
- README documents `gemini -p "..."` for non-interactive use.
- No `--prompt` long form is shown.

### copilot (GitHub Copilot CLI)
- Docs: "To use the CLI programmatically, include the `-p` or `--prompt`
  command-line option." Example: `copilot -p "Show me this week's commits ..."`.
- Confidence is medium because most non-trivial Copilot CLI tasks need an
  approval flag (e.g. `--allow-tool='shell(git)'`, `--allow-all-tools`)
  to do anything beyond text generation. Without an allow flag the agent
  may prompt for approval and hang in a headless context.

## Verification checklist (for a human)

For each agent, before swapping into `AGENTS_CONFIG`:

1. Run `which <agent>` to confirm the binary is installed.
2. Run `<agent> --help` and confirm the documented flag/subcommand exists in
   the installed version.
3. Run the proposed command with a trivial prompt
   (e.g. `claude -p "say hi"`) and confirm it exits non-zero on
   misconfiguration vs. zero on success.
4. For `codex`, additionally verify whether the workspace needs to be a git
   repo or whether `--skip-git-repo-check` should be appended.
5. For `copilot`, decide on a default approval policy and whether to append
   `--allow-all-tools` (security trade-off — review carefully before enabling
   in a multi-user bot).
