---
description: "Use when modifying Docker or Lando service definitions, changing Dockerfiles, updating port mappings, adding volumes, changing environment variables in compose, adding a new service, or debugging container networking. Covers build context rules, the Lando API 3 service format, internal DNS, and the anonymous node_modules volume pattern."
applyTo: "{docker-compose.yml,.lando.yml,**/Dockerfile}"
---

# Modifying Docker / Lando Service Definitions

## Service map

| Service | Definition | Build context | Internal port |
|---------|-----------|---------------|---------------|
| `postgres` | Lando + Compose | prebuilt image | 5432 |
| `redis` | Lando + Compose | prebuilt image | 6379 |
| `mcp-filesystem` | Lando + Compose | repo root `.` | 8765 |
| `ollama` | Lando + Compose | prebuilt image | 11434 |
| `appserver` / `langgraph` | Lando + Compose | repo root `.` | 8000 |
| `django` | Lando + Compose | repo root `.` | 8080 |
| `frontend` | Lando + Compose | `./frontend` | 3000 |
| `charts` | Lando + Compose | prebuilt image | 80 |

## Required sync updates when adding a service

When adding or renaming any service in `docker-compose.yml` or `.lando.yml`, also update all of the following in the same change:

1. Runtime stack config:
  - `docker-compose.yml` service definition, healthcheck/dependencies, volumes, and env wiring.
  - `.lando.yml` service definition, proxy URL, and tooling commands.
2. User-facing docs:
  - `readme.md` architecture diagram and the "Additional services" table.
3. User-facing services map API:
  - `frontend/app/api/services/data.mjs` in all three places:
    - `DEFAULT_ENDPOINTS`
    - `PUBLIC_LOCATIONS`
    - `services` array in `buildServicesDashboardData()`

Minimum expected entries include both durable and coordination stores (`postgres` and `redis`) so they appear in docs and in the services dashboard.

## Build context rules

### Django — build context MUST be repo root

`django/Dockerfile` COPYs from both `django/` and `src/`:

```dockerfile
COPY django/requirements.txt ./requirements.txt
COPY src/ ./src/
COPY django/ .
```

In `docker-compose.yml`:
```yaml
django:
  build:
    context: .              # repo root
    dockerfile: django/Dockerfile
```

In `.lando.yml`:
```yaml
django:
  services:
    build:
      context: .            # repo root
      dockerfile: django/Dockerfile
```

**Never** set `context: ./django` — the Dockerfile will fail to find `src/`.

### Frontend — build context is `./frontend`

`frontend/Dockerfile` only needs files inside `frontend/`:

```yaml
frontend:
  build:
    context: ./frontend
    dockerfile: Dockerfile
```

## Volume mount patterns

### Hot-reload: override image files with host source

```yaml
volumes:
  - ./django:/app           # Django source
  - ./src:/app/src:ro       # shared graphs (read-only)
```

```yaml
volumes:
  - ./frontend:/app         # Next.js source
  - /app/node_modules       # anonymous volume — preserves image's node_modules
```

The anonymous `/app/node_modules` volume is essential for the frontend. Without it, the host mount would shadow the container's installed packages with an empty host directory.

### `src/` is always `:ro` inside Django

Never mount `src/` read-write into the Django container. The graphs are Python library code; Django should not write to them.

## Environment variables

Internal service URLs use Docker/Lando DNS — not `localhost`:

```yaml
# Correct (Compose)
OLLAMA_BASE_URL: http://ollama:11434
DJANGO_INTERNAL_URL: http://django:8080

# Correct (Lando — service names resolve as <name>.<appname>.internal)
OLLAMA_BASE_URL: http://ollama.langgraph.internal:11434
DJANGO_INTERNAL_URL: http://django.langgraph.internal:8080
```

`OLLAMA_MODEL` and `OLLAMA_BASE_URL` must be set on both `langgraph`/`appserver` and `django` — both services run graphs against Ollama.

## Lando service format (API 3)

All services use `api: 3` with `type: lando`. The `services:` block is a standard Docker Compose service definition:

```yaml
myservice:
  api: 3
  type: lando
  app_mount: false          # disable Lando's default app volume mount
  services:
    build:
      context: .
      dockerfile: myservice/Dockerfile
    command: my-start-command
    environment:
      MY_VAR: value
    ports:
      - "8090"              # expose to Lando's internal network (no host binding needed)
    volumes:
      - ./myservice:/app
  moreHttpPorts:
    - 8090                  # tell Lando to proxy this port
```

Add a proxy entry to expose it via `.lndo.site`:

```yaml
proxy:
  myservice:
    - myservice.langgraph.lndo.site:8090
```

Add tooling to run commands in the container:

```yaml
tooling:
  myservice-cmd:
    service: myservice
    cmd: my-binary
    description: "Run my-binary in the myservice container"
```

## Adding a Python dependency (Django)

1. Add to `django/requirements.txt`
2. `lando rebuild -y` or `docker compose build django`

Do not `pip install` in a running container — it won't persist.

## Adding a Node.js dependency (frontend)

1. `lando npm install <pkg> --save` (updates `package.json` in the volume-mounted source)
2. `lando rebuild -y` or `docker compose build frontend` (bakes it into the image layer)

## Healthchecks

`ollama` has a healthcheck; `django` has one too. The `frontend` service uses `depends_on: django: condition: service_healthy`. If you change the Django port, update the healthcheck URL accordingly:

```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -sf http://localhost:8080/api/health/ || exit 1"]
```

## Ports exposed to the host

| Host port | Service | Notes |
|-----------|---------|-------|
| 3000 | frontend | Next.js dev server |
| 5432 | postgres | Durable checkpoint storage |
| 6379 | redis | Orchestration/state coordination cache |
| 8080 | django | Django/uvicorn |
| 8765 | mcp-filesystem | MCP filesystem server |
| 8123 | langgraph | LangGraph dev server (maps container :8000) |
| 8124 | charts | nginx static |
| 11434 | ollama | Ollama API |

When using Lando, host ports are assigned dynamically — use `lando info` to find them. The `.lndo.site` proxy URLs are stable.

## Shell mapping (agent + user safety)

Do not run project runtime commands in the host shell. Use a service shell.

| Area | Lando shell | Docker shell |
|------|-------------|--------------|
| Frontend | `lando ssh -s frontend` | `docker exec -it langgraph-frontend sh` |
| Django | `lando ssh -s django` | `docker exec -it langgraph-django sh` |
| LangGraph appserver | `lando ssh -s appserver` | `docker exec -it langgraph-dev sh` |
| Postgres | `lando ssh -s postgres` | `docker exec -it langgraph-postgres sh` |
| Redis | `lando ssh -s redis` | `docker exec -it langgraph-redis sh` |
| Ollama | `lando ssh -s ollama` | `docker exec -it ollama sh` |
| MCP filesystem | `lando ssh -s mcp-filesystem` | `docker exec -it langgraph-mcp-filesystem sh` |
| Charts | `lando ssh -s charts` | `docker exec -it langgraph-charts sh` |

Use `lando ssh -s <service> -c "<cmd>"` (or `docker exec -it <container> sh -lc "<cmd>"`) for one-off commands.
