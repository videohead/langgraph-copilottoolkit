---
description: "Use when modifying the Django API, adding new REST endpoints, changing AG-UI streaming behaviour, updating CORS settings, changing Django settings, or debugging the agent SSE stream. Covers the AG-UI protocol, StreamingHttpResponse pattern, URL routing, and Django ASGI setup."
applyTo: "django/**"
---

# Modifying the Django AG-UI Layer

## Project layout

```
django/
  manage.py
  langgraph_api/        Django project package
    settings.py
    urls.py             root URLconf — includes agents.urls
    asgi.py             ASGI entry point (uvicorn)
    wsgi.py             WSGI entry point (unused in dev)
  agents/
    views.py            ALL endpoint logic lives here
    urls.py             URL patterns for agents app
```

## Running Django locally (inside the container)

```bash
lando django shell                # interactive shell
lando django check                # system check
lando python manage.py <cmd>      # arbitrary management commands
```

## AG-UI SSE protocol

Every response from `run_agent` is a `text/event-stream` with newline-delimited JSON events. Required shape per event:

```
data: {"type": "<EVENT_TYPE>", ...fields}\n\n
```

### Required event sequence

```
RUN_STARTED      {"type","threadId","runId"}
  TEXT_MESSAGE_START   {"type","messageId","role":"assistant"}
  TEXT_MESSAGE_CONTENT {"type","messageId","delta":"<token>"}   ← repeat per token
  TEXT_MESSAGE_END     {"type","messageId"}
  ... repeat for each agent node ...
RUN_FINISHED     {"type","threadId","runId"}
```

On error, emit `RUN_ERROR` instead of `RUN_FINISHED`:

```json
{"type": "RUN_ERROR", "threadId": "...", "runId": "...", "message": "<error text>"}
```

**Never** invent custom event types — CopilotKit's `HttpAgent` only parses the types above.

## `run_agent` view pattern

```python
@csrf_exempt
@require_POST
def run_agent(request, graph_name: str):
    body = json.loads(request.body)
    thread_id = body.get("threadId") or str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    lc_messages = _parse_messages(body.get("messages", []))

    def stream_events():
        yield _sse({"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id})
        # ... graph.stream() loop ...
        yield _sse({"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id})

    response = StreamingHttpResponse(stream_events(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"   # prevents nginx buffering the SSE stream
    return response
```

`StreamingHttpResponse` with a generator works under uvicorn (ASGI). Do not use `HttpResponse` for streaming — it buffers the entire body.

## Adding a new REST endpoint

Add to `django/agents/urls.py`:

```python
path("api/my-endpoint/", views.my_view),
```

Add the view to `django/agents/views.py`. Use `@require_GET` / `@require_POST` decorators.  
Always add `@csrf_exempt` on POST views — the CopilotKit runtime sends no CSRF token.

## CORS

Configured in `django/langgraph_api/settings.py`:

```python
CORS_ALLOW_ALL_ORIGINS = DEBUG          # True in dev, False in prod
CORS_ALLOWED_ORIGINS = [                # populate via CORS_ALLOWED_ORIGINS env var in prod
    o for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o
]
```

`CorsMiddleware` must remain the **first** middleware in `MIDDLEWARE`. Do not reorder it.

## Adding a Python dependency

1. Add to `django/requirements.txt`
2. Rebuild: `lando rebuild -y` (or `docker compose build django`)

Do not `pip install` directly into a running container — changes will be lost on restart.

## LangGraph imports inside Django

`src/` is on `PYTHONPATH=/app` (set in the Dockerfile and compose environment).  
Import graphs as: `from src.<module>.graph import graph as _name`

`src/` is volume-mounted `:ro` — do not write files from Django into that path.

## MCP shell policy boundaries

If shell tools are enabled for graphs, keep command allowlist enforcement in the `mcp-shell` service itself.

- Django/profile policy may add higher-level gating (for example, profile-level shell enable/disable).
- Django policy is not a substitute for execution-layer allowlist checks.
- Do not treat Postgres persistence as the primary allowlist source unless you also implement explicit reload/sync semantics in `mcp-shell`.

## ASGI / uvicorn

Django runs under uvicorn via:
```
uvicorn langgraph_api.asgi:application --host 0.0.0.0 --port 8080 --reload
```

`--reload` watches for file changes when source is volume-mounted. Do not remove it in the dev compose configuration.  
For production, remove `--reload` and add `--workers <N>`.
