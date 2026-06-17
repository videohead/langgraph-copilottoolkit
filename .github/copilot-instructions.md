# LangGraph + CopilotKit Project Guidelines

## Architecture

Four services communicate over a shared Docker/Lando network:

```
Browser → frontend:3000 (Next.js + CopilotKit UI)
            → /api/copilotkit/* (CopilotKit Runtime — Next.js API route, server-side)
                → django:8080 (Django AG-UI SSE endpoint)
                    → ollama:11434 (Ollama LLM)
```

The `appserver` (LangGraph dev server on :8000/:8123) is a secondary entry point used only by the CLI scripts and direct API calls — the browser UI routes exclusively through Django.

## Service Map

| Directory | Language | Role |
|-----------|----------|------|
| `src/` | Python | LangGraph graph definitions — imported directly by Django |
| `django/` | Python 3.12 | AG-UI streaming API (`/api/agents/<id>/`), health, graph list |
| `frontend/` | TypeScript/Next.js 15 | CopilotKit UI + runtime proxy to Django |
| Root `Dockerfile` | — | LangGraph dev server (`langgraph dev`) |

## Build Commands

```bash
# Start everything
lando start                          # or: docker compose up --build

# Pull Ollama model (first run)
lando pull-model

# Rebuild after dependency changes
lando rebuild -y                     # or: docker compose build

# Django management
lando django migrate
lando django shell

# Frontend
lando npm install
lando npm run build
```

## Key Conventions

### Adding a new LangGraph graph
See `.github/instructions/add-graph.instructions.md`.

### Modifying the Django AG-UI layer
See `.github/instructions/django-api.instructions.md`.

### Modifying the Next.js frontend or CopilotKit runtime
See `.github/instructions/frontend.instructions.md`.

### Changing Docker/Lando service definitions
See `.github/instructions/docker-lando.instructions.md`.

## Critical Constraints

- **Django build context is always the repo root** (`context: .`). Its Dockerfile references `django/requirements.txt` and `src/` from the root. Never change the build context to `./django`.
- **`src/` is read-only inside Django**. Volume-mounted as `:ro` in dev. Do not write graph state to disk from within `src/`.
- **No CopilotKit public API key is required** — the runtime runs fully local, proxying to Django via `DJANGO_INTERNAL_URL` (an internal Docker DNS name, never exposed to the browser).
- **Ollama model name** is controlled by the `OLLAMA_MODEL` environment variable (default: `qwen2.5-coder:7b`). Change it in `.env` or the compose/lando environment block, not in Python source.
- **AG-UI event types** are uppercase strings: `RUN_STARTED`, `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`, `RUN_FINISHED`, `RUN_ERROR`. Never invent new types.
