const DEFAULT_ENDPOINTS = {
  django: [
    process.env.DJANGO_INTERNAL_URL,
    "http://django:8080",
    "http://django.langgraph.internal:8080",
  ],
  appserver: [
    process.env.LANGGRAPH_INTERNAL_URL,
    "http://appserver:8000",
    "http://appserver.langgraph.internal:8000",
  ],
  ollama: [
    process.env.OLLAMA_INTERNAL_URL,
    "http://ollama:11434",
    "http://ollama.langgraph.internal:11434",
  ],
  mcpFilesystem: [
    process.env.MCP_FILESYSTEM_INTERNAL_URL,
    "http://mcp-filesystem:8765",
    "http://mcp-filesystem.langgraph.internal:8765",
  ],
  charts: [
    process.env.CHARTS_INTERNAL_URL,
    "http://charts:80",
    "http://charts.langgraph.internal:80",
  ],
};

const PUBLIC_LOCATIONS = {
  frontend: process.env.FRONTEND_PUBLIC_URL ?? "http://langgraph.lndo.site",
  django: process.env.DJANGO_PUBLIC_URL ?? "http://api.langgraph.lndo.site",
  appserver: process.env.LANGGRAPH_PUBLIC_URL ?? "http://langgraph-api.lndo.site",
  ollama: process.env.OLLAMA_PUBLIC_URL ?? "http://localhost:11434",
  mcpFilesystem: process.env.MCP_FILESYSTEM_PUBLIC_URL ?? "http://mcpfs.langgraph.lndo.site",
  charts: process.env.CHARTS_PUBLIC_URL ?? "http://charts.langgraph.lndo.site",
};

const FALLBACK_GRAPH_IDS = ["basic", "swarm_v1"];
const GRAPH_CHART_FALLBACK = "swarm-chart.png";
const REQUEST_TIMEOUT_MS = Number(process.env.SERVICE_STATUS_TIMEOUT_MS ?? 1500);

function normalizeBaseUrls(candidates) {
  const seen = new Set();
  const urls = [];
  for (const value of candidates) {
    if (typeof value !== "string") {
      continue;
    }
    const trimmed = value.trim().replace(/\/$/, "");
    if (!trimmed || seen.has(trimmed)) {
      continue;
    }
    seen.add(trimmed);
    urls.push(trimmed);
  }
  return urls;
}

function withTimeout(promiseFactory, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return promiseFactory(controller.signal).finally(() => {
    clearTimeout(timer);
  });
}

async function probeUrls(urls, fetchImpl, timeoutMs) {
  let lastError = "no probe targets";
  for (const url of urls) {
    try {
      const response = await withTimeout(
        (signal) =>
          fetchImpl(url, {
            method: "GET",
            cache: "no-store",
            signal,
          }),
        timeoutMs,
      );

      if (response.ok) {
        return {
          status: "up",
          probeUrl: url,
          statusCode: response.status,
          error: null,
        };
      }

      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : "request failed";
    }
  }

  return {
    status: "down",
    probeUrl: urls[0] ?? null,
    statusCode: null,
    error: lastError,
  };
}

function chartCandidatesForGraph(graphId) {
  const candidates = [`${graphId}-chart.png`];

  if (graphId.startsWith("swarm")) {
    candidates.push("swarm-chart.png");
  }

  if (graphId.startsWith("basic")) {
    candidates.push("basic-chart.png");
  }

  if (!candidates.includes(GRAPH_CHART_FALLBACK)) {
    candidates.push(GRAPH_CHART_FALLBACK);
  }

  return [...new Set(candidates)];
}

async function firstExistingChart(chartBases, chartFiles, fetchImpl, timeoutMs) {
  for (const filename of chartFiles) {
    const probes = chartBases.map((base) => `${base}/${filename}`);
    const result = await probeUrls(probes, fetchImpl, timeoutMs);
    if (result.status === "up") {
      return filename;
    }
  }
  return null;
}

async function loadGraphIds(fetchImpl, timeoutMs) {
  const djangoBases = normalizeBaseUrls(DEFAULT_ENDPOINTS.django);
  for (const base of djangoBases) {
    try {
      const response = await withTimeout(
        (signal) =>
          fetchImpl(`${base}/api/graphs/`, {
            method: "GET",
            cache: "no-store",
            signal,
          }),
        timeoutMs,
      );

      if (!response.ok) {
        continue;
      }

      const payload = await response.json();
      const graphIds = Array.isArray(payload?.graphs)
        ? payload.graphs
            .map((graph) => (graph && typeof graph.id === "string" ? graph.id.trim() : ""))
            .filter((id) => id.length > 0)
        : [];

      if (graphIds.length > 0) {
        return graphIds;
      }
    } catch {
      // Try next candidate.
    }
  }

  return FALLBACK_GRAPH_IDS;
}

