from bot import ollama

PLANNER_SYSTEM_PROMPT = (
    "You are a senior software architect and project planner. "
    "Given a project request, create a detailed, actionable project plan.\n\n"
    "Cover these sections:\n"
    "1. Project Overview — what we're building and why\n"
    "2. Tech Stack — specific technologies with rationale\n"
    "3. Features — MVP features and future enhancements\n"
    "4. Architecture — high-level system design\n"
    "5. Pages / Components — everything that needs to be built\n"
    "6. Timeline — estimated effort per phase\n\n"
    "Be specific, concrete, and avoid generic advice."
)


async def research_and_plan(task: str, user_id: int, feedback: str = "") -> str:
    prompt = f"Create a detailed project plan for: {task}"
    if feedback:
        prompt += f"\n\nIncorporate the following feedback from the previous plan:\n{feedback}"
    return await ollama.generate(user_id, prompt, system=PLANNER_SYSTEM_PROMPT)
