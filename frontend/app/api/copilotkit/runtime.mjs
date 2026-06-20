import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";

export const djangoUrl =
  process.env.DJANGO_INTERNAL_URL?.replace(/\/$/, "") ?? "http://django:8080";

const FALLBACK_GRAPH_IDS = ["basic", "swarm_v1"];
const RUNTIME_CACHE_TTL_MS = Number(process.env.COPILOTKIT_RUNTIME_CACHE_MS ?? 5000);
const DISCOVERY_TIMEOUT_MS = Number(process.env.COPILOTKIT_DISCOVERY_TIMEOUT_MS ?? 800);

let _cached = {
  expiresAt: 0,
  handler: null,
  graphIds: [],
};

function normalizeGraphIds(payload) {
  if (!payload || !Array.isArray(payload.graphs)) {
    return [];
  }
  return payload.graphs
    .map((g) => (g && typeof g.id === "string" ? g.id.trim() : ""))
    .filter((id) => id.length > 0);
}

async function loadGraphIdsFromDjango() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DISCOVERY_TIMEOUT_MS);
  try {
    const response = await fetch(`${djangoUrl}/api/graphs/`, {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      return FALLBACK_GRAPH_IDS;
    }
    const payload = await response.json();
    const dynamicIds = normalizeGraphIds(payload);
    return dynamicIds.length > 0 ? dynamicIds : FALLBACK_GRAPH_IDS;
  } catch {
    return FALLBACK_GRAPH_IDS;
  } finally {
    clearTimeout(timeout);
  }
}

function buildRuntimeAndHandler(graphIds) {
  const ids = graphIds.length > 0 ? graphIds : FALLBACK_GRAPH_IDS;
  const defaultGraphId = ids.includes("basic") ? "basic" : ids[0];

  const agents = {
    default: new HttpAgent({ url: `${djangoUrl}/api/agents/${encodeURIComponent(defaultGraphId)}/` }),
  };

  for (const graphId of ids) {
    agents[graphId] = new HttpAgent({
      url: `${djangoUrl}/api/agents/${encodeURIComponent(graphId)}/`,
    });
  }

  const runtime = new CopilotRuntime({ agents });
  const handler = createCopilotRuntimeHandler({
    runtime,
    basePath: "/api/copilotkit",
  });
  return { handler, graphIds: ids };
}

async function getCopilotkitHandler() {
  const now = Date.now();
  if (_cached.handler && now < _cached.expiresAt) {
    return _cached.handler;
  }

  const graphIds = await loadGraphIdsFromDjango();
  const built = buildRuntimeAndHandler(graphIds);
  _cached = {
    expiresAt: now + RUNTIME_CACHE_TTL_MS,
    handler: built.handler,
    graphIds: built.graphIds,
  };
  return _cached.handler;
}

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

function createProjectProfileSystemMessage(profileId, content) {
  return {
    id: `project-profile:${profileId}`,
    role: "system",
    content,
  };
}

export function injectProjectProfileContext(req, requestBody) {
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

  const filesystemRoots = Array.isArray(profile.filesystem_roots)
    ? profile.filesystem_roots.filter((root) => typeof root === "string" && root.trim())
    : [];
  const selectedRoot =
    typeof profile.selected_filesystem_root === "string" && profile.selected_filesystem_root.trim()
      ? profile.selected_filesystem_root
      : (filesystemRoots[0] ?? profile.mcp_root ?? "/workspace-data");

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
    `Filesystem root: ${selectedRoot}`,
    `Allowed roots: ${filesystemRoots.length > 0 ? filesystemRoots.join(", ") : selectedRoot}`,
    `Tool mode: ${profile.tool_mode ?? "read_only"}`,
  ].join("\n");

  return {
    ...requestBody,
    messages: [createProjectProfileSystemMessage(profile.id, systemText), ...messages],
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
  const copilotkitHandler = await getCopilotkitHandler();

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