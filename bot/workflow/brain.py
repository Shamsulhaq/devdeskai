import json
import re

from bot import ollama
from bot.workflow import tech

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

PRD_WITH_TECH_STACK_PROMPT = (
    "You are a senior software architect. Create a detailed PRD with an explicit "
    "Tech Stack section.\n\n"
    "Required sections (in order):\n"
    "1. Project Overview\n"
    "2. Tech Stack — MUST specify:\n"
    "   - Language and version (e.g., Python 3.11, JavaScript ES2022)\n"
    "   - Framework and version (e.g., Flask 3.0, React 18, none)\n"
    "   - Build tool (e.g., npm, pip, cargo, go build)\n"
    "   - Test framework and exact test command (e.g., 'pytest', 'npm test')\n"
    "   - Linter/formatter (e.g., 'ruff check .', 'eslint .', none)\n"
    "3. Features — list every feature with acceptance criteria\n"
    "4. Architecture — high-level system design\n"
    "5. Pages / Components\n"
    "6. Test Plan — which test types, which files\n"
    "7. Documentation Plan\n\n"
    "Tech Stack section MUST be specific and concrete. The exact test command "
    "is required so the brain can run tests without guessing."
)

CLASSIFY_REQUEST_PROMPT = (
    "You are classifying a user request into a category. Output ONLY valid JSON:\n"
    '{"category": "<one of: code_build, code_fix, code_review, '
    'research, analysis, writing, creative, data, question, general>", '
    '"confidence": 0.0-1.0}\n\n'
    "Categories:\n"
    "- code_build: build/ develop/ create a new project/app/tool\n"
    "- code_fix: fix bugs, repair broken code\n"
    "- code_review: review existing code, audit, check for issues\n"
    "- research: explain concepts, find out about topics\n"
    "- analysis: compare, evaluate, assess, analyze data\n"
    "- writing: write articles, blog posts, essays\n"
    "- creative: poems, stories, jokes, creative content\n"
    "- data: calculate, compute, process data\n"
    "- question: simple Q&A, factual lookups\n"
    "- general: anything else\n\n"
    "If the request is about creating or building software, code_build is the best fit."
)

EXTRACT_TECH_STACK_PROMPT = (
    "Extract the tech stack from this PRD. Output ONLY valid JSON:\n"
    "{\n"
    '  "language": "<primary language>",\n'
    '  "version": "<language version if specified>",\n'
    '  "framework": "<framework name or empty>",\n'
    '  "test_cmd": "<exact test command>",\n'
    '  "lint_cmd": "<linter command or empty>",\n'
    '  "build_cmd": "<build command or empty>"\n'
    "}"
)

