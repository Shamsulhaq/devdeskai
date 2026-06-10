import json
import re

from bot import ollama

PRD_SYSTEM_PROMPT = (
    "You are a product manager creating a detailed Product Requirements Document (PRD). "
    "Cover: project overview, target users, core features with acceptance criteria, "
    "technical requirements, user stories, and success metrics. "
    "Be specific and actionable."
)

VERIFY_SYSTEM_PROMPT = (
    "You are a QA lead reviewing a verification report from a code review. "
    "Decide whether the code passes or needs fixes. "
    "Output ONLY valid JSON in one of these formats:\n"
    '{"verdict": "pass", "explanation": "<why it passes>"}\n'
    '{"verdict": "fail", "issues": "<what needs to be fixed>"}'
)

ROOT_CAUSE_SYSTEM_PROMPT = (
    "You are a senior debugging engineer. Analyze why verification failed "
    "and determine the root cause and fix approach. "
    "Output ONLY valid JSON:\n"
    '{"root_cause": "<actual root cause>", '
    '"approach": "<specific fix strategy>"}'
)

ROUTING_SYSTEM_PROMPT = (
    "You are a routing expert for a multi-agent build system. "
    "Given a task, available agents, their usage limits, and remaining capacity, "
    "pick the best agent for the job. Consider:\n"
    "1. Task type (coding → opencode, tests → codex, review → claude)\n"
    "2. Remaining capacity (prefer agents with more headroom)\n"
    "3. Agent reliability (avoid agents with recent errors)\n\n"
    "Output ONLY valid JSON:\n"
    '{"choice": "<agent_name>", "reason": "<one-line explanation>", '
    '"confidence": "high|medium|low"}'
)

SUMMARY_SYSTEM_PROMPT = (
    "You are a project manager creating a brief completion summary. "
    "Summarize what was built, the tech used, and key results. Keep it concise."
)


async def generate_prd(task: str, user_id: int) -> str:
    return await ollama.generate(
        user_id,
        f"Create a detailed PRD for: {task}",
        system=PRD_SYSTEM_PROMPT,
    )


async def evaluate_verification(verify_report: str, user_id: int) -> dict:
    response = await ollama.generate(
        user_id,
        f"Evaluate this verification report and decide pass or fail:\n\n{verify_report}",
        system=VERIFY_SYSTEM_PROMPT,
    )
    return _extract_json(response, {"verdict": "fail", "issues": "Could not parse brain response"})


async def analyze_root_cause(
    verify_report: str,
    files: dict[str, str],
    previous_attempts: list[dict],
    user_id: int,
) -> dict:
    files_section = "\n\n".join(
        f"--- {path} ---\n{content[:2000]}" for path, content in files.items()
    )
    attempts_section = ""
    if previous_attempts:
        attempts_section = "\n\nPrevious fix attempts:\n" + "\n".join(
            f"Attempt {a['cycle']}: {a.get('issues', 'unknown')[:200]}"
            for a in previous_attempts[-3:]
        )
    prompt = (
        f"Verification report:\n{verify_report[:3000]}\n\n"
        f"Project files:\n{files_section[:4000]}{attempts_section}\n\n"
        "What is the root cause and how should we fix it?"
    )
    response = await ollama.generate(user_id, prompt, system=ROOT_CAUSE_SYSTEM_PROMPT)
    return _extract_json(response, {
        "root_cause": "Verification failed",
        "approach": "Review and fix implementation",
    })


async def should_retry(
    cycle: int, max_cycles: int, previous_attempts: list[dict], user_id: int,
) -> bool:
    if cycle >= max_cycles:
        return False
    if len(previous_attempts) < 2:
        return True
    same_count = 0
    last_issue = previous_attempts[-1].get("issues", "")
    for a in reversed(previous_attempts[:-1]):
        if a.get("issues", "") == last_issue:
            same_count += 1
        else:
            break
    if same_count >= 2:
        response = await ollama.generate(
            user_id,
            f"The fix has been attempted {cycle + 1} times with the same issue persisting. "
            f"Issue: {last_issue[:300]}\n\n"
            "Should we try a completely different approach or give up? "
            "Output JSON: {\"continue\": true/false, \"new_approach\": \"...\"}",
            system="You are a project lead deciding whether to continue or pivot.",
        )
        result = _extract_json(response, {"continue": False})
        return result.get("continue", False)
    return True


