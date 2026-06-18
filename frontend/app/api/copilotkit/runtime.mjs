import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";

export const djangoUrl =
  process.env.DJANGO_INTERNAL_URL?.replace(/\/$/, "") ?? "http://django:8080";

export const runtime = new CopilotRuntime({
  agents: {
    basic: new HttpAgent({ url: `${djangoUrl}/api/agents/basic/` }),
    swarm_v1: new HttpAgent({ url: `${djangoUrl}/api/agents/swarm_v1/` }),
  },
});

export const copilotkitHandler = createCopilotRuntimeHandler({
  runtime,
  basePath: "/api/copilotkit",
});

export async function handleCopilotKitRequest(req) {
  const url = new URL(req.url);

  if (req.method === "POST" && url.pathname === "/api/copilotkit") {
    try {
      const body = await req.clone().json();
      if (body?.method === "info") {
        return copilotkitHandler(
          new Request(new URL("/api/copilotkit/info", url), {
            method: "GET",
            headers: req.headers,
          }),
        );
      }
    } catch {
      // Fall through to the default handler.
    }
  }

  return copilotkitHandler(req);
}