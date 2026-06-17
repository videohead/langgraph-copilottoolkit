/**
 * CopilotKit Runtime endpoint.
 *
 * This route acts as the CopilotKit backend. It creates HttpAgent instances
 * that point at the Django AG-UI endpoint, so the LangGraph graphs are run
 * server-side in Django while the frontend only communicates with this
 * Next.js route (same origin, no CORS issues).
 *
 * Multi-route mode exposes:
 *   GET  /api/copilotkit/info                     — agent discovery
 *   POST /api/copilotkit/agent/:agentId/run        — run an agent (SSE)
 */
import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";
import type { NextRequest } from "next/server";

const djangoUrl =
  process.env.DJANGO_INTERNAL_URL?.replace(/\/$/, "") ?? "http://django:8080";

const runtime = new CopilotRuntime({
  agents: {
    basic: new HttpAgent({ url: `${djangoUrl}/api/agents/basic/` }),
    swarm_v1: new HttpAgent({ url: `${djangoUrl}/api/agents/swarm_v1/` }),
  },
});

const handler = createCopilotRuntimeHandler({
  runtime,
  basePath: "/api/copilotkit",
});

export async function GET(req: NextRequest) {
  return handler(req);
}

export async function POST(req: NextRequest) {
  return handler(req);
}