async def generate_summary(wf, user_id: int) -> str:
    artifacts_text = "\n".join(f"- {a.name}" for a in wf.artifacts)
    return await ollama.generate(
        user_id,
        f"Project task: {wf.task}\n\nFiles created:\n{artifacts_text}\n\nCreate a brief completion summary.",
        system=SUMMARY_SYSTEM_PROMPT,
    )


async def pick_best_agent(
    task: str, agents: list[str], fallback_order: list[str],
    status: dict, user_id: int,
) -> str:
    """Brain picks the best agent considering limits and task type."""
    status_lines = "\n".join(
        f"  {a}: {'✅' if s['available'] else '❌'} "
        f"reqs={s['requests']}/{s['limit_rph']} "
        f"errors={s['errors']} "
        f"{'DEAD' if s['dead'] else 'alive'}"
        for a, s in status.items() if a in agents
    )
    prompt = (
        f"Task (first 500 chars):\n{task[:500]}\n\n"
        f"Available agents:\n{status_lines}\n\n"
        f"Preferred fallback order: {', '.join(fallback_order[:5])}\n"
        "Pick the best agent for this task. Output JSON only."
    )
    response = await ollama.generate(user_id, prompt, system=ROUTING_SYSTEM_PROMPT)
    result = _extract_json(response, {"choice": agents[0] if agents else "unknown"})
    chosen = result.get("choice", agents[0] if agents else "unknown")
    if chosen not in agents:
        chosen = agents[0]
    return chosen


def format_code_prompt(prd: str) -> str:
    return (
        "Build a complete, runnable project based on this PRD. "
        "Output ALL files using one of these formats:\n\n"
        "Format A (preferred):\n"
        "--- index.html ---\n"
        "<!DOCTYPE html><html>...\n"
        "--- style.css ---\n"
        "body { ... }\n\n"
        "Format B:\n"
        "```html\n"
        "<!DOCTYPE html><html>...\n"
        "```\n"
        "```css\n"
        "body { ... }\n"
        "```\n\n"
        "Include ALL code — full implementations, no placeholders. "
        "Cover: HTML, CSS, JavaScript, config files. "
        "Make it complete and runnable.\n\n"
        f"PRD:\n{prd}"
    )


def format_tests_prompt(prd: str) -> str:
    return (
        "Write comprehensive test cases for this project. "
        "Output ALL test files using one of these formats:\n\n"
        "Format A (preferred):\n"
        "--- tests/test_app.js ---\n"
        "test('...', () => { ... });\n\n"
        "Format B:\n"
        "```javascript\n"
        "test('...', () => { ... });\n"
        "```\n\n"
        "Include unit tests, integration tests, edge cases. "
        "Tests must match the implementation.\n\n"
        f"PRD:\n{prd}"
    )


def format_verify_prompt(files: dict[str, str]) -> str:
    sections = []
    for path, content in files.items():
        sections.append(f"--- {path} ---\n{content[:3000]}")
    body = "\n\n".join(sections)
    return (
        "Review these project files carefully. Check function signatures match, "
        "tests align with implementation, imports resolve, and the project is coherent. "
        "End with PASS or FAIL on its own line.\n\n"
        f"{body}"
    )


def format_fix_prompt(
    root_cause: str, approach: str, files: dict[str, str], previous_attempts: list[dict],
) -> str:
    files_section = "\n\n".join(
        f"--- {path} ---\n{content[:3000]}" for path, content in files.items()
    )
    attempts = ""
    if previous_attempts:
        attempts = "\n\nPrevious attempts:\n" + "\n".join(
            f"  Attempt {a['cycle']}: {a.get('approach', 'fix')[:200]}"
            for a in previous_attempts[-3:]
        )
    return (
        f"Root cause: {root_cause}\n"
        f"Approach: {approach}{attempts}\n\n"
        f"Current files:\n{files_section[:5000]}\n\n"
        "Output the FIXED files using the same marker format:\n"
        "--- filename.ext ---\n"
        "fixed content\n\n"
        "Output ALL files that need changes, each with its marker."
    )


