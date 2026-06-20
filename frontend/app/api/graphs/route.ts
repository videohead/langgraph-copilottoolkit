import { NextResponse } from "next/server";

const djangoUrl =
  process.env.DJANGO_INTERNAL_URL?.replace(/\/$/, "") ?? "http://django:8080";

export async function GET() {
  const upstream = await fetch(`${djangoUrl}/api/graphs/`, {
    method: "GET",
    cache: "no-store",
  });

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "cache-control": "no-store",
    },
  });
}