async function loadOllamaModel(fetchImpl, timeoutMs) {
  const ollamaBases = normalizeBaseUrls(DEFAULT_ENDPOINTS.ollama);

  for (const base of ollamaBases) {
    try {
      const psResponse = await withTimeout(
        (signal) =>
          fetchImpl(`${base}/api/ps`, {
            method: "GET",
            cache: "no-store",
            signal,
          }),
        timeoutMs,
      );

      if (psResponse.ok) {
        const payload = await psResponse.json();
        const runningModels = Array.isArray(payload?.models)
          ? payload.models
              .map((entry) => {
                if (!entry || typeof entry !== "object") {
                  return "";
                }
                const byModel = typeof entry.model === "string" ? entry.model.trim() : "";
                const byName = typeof entry.name === "string" ? entry.name.trim() : "";
                return byModel || byName;
              })
              .filter((name) => name.length > 0)
          : [];

        if (runningModels.length > 0) {
          return runningModels.join(", ");
        }
      }
    } catch {
      // Ignore and try fallback endpoint.
    }

    try {
      const tagsResponse = await withTimeout(
        (signal) =>
          fetchImpl(`${base}/api/tags`, {
            method: "GET",
            cache: "no-store",
            signal,
          }),
        timeoutMs,
      );

      if (!tagsResponse.ok) {
        continue;
      }

      const payload = await tagsResponse.json();
      const models = Array.isArray(payload?.models)
        ? payload.models
            .map((entry) => {
              if (!entry || typeof entry !== "object") {
                return "";
              }
              const byModel = typeof entry.model === "string" ? entry.model.trim() : "";
              const byName = typeof entry.name === "string" ? entry.name.trim() : "";
              return byModel || byName;
            })
            .filter((name) => name.length > 0)
        : [];

      if (models.length > 0) {
        return models[0];
      }
    } catch {
      // Try next endpoint candidate.
    }
  }

  return process.env.OLLAMA_MODEL?.trim() || null;
}

export async function buildServicesDashboardData(options = {}) {
  const fetchImpl = options.fetchImpl ?? fetch;
  const timeoutMs = Number(options.timeoutMs ?? REQUEST_TIMEOUT_MS);
  const now = options.now ?? new Date().toISOString();
  const ollamaModel = await loadOllamaModel(fetchImpl, timeoutMs);

  const services = [
    {
      id: "frontend",
      name: "Frontend",
      group: "core",
      location: PUBLIC_LOCATIONS.frontend,
      startup: { status: "up", probeUrl: "self", statusCode: 200, error: null },
    },
    {
      id: "django",
      name: "Django API",
      group: "core",
      location: PUBLIC_LOCATIONS.django,
      startup: await probeUrls(
        normalizeBaseUrls(DEFAULT_ENDPOINTS.django).map((base) => `${base}/api/health/`),
        fetchImpl,
        timeoutMs,
      ),
    },
    {
      id: "ollama",
      name: "Ollama",
      group: "core",
      location: PUBLIC_LOCATIONS.ollama,
      detail: ollamaModel,
      startup: await probeUrls(
        normalizeBaseUrls(DEFAULT_ENDPOINTS.ollama).map((base) => `${base}/api/tags`),
        fetchImpl,
        timeoutMs,
      ),
    },
    {
      id: "appserver",
      name: "LangGraph Appserver",
      group: "additional",
      location: PUBLIC_LOCATIONS.appserver,
      startup: await probeUrls(normalizeBaseUrls(DEFAULT_ENDPOINTS.appserver), fetchImpl, timeoutMs),
    },
    {
      id: "mcp-filesystem",
      name: "MCP Filesystem",
      group: "additional",
      location: PUBLIC_LOCATIONS.mcpFilesystem,
      startup: await probeUrls(
        normalizeBaseUrls(DEFAULT_ENDPOINTS.mcpFilesystem).map((base) => `${base}/mcp`),
        fetchImpl,
        timeoutMs,
      ),
    },
    {
      id: "charts",
      name: "Charts",
      group: "additional",
      location: PUBLIC_LOCATIONS.charts,
      startup: await probeUrls(
        normalizeBaseUrls(DEFAULT_ENDPOINTS.charts).map((base) => `${base}/${GRAPH_CHART_FALLBACK}`),
        fetchImpl,
        timeoutMs,
      ),
    },
  ];

  const graphIds = await loadGraphIds(fetchImpl, timeoutMs);
  const chartBases = normalizeBaseUrls(DEFAULT_ENDPOINTS.charts);
  const graphCharts = [];

  for (const graphId of graphIds) {
    const filename = await firstExistingChart(
      chartBases,
      chartCandidatesForGraph(graphId),
      fetchImpl,
      timeoutMs,
    );

    graphCharts.push({
      graphId,
      pngUrl: filename ? `${PUBLIC_LOCATIONS.charts}/${filename}` : null,
      available: Boolean(filename),
    });
  }

  return {
    generatedAt: now,
    services,
    graphCharts,
  };
}
