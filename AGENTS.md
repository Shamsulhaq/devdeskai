# AGENTS.md

## Overview of Telegram Bot Agents
it's an agent manager system that can execute tasks on behalf of users by leveraging the capabilities of various agents. The system is designed to automate workflows and processes, provide personalized assistance, and enhance user engagement and satisfaction. The architecture of the agent system consists of a planner, a workflow engine, and various agents that can perform specific tasks. The planner generates a structured workflow plan based on user input, which is then executed by the workflow engine. The agents are responsible for performing the actual tasks, and the system includes mechanisms for reviewing and reworking outputs to ensure quality and accuracy. The ultimate goal of the agent system is to provide a seamless and efficient experience for users while automating complex tasks and processes.


## Goals of the agent system:
1. automate workflows and processes
2. provide personalized assistance to users
3. enhance user engagement and satisfaction

## Architecture

```
User: "/build personal website"
          │
          ▼
┌─────────────────────────────┐
│      Planner (Ollama)       │  ← Brain that decomposes task
│  Generates structured JSON  │
└──────────┬──────────────────┘
           │ Workflow Plan
           ▼
┌─────────────────────────────┐
│      Workflow Engine        │  ← Async executor in bot/workflow/
│  ┌──────────────────────┐   │
│  │   Phase Executor     │   │  ← Runs phases sequentially
│  │   ┌──────┐ ┌──────┐  │   │
│  │   │ Step │ │ Step │  │   │  ← Each step = one agent task
│  │   └──┬───┘ └──┬───┘  │   │
│  │      ▼         ▼      │   │
│  │  ┌────────┐ ┌────────┐│   │
│  │  │ Agent  │ │ Agent  ││   │  ← Routes to CLI agent
│  │  │ Claude │ │ Codex  ││   │
│  │  └───┬────┘ └───┬────┘│   │
│  │      ▼           ▼     │   │
│  │  workspace/    workspace│   │
│  └──────────────────────┘   │
│             │               │
│             ▼               │
│  ┌──────────────────────┐   │
│  │   Review & Rework    │   │  ← Validate output, loop if needed
│  │   max_retries = 2    │   │
│  └──────────────────────┘   │
│             │               │
│             ▼               │
│  ┌──────────────────────┐   │
│  │  Completion Report   │   │  ← Summary + file listing
│  └──────────────────────┘   │
└─────────────────────────────┘
          │
          ▼
    Telegram: "✅ Job done!"
```

