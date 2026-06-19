import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";

export const djangoUrl =
  process.env.DJANGO_INTERNAL_URL?.replace(/\/$/, "") ?? "http://django:8080";

export const runtime = new CopilotRuntime({
  agents: {
    // CopilotKit internals may fall back to agentId "default".
    // Map it to the basic graph so runtime sync never fails on that fallback.
    default: new HttpAgent({ url: `${djangoUrl}/api/agents/basic/` }),
    basic: new HttpAgent({ url: `${djangoUrl}/api/agents/basic/` }),
    swarm_v1: new HttpAgent({ url: `${djangoUrl}/api/agents/swarm_v1/` }),
  },
});

export const copilotkitHandler = createCopilotRuntimeHandler({
  runtime,
  basePath: "/api/copilotkit",
});

const COPILOTKIT_BASE_PATH = "/api/copilotkit";

function jsonError(status, message) {
  return new Response(JSON.stringify({ error: "invalid_request", message }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function createForwardRequest(req, targetUrl, method, body) {
  const headers = new Headers(req.headers);
  headers.delete("content-length");

  if (method === "POST") {
    headers.set("content-type", "application/json");
  }

  const init = {
    method,
    headers,
    signal: req.signal,
  };

  if (method === "POST" && body !== undefined && body !== null) {
    init.body = typeof body === "string" ? body : JSON.stringify(body);
  }

  return new Request(targetUrl, init);
}

function resolveRootMethodRoute(reqUrl, envelope) {
  const method = envelope?.method;
  const params = envelope?.params ?? {};

  if (method === "info") {
    return {
      targetPath: `${COPILOTKIT_BASE_PATH}/info`,
      httpMethod: "GET",
    };
  }

  if (method === "agent/run") {
    if (typeof params.agentId !== "string" || !params.agentId.trim()) {
      return { error: "Missing or invalid parameter 'agentId'" };
    }
    return {
      targetPath: `${COPILOTKIT_BASE_PATH}/agent/${encodeURIComponent(params.agentId)}/run`,
      httpMethod: "POST",
      body: envelope.body,
    };
  }

  if (method === "agent/connect") {
    if (typeof params.agentId !== "string" || !params.agentId.trim()) {
      return { error: "Missing or invalid parameter 'agentId'" };
    }
    return {
      targetPath: `${COPILOTKIT_BASE_PATH}/agent/${encodeURIComponent(params.agentId)}/connect`,
      httpMethod: "POST",
      body: envelope.body,
    };
  }

  if (method === "agent/stop") {
    if (typeof params.agentId !== "string" || !params.agentId.trim()) {
      return { error: "Missing or invalid parameter 'agentId'" };
    }
    if (typeof params.threadId !== "string" || !params.threadId.trim()) {
      return { error: "Missing or invalid parameter 'threadId'" };
    }
    return {
      targetPath: `${COPILOTKIT_BASE_PATH}/agent/${encodeURIComponent(params.agentId)}/stop/${encodeURIComponent(params.threadId)}`,
      httpMethod: "POST",
      body: envelope.body,
    };
  }

  if (method === "transcribe") {
    return {
      targetPath: `${COPILOTKIT_BASE_PATH}/transcribe`,
      httpMethod: "POST",
      body: envelope.body,
    };
  }

  return null;
}

export async function handleCopilotKitRequest(req) {
  const url = new URL(req.url);

  if (req.method === "POST" && url.pathname === COPILOTKIT_BASE_PATH) {
    try {
      const envelope = await req.clone().json();
      const route = resolveRootMethodRoute(url, envelope);

      if (route?.error) {
        return jsonError(400, route.error);
      }

      if (route) {
        const targetUrl = new URL(route.targetPath, url);
        const forwarded = createForwardRequest(
          req,
          targetUrl,
          route.httpMethod,
          route.body,
        );
        return copilotkitHandler(forwarded);
      }
    } catch {
      // Fall through to the default handler.
    }
  }

  return copilotkitHandler(req);
}