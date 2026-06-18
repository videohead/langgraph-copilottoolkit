---
description: "Use when modifying the Next.js frontend, adding React components, changing the CopilotKit UI, updating the CopilotKit Runtime API route, changing agent registration, adding environment variables, or updating Next.js config. Covers CopilotKit v2 imports, the runtime proxy pattern, and the graph selector component."
applyTo: "frontend/**"
---

# Modifying the Next.js Frontend / CopilotKit Runtime

## Project layout

```
frontend/
  app/
    layout.tsx                       CopilotKit provider — wraps entire app
    page.tsx                         Main page: graph selector + CopilotSidebar
    globals.css                      Tailwind base + body reset
    api/
      copilotkit/
        [...path]/route.ts           CopilotKit Runtime — proxies to Django
  next.config.ts
  tailwind.config.ts
  postcss.config.js
  package.json
  Dockerfile
  .env.local.example
```

## Package imports

| Symbol | Import path |
|--------|-------------|
| `CopilotKit` (app shell provider) | `@copilotkit/react-core` |
| `CopilotChatConfigurationProvider`, `useCopilotChatConfiguration`, `useAgent` | `@copilotkit/react-core/v2/headless` |
| `CopilotSidebar`, `CopilotPopup` | `@copilotkit/react-ui` |
| UI styles | `@copilotkit/react-ui/styles.css` |
| `CopilotRuntime`, `createCopilotRuntimeHandler` | `@copilotkit/runtime/v2` |
| `HttpAgent` | `@ag-ui/client` |

Do not mix v1 and v2 import paths in the same file. In particular, avoid importing `@copilotkit/react-core/v2` from a client boundary such as `app/layout.tsx`; Next.js 15 rejects the `dist/v2/index.mjs` barrel because it uses `export *`.

Use the package-root `@copilotkit/react-core` import for the app shell provider in `app/layout.tsx`, and use `@copilotkit/react-core/v2/headless` for the agent/configuration hooks inside client components.

Known issue: the installed v2 headless subpath exposes `CopilotChatConfigurationProvider`, `useCopilotChatConfiguration`, and `useAgent`, but it does not export a top-level provider component. The app shell still needs the package-root `CopilotKit` from `@copilotkit/react-core`.

## CopilotKit provider (`app/layout.tsx`)

The provider points at the local Next.js API route — same-origin, no CORS:

```tsx
import { CopilotKit } from "@copilotkit/react-core";

<CopilotKit runtimeUrl="/api/copilotkit">{children}</CopilotKit>
```

Do not set `publicApiKey` or `selfManagedAgents` here — all agent traffic flows through the runtime route.

## CopilotKit Runtime routes (`app/api/copilotkit/route.ts` and `app/api/copilotkit/[...path]/route.ts`)

```typescript
import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";

const djangoUrl = process.env.DJANGO_INTERNAL_URL?.replace(/\/$/, "") ?? "http://django:8080";

const runtime = new CopilotRuntime({
  agents: {
    basic:    new HttpAgent({ url: `${djangoUrl}/api/agents/basic/` }),
    swarm_v1: new HttpAgent({ url: `${djangoUrl}/api/agents/swarm_v1/` }),
  },
});

const handler = createCopilotRuntimeHandler({ runtime, basePath: "/api/copilotkit" });

export async function GET(req: NextRequest) { return handler(req); }
export async function POST(req: NextRequest) { return handler(req); }
```

The frontend now uses a shared runtime helper so both routes stay in sync:

- `app/api/copilotkit/route.ts` handles the single-endpoint CopilotKit handshake (`POST /api/copilotkit` with `{ method: "info" }`).
- `app/api/copilotkit/[...path]/route.ts` handles REST-style subpaths such as `/info` and the agent run paths.

If the browser shows `runtime_info_fetch_failed`, verify that the root route exists and that the frontend dev server was restarted after any `.next` cleanup.

- `DJANGO_INTERNAL_URL` is a Docker-internal URL (`http://django:8080`). It is **never** exposed to the browser.
- Both `GET` and `POST` must be exported — `GET /info` is used for agent discovery.
- The catch-all route `[...path]` captures all sub-paths under `/api/copilotkit/`.

## Adding an agent to the frontend

When a new graph is added (see `add-graph.instructions.md`), register a new `HttpAgent` in the runtime route AND add an entry to the `GRAPHS` array in `page.tsx`. The `id` field must match the key in `CopilotRuntime.agents`.

## Environment variables

| Variable | Where set | Purpose |
|----------|-----------|---------|
| `DJANGO_INTERNAL_URL` | Compose/Lando env, `.env.local` | Django URL seen by Next.js server |
| `NEXT_TELEMETRY_DISABLED` | Dockerfile, Compose env | Disable Next.js telemetry |
| `WATCHPACK_POLLING` | Dockerfile, Compose env | File-watching in Docker (keep `"true"`) |

Never prefix internal service URLs with `NEXT_PUBLIC_` — that would expose them to the browser bundle.

## Adding a frontend npm dependency

```bash
lando npm install <package>
# then rebuild so the Dockerfile layer is updated:
lando rebuild -y
```

After `lando npm install` the new package is only in the volume-mounted `node_modules`. Run `lando rebuild -y` so the next fresh container build includes it in the image layer.

## Frontend tests

The frontend has a lightweight Node test script in `frontend/package.json`:

```bash
cd frontend
npm test
```

Current coverage lives in `frontend/tests/copilotkit-runtime.test.mjs`, which checks both `POST /api/copilotkit` and `GET /api/copilotkit/info`.

## Tailwind

Tailwind v3 with PostCSS. Config: `frontend/tailwind.config.ts`.  
Content paths: `./app/**/*.{ts,tsx}` and `./components/**/*.{ts,tsx}`.  
Add `components/` to content paths if you create a `frontend/components/` directory.

## Next.js config notes

`next.config.ts` sets `output: "standalone"` (for production images) and enables server actions.  
Do not set `output: "export"` — the app uses server-side API routes.
