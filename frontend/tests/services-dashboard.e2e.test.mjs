import test from "node:test";
import assert from "node:assert/strict";

import { buildServicesDashboardData } from "../app/api/services/data.mjs";

function mockFetch(routes) {
  return async function fetchImpl(url) {
    const route = routes.find((candidate) => {
      if (candidate.match instanceof RegExp) {
        return candidate.match.test(url);
      }
      return url === candidate.match;
    });

    if (!route) {
      throw new Error(`Unexpected URL: ${url}`);
    }

    if (route.error) {
      throw route.error;
    }

    const status = route.status ?? 200;
    const headers = { "content-type": "application/json" };
    const body = route.body ?? "{}";
    return new Response(typeof body === "string" ? body : JSON.stringify(body), {
      status,
      headers,
    });
  };
}

test("services dashboard reports startup status for core and additional services", async () => {
  const fetchImpl = mockFetch([
    {
      match: /\/api\/ps$/,
      status: 200,
      body: { models: [{ model: "qwen2.5-coder:7b" }] },
    },
    { match: /\/api\/health\/$/, status: 200, body: { status: "ok" } },
    { match: /\/api\/tags$/, status: 200, body: { models: [] } },
    { match: "http://appserver:8000", status: 200, body: "ok" },
    { match: /\/health$/, status: 200, body: { status: "ok" } },
    { match: /\/swarm-chart\.png$/, status: 200, body: "png" },
    {
      match: /\/api\/graphs\/$/,
      status: 200,
      body: {
        graphs: [
          { id: "basic", description: "Basic" },
          { id: "swarm_v1", description: "Swarm" },
        ],
      },
    },
    { match: /\/basic-chart\.png$/, status: 404, body: "missing" },
    { match: /\/swarm_v1-chart\.png$/, status: 404, body: "missing" },
  ]);

  const payload = await buildServicesDashboardData({
    fetchImpl,
    now: "2026-06-20T12:00:00.000Z",
    timeoutMs: 100,
  });

  assert.equal(payload.generatedAt, "2026-06-20T12:00:00.000Z");
  assert.ok(payload.services.length >= 6);

  const frontend = payload.services.find((service) => service.id === "frontend");
  assert.equal(frontend?.startup.status, "up");

  const django = payload.services.find((service) => service.id === "django");
  assert.equal(django?.startup.status, "up");

  const ollama = payload.services.find((service) => service.id === "ollama");
  assert.equal(ollama?.detail, "qwen2.5-coder:7b");

  const charts = payload.services.find((service) => service.id === "charts");
  assert.equal(charts?.startup.status, "up");

  const mcpFilesystem = payload.services.find((service) => service.id === "mcp-filesystem");
  assert.equal(mcpFilesystem?.location, "http://mcpfs.langgraph.lndo.site/health");
  assert.equal(mcpFilesystem?.startup.status, "up");
  assert.equal(mcpFilesystem?.detail, "MCP transport: http://mcpfs.langgraph.lndo.site/mcp");

  const basicChart = payload.graphCharts.find((chart) => chart.graphId === "basic");
  assert.equal(basicChart?.available, true);
  assert.ok(basicChart?.pngUrl?.endsWith("/swarm-chart.png"));
});

test("services dashboard handles chart service outage", async () => {
  const fetchImpl = mockFetch([
    { match: /\/api\/ps$/, status: 200, body: { models: [] } },
    { match: /\/api\/health\/$/, status: 200, body: { status: "ok" } },
    { match: /\/api\/tags$/, status: 200, body: { models: [] } },
    { match: "http://appserver:8000", status: 200, body: "ok" },
    { match: /\/health$/, status: 200, body: { status: "ok" } },
    { match: /\/swarm-chart\.png$/, error: new Error("connect ECONNREFUSED") },
    {
      match: /\/api\/graphs\/$/,
      status: 200,
      body: { graphs: [{ id: "basic", description: "Basic" }] },
    },
    { match: /\/basic-chart\.png$/, error: new Error("connect ECONNREFUSED") },
    { match: /\/swarm-chart\.png$/, error: new Error("connect ECONNREFUSED") },
  ]);

  const payload = await buildServicesDashboardData({ fetchImpl, timeoutMs: 100 });

  const charts = payload.services.find((service) => service.id === "charts");
  assert.equal(charts?.startup.status, "down");

  const basicChart = payload.graphCharts.find((chart) => chart.graphId === "basic");
  assert.equal(basicChart?.available, false);
  assert.equal(basicChart?.pngUrl, null);
});