TEST_CONFIDENCE_PROMPT = (
    "Evaluate whether I (Ollama) can confidently run tests for this project. "
    "Consider:\n"
    "- Tech stack standard-ness (Python+pytest=high, exotic framework=low)\n"
    "- Whether tests need external services (DB, network, browser)\n"
    "- Project size and complexity\n"
    "- Whether the test command is well-known\n\n"
    "Output ONLY valid JSON:\n"
    '{"confident": 0.0-1.0, "reason": "<one-sentence explanation>"}'
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


async def generate_project_name(request: str, user_id: int) -> str:
    """Brain generates a filesystem-safe project name from the request.

    e.g., 'personal website for software engineer' → 'personal-website-software-engineer'
    """
    import re
    prompt = (
        f"Generate a short, kebab-case project name (max 4 words) for: {request}\n"
        "Output ONLY the name, no explanation. Lowercase, words separated by hyphens."
    )
    response = await ollama.generate(user_id, prompt, system="You name projects.")
    name = response.strip().split("\n")[0].strip().lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    if not name:
        name = "project"
    return name[:50]


async def classify_request(request: str, user_id: int) -> str:
    """Classify a user request as code_build / code_fix / research / writing / etc.

    Uses keyword matching first (fast), falls back to Ollama classification.
    """
    text = request.lower()
    code_keywords = ["build", "create", "develop", "make", "implement", "code", "app", "website", "project", "script", "tool", "cli", "api", "service"]
    fix_keywords = ["fix", "bug", "error", "broken", "not working", "issue", "repair"]
    review_keywords = ["review", "check code", "audit", "analyze code"]
    research_keywords = ["what is", "explain", "tell me about", "how does", "research"]
    writing_keywords = ["write", "draft", "blog", "article", "essay", "report"]
    creative_keywords = ["poem", "story", "joke", "creative", "rhyme"]
    data_keywords = ["calculate", "compute", "process data", "analyze data", "statistics"]

    if any(k in text for k in fix_keywords) and any(k in text for k in code_keywords + ["code"]):
        return "code_fix"
    if any(k in text for k in review_keywords):
        return "code_review"
    if any(k in text for k in code_keywords):
        return "code_build"
    if any(k in text for k in data_keywords):
        return "data"
    if any(k in text for k in writing_keywords):
        return "writing"
    if any(k in text for k in creative_keywords):
        return "creative"
    if any(k in text for k in research_keywords):
        return "research"

    try:
        prompt = f"Classify this request: {request}"
        response = await ollama.generate(user_id, prompt, system=CLASSIFY_REQUEST_PROMPT)
        result = _extract_json(response, {"category": "general", "confidence": 0.5})
        return result.get("category", "general")
    except Exception:
        return "general"


async def generate_prd_with_tech_stack(
    request: str, project_name: str, user_id: int
) -> str:
    """Generate a PRD with explicit Tech Stack section."""
    prompt = (
        f"Project name: {project_name}\n"
        f"User request: {request}\n\n"
        "Create the PRD. The Tech Stack section is critical — the brain will use it "
        "to run tests, so include the exact test command."
    )
    return await ollama.generate(user_id, prompt, system=PRD_WITH_TECH_STACK_PROMPT)


async def extract_tech_stack(prd: str, user_id: int) -> dict:
    """Extract tech stack dict from PRD text, validated against known stacks."""
    try:
        response = await ollama.generate(
            user_id,
            f"PRD:\n{prd[:3000]}",
            system=EXTRACT_TECH_STACK_PROMPT,
        )
        result = _extract_json(response, {})
        return tech.validate_tech_stack(result)
    except Exception:
        return {}


async def evaluate_test_confidence(
    tech_stack: dict, project_dir: str, user_id: int
) -> float:
    """Brain evaluates whether it can confidently run tests itself.

    Returns 0-1. Higher = more confident.
    """
    if not tech_stack or not tech_stack.get("test_cmd"):
        return 0.0
    if tech.has_external_services(tech_stack):
        return 0.2

    files = tech.list_project_files(project_dir, max_files=15)
    prompt = (
        f"Tech stack: {tech_stack}\n"
        f"Project files ({len(files)}): {', '.join(files[:10])}\n\n"
        "Can I (Ollama) confidently run the test command myself?"
    )
    try:
        response = await ollama.generate(user_id, prompt, system=TEST_CONFIDENCE_PROMPT)
        result = _extract_json(response, {"confident": 0.5})
        return float(result.get("confident", 0.5))
    except Exception:
        return 0.5


async def brain_run_tests(
    tech_stack: dict, project_dir: str, user_id: int
) -> dict:
    """Brain runs the test command itself and parses results.

    Returns {passed, summary, failures, exit_code, stdout, stderr}.
    """
    import asyncio
    test_cmd = tech_stack.get("test_cmd", "")
    if not test_cmd:
        return {"passed": True, "skipped": True, "summary": "No test command"}

    try:
        proc = await asyncio.create_subprocess_shell(
            test_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            return {"passed": False, "summary": "Test command timed out (120s)",
                    "exit_code": -1, "stdout": "", "stderr": "timeout"}

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        passed = proc.returncode == 0
        summary = _summarize_test_output(out, err, passed)
        return {
            "passed": passed,
            "summary": summary,
            "exit_code": proc.returncode,
            "stdout": out[-2000:],
            "stderr": err[-2000:],
        }
    except Exception as e:
        return {"passed": False, "summary": f"Test execution error: {e}",
                "exit_code": -1, "stdout": "", "stderr": str(e)}


def _summarize_test_output(stdout: str, stderr: str, passed: bool) -> str:
    """Pull the most useful lines from test output."""
    lines = []
    for line in (stdout + "\n" + stderr).splitlines():
        line = line.strip()
        if not line:
            continue
        if any(kw in line.lower() for kw in ["passed", "failed", "error", "test", "expect"]):
            lines.append(line)
        if len(lines) >= 20:
            break
    if not lines:
        lines = (stdout + stderr).strip().splitlines()[:5]
    return "\n".join(lines) or ("Tests passed" if passed else "Tests failed (no details)")


async def try_with_model_swap(
    callable_fn, models: list[str], user_id: int,
) -> str:
    """Try callable with each model in order. First success wins.

    callable_fn(model) should accept a model name and return a string result.
    A result is considered 'good' if it's non-empty and doesn't look like garbage.
    """
    from bot import persistence
    from bot import ollama as ollama_module

    original = persistence.user_models.get(user_id)
    last_result = ""
    for model in models:
        try:
            persistence.user_models[user_id] = model
            ollama_module._client = None
            result = await callable_fn(model)
            if result and not _is_low_quality(result):
                persistence.user_models[user_id] = original or model
                return result
            last_result = result or last_result
        except Exception:
            continue
    if original:
        persistence.user_models[user_id] = original
    return last_result or "All models failed"


def _is_low_quality(text: str) -> bool:
    """Heuristic to detect bad model output worth retrying."""
    if not text or not text.strip():
        return True
    if len(text.strip()) < 20:
        return True
    return False


def format_test_prompt(tech_stack: dict, project_files: list) -> str:
    """Build prompt for test agent to create test cases."""
    files_str = "\n".join(f"  - {f}" for f in project_files[:20])
    return (
        f"Tech stack: {tech_stack}\n"
        f"Test command to use: {tech_stack.get('test_cmd', 'pytest')}\n\n"
        f"Project files:\n{files_str}\n\n"
        "Create comprehensive test cases for this project. "
        "Use the specified test framework. Output test files using the same "
        "marker format as the code step: '--- filename.ext ---' on its own line."
    )


def format_fix_prompt(test_results: dict, project_files: list) -> str:
    """Build prompt for fix agent based on test failure."""
    files_str = "\n".join(f"  - {f}" for f in project_files[:20])
    return (
        "Tests failed. Analyze the failures and fix the code.\n\n"
        f"Test summary:\n{test_results.get('summary', 'unknown')}\n\n"
        f"Exit code: {test_results.get('exit_code', 'unknown')}\n\n"
        f"stderr (last 2000 chars):\n{test_results.get('stderr', '')[:2000]}\n\n"
        f"stdout (last 2000 chars):\n{test_results.get('stdout', '')[:2000]}\n\n"
        f"Project files:\n{files_str}\n\n"
        "Output the FIXED files using '--- filename.ext ---' markers. "
        "Only output files that need changes."
    )


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


async def execute_brain_step(step, user_id: int) -> str:
    """Execute a brain-sourced step (uses Ollama directly)."""
    from bot import ollama as ollama_module
    return await ollama_module.generate(user_id, step.prompt)


EVALUATE_STEP_SYSTEM_PROMPT = (
    "You are a QA reviewer evaluating task output against success criteria. "
    "Output ONLY valid JSON:\n"
    '{"verdict": "pass", "explanation": "<why it passes>"}\n'
    '{"verdict": "fail", "issues": "<what needs fixing>"}'
)


async def evaluate_step(step_id: str, output: str, criteria: str, user_id: int) -> dict:
    """Generic step evaluation gate. Returns dict with verdict and issues."""
    from bot import ollama as ollama_module
    prompt = (
        f"Step: {step_id}\n"
        f"Success criteria: {criteria}\n\n"
        f"Output to evaluate:\n{output[:4000]}\n\n"
        "Does the output meet the criteria? Output JSON only."
    )
    response = await ollama_module.generate(user_id, prompt, system=EVALUATE_STEP_SYSTEM_PROMPT)
    return _extract_json(response, {"verdict": "fail", "issues": "Could not parse evaluation"})


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