def format_doc_prompt(file_list: list[str], files: dict[str, str]) -> str:
    files_str = "\n".join(f"  - {f}" for f in file_list)
    code_section = "\n\n".join(
        f"--- {path} ---\n{content[:1500]}" for path, content in list(files.items())[:5]
    )
    return (
        "Write a complete README.md for this project. "
        "Start with '--- README.md ---' on its own line, then the content.\n\n"
        f"Project files:\n{files_str}\n\n"
        f"Key code:\n{code_section}\n\n"
        "Include: project description, setup, usage, structure."
    )


def parse_file_markers(text: str) -> dict[str, str]:
    """Parse agent output into {filename: content} dict.

    Tries multiple strategies in order:
      1. --- filename.ext --- markers
      2. ```language / ``` code blocks
      3. JSON {"filename": "content"} format
      4. Content-type detection (HTML/JS/CSS)
      5. Fallback: save entire output as raw_output.txt
    """
    files = {}

    # Pattern 1: --- filename.ext ---
    marker_pat = re.compile(r"^---\s+(.+?)\s+---\s*$", re.MULTILINE)
    markers = list(marker_pat.finditer(text))
    if len(markers) >= 1:
        for i, m in enumerate(markers):
            name = m.group(1).strip()
            start = m.end()
            end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            content = text[start:end].strip()
            if content:
                files[name] = content
        if files:
            return files

    # Pattern 2: ``` language / ``` code blocks
    bt_pat = re.compile(r"^```(\S*)\s*$", re.MULTILINE)
    bts = list(bt_pat.finditer(text))
    if len(bts) >= 2:
        for i in range(0, len(bts) - 1, 2):
            lang = bts[i].group(1).strip() or f"file_{i // 2}"
            start = bts[i].end()
            end = bts[i + 1].start()
            content = text[start:end].strip()
            if content:
                name = _lang_to_filename(lang, i // 2)
                files[name] = content
        if files:
            return files

    # Pattern 3: JSON {"filename": "content", ...}
    json_match = re.search(r"\{[^{}]*\"[^\"]+\"[^{}]*\"[^\"]+\"[^{}]*\}", text, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if isinstance(v, str) and v.strip():
                        files[str(k)] = v
                if files:
                    return files
        except json.JSONDecodeError:
            pass

    # Pattern 4: detect by content type
    text_stripped = text.strip()
    if re.search(r"<!DOCTYPE html|<\s*html[^>]*>", text_stripped, re.IGNORECASE):
        files["index.html"] = text_stripped
        return files
    if re.search(r"<\s*style[^>]*>|[\w\.\-]+\s*\{[^}]*\}", text_stripped):
        files["style.css"] = text_stripped
        return files
    if re.search(r"function\s+\w+\s*\(|const\s+\w+|let\s+\w+|document\.", text_stripped):
        files["script.js"] = text_stripped
        return files
    if re.search(r"import\s+|export\s+|from\s+['\"]", text_stripped):
        files["app.js"] = text_stripped
        return files
    if re.search(r"describe\(|it\(|test\(|expect\(|assert\.", text_stripped):
        files["tests.js"] = text_stripped
        return files
    if text_stripped.startswith("# ") or text_stripped.startswith("## "):
        files["README.md"] = text_stripped
        return files

    # Pattern 5: fallback — entire output as one file
    files["raw_output.txt"] = text_stripped
    return files


def _lang_to_filename(lang: str, index: int) -> str:
    ext_map = {
        "html": "index.html", "htm": "index.html",
        "css": "style.css",
        "js": "script.js", "javascript": "script.js",
        "py": "main.py", "python": "main.py",
        "json": "data.json",
        "md": "README.md", "markdown": "README.md",
        "sh": "run.sh", "bash": "run.sh",
        "yaml": "config.yaml", "yml": "config.yaml",
        "toml": "config.toml",
        "txt": "output.txt",
    }
    return ext_map.get(lang.lower(), f"file_{index}.{lang}") if lang else f"file_{index}.txt"


def _extract_json(text: str, fallback: dict) -> dict:
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return fallback
