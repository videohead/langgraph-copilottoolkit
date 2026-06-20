import { NextResponse } from "next/server";
import { buildServicesDashboardData } from "./data.mjs";

export async function GET() {
  const payload = await buildServicesDashboardData();

  return NextResponse.json(payload, {
    status: 200,
    headers: {
      "cache-control": "no-store",
    },
  });
}
