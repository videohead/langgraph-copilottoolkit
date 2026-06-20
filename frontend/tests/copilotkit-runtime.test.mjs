import test from "node:test";
import assert from "node:assert/strict";

import { handleCopilotKitRequest } from "../app/api/copilotkit/runtime.mjs";

async function readJson(response) {
  const text = await response.text();
  return JSON.parse(text);
}

test("POST /api/copilotkit answers runtime info", async () => {
  const response = await handleCopilotKitRequest(
    new Request("http://localhost/api/copilotkit", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ method: "info" }),
    }),
  );

  assert.equal(response.status, 200);

  const payload = await readJson(response);
  assert.match(payload.version, /^\d+\.\d+\.\d+$/);
  const agentIds = Object.keys(payload.agents);
  assert.ok(agentIds.includes("default"));
  assert.ok(agentIds.length >= 2);
  assert.ok(agentIds.some((id) => id !== "default"));
});

test("GET /api/copilotkit/info answers runtime info", async () => {
  const response = await handleCopilotKitRequest(
    new Request("http://localhost/api/copilotkit/info", {
      method: "GET",
    }),
  );

  assert.equal(response.status, 200);

  const payload = await readJson(response);
  assert.match(payload.version, /^\d+\.\d+\.\d+$/);
  const agentIds = Object.keys(payload.agents);
  assert.ok(agentIds.includes("default"));
  assert.ok(agentIds.length >= 2);
  assert.ok(agentIds.some((id) => id !== "default"));
});

test("POST /api/copilotkit agent/run maps to runtime route (not Not found)", async () => {
  const response = await handleCopilotKitRequest(
    new Request("http://localhost/api/copilotkit", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        method: "agent/run",
        params: { agentId: "basic" },
        body: {},
      }),
    }),
  );

  // Invalid body is expected in this unit test, but route resolution must work.
  assert.notEqual(response.status, 404);

  const text = await response.text();
  assert.ok(!text.includes('"error":"Not found"'));
});