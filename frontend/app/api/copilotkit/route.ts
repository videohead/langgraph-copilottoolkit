import type { NextRequest } from "next/server";
import { handleCopilotKitRequest } from "./runtime.mjs";

export async function GET(req: NextRequest) {
  return handleCopilotKitRequest(req);
}

export async function POST(req: NextRequest) {
  return handleCopilotKitRequest(req);
}