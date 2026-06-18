/**
 * CopilotKit Runtime endpoint for sub-path requests.
 *
 * The root route at `app/api/copilotkit/route.ts` handles the single-endpoint
 * handshake (`POST /api/copilotkit` with `{ method: "info" }`). This catch-all
 * route preserves the REST-style subpaths such as `/info` and agent run paths.
 */
import type { NextRequest } from "next/server";
import { handleCopilotKitRequest } from "../runtime.mjs";

export async function GET(req: NextRequest) {
  return handleCopilotKitRequest(req);
}

export async function POST(req: NextRequest) {
  return handleCopilotKitRequest(req);
}
