import test from "node:test";
import assert from "node:assert/strict";

import {
  handleCopilotKitRequest,
  injectProjectProfileContext,
} from "../app/api/copilotkit/runtime.mjs";

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

test("project profile injection adds a schema-compatible system message id", () => {
  const request = new Request("http://localhost/api/copilotkit", {
    headers: {
      cookie:
        "copilot_project_profile=%7B%22id%22%3A%22demo%22%2C%22name%22%3A%22Demo%20Project%22%2C%22filesystem_roots%22%3A%5B%22%2Fworkspace-data%22%5D%7D",
    },
  });

  const payload = injectProjectProfileContext(request, {
    messages: [{ id: "user-1", role: "user", content: "Hello" }],
  });

  assert.equal(payload.messages[0].id, "project-profile:demo");
  assert.equal(payload.messages[0].role, "system");
  assert.match(payload.messages[0].content, /\[project-profile:demo\]/);
  assert.equal(payload.messages[1].id, "user-1");
});