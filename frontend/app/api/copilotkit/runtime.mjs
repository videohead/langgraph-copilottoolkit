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

function readCookieValue(cookieHeader, name) {
  if (!cookieHeader) {
    return null;
  }
  const parts = cookieHeader.split(";");
  for (const part of parts) {
    const trimmed = part.trim();
    if (trimmed.startsWith(`${name}=`)) {
      return decodeURIComponent(trimmed.slice(name.length + 1));
    }
  }
  return null;
}

function tryParseJson(value) {
  if (!value) {
    return null;
  }
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function injectProjectProfileContext(req, requestBody) {
  if (!requestBody || typeof requestBody !== "object") {
    return requestBody;
  }

  const messages = Array.isArray(requestBody.messages) ? requestBody.messages : [];
  const cookieHeader = req.headers.get("cookie") ?? "";
  const rawProfile = readCookieValue(cookieHeader, "copilot_project_profile");
  const profile = tryParseJson(rawProfile);

  if (!profile || !profile.id || !profile.name) {
    return requestBody;
  }

  const marker = `[project-profile:${profile.id}]`;
  const hasMarker = messages.some(
    (m) => m?.role === "system" && typeof m?.content === "string" && m.content.includes(marker),
  );
  if (hasMarker) {
    return requestBody;
  }

  const systemText = [
    `${marker}`,
    `Project profile: ${profile.name}`,
    `Description: ${profile.description ?? ""}`,
    `Filesystem root: ${profile.mcp_root ?? "/workspace-data"}`,
    `Tool mode: ${profile.tool_mode ?? "read_only"}`,
  ].join("\n");

  return {
    ...requestBody,
    messages: [{ role: "system", content: systemText }, ...messages],
  };
}

function resolveRootMethodRoute(req, envelope) {
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
      body: injectProjectProfileContext(req, envelope.body),
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
      const route = resolveRootMethodRoute(req, envelope);

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