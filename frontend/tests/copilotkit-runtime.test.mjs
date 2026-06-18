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
  assert.equal(payload.version, "1.60.2");
  assert.deepEqual(Object.keys(payload.agents).sort(), ["basic", "swarm_v1"]);
});

test("GET /api/copilotkit/info answers runtime info", async () => {
  const response = await handleCopilotKitRequest(
    new Request("http://localhost/api/copilotkit/info", {
      method: "GET",
    }),
  );

  assert.equal(response.status, 200);

  const payload = await readJson(response);
  assert.equal(payload.version, "1.60.2");
  assert.deepEqual(Object.keys(payload.agents).sort(), ["basic", "swarm_v1"]);
});