import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, rejectCrossSite, upstream } from "@/lib/api";
import { upstreamPath } from "@/lib/lakehouse";

type Params = { params: Promise<{ path?: string[] }> };

async function forward(request: NextRequest, { params }: Params, method: string): Promise<NextResponse> {
  const { path = [] } = await params;
  const target = upstreamPath(method, path, request.nextUrl.searchParams);
  if (!target) return NextResponse.json({ detail: "Not found." }, { status: 404 });
  if (method !== "GET") {
    // Files go as raw bytes, everything else as JSON: a cross-site form can send neither.
    const refused = rejectCrossSite(request, path[0] === "files" ? "application/octet-stream" : "application/json");
    if (refused) return refused;
  }
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const init: RequestInit =
    method === "GET"
      ? {}
      : path[0] === "files"
        ? ({ method, headers: { "content-type": "application/octet-stream" }, body: request.body, duplex: "half" } as RequestInit)
        : { method, body: await request.text() };
  const api = await upstream(target, init, token);
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}

export const GET = (request: NextRequest, ctx: Params) => forward(request, ctx, "GET");
export const POST = (request: NextRequest, ctx: Params) => forward(request, ctx, "POST");
export const PUT = (request: NextRequest, ctx: Params) => forward(request, ctx, "PUT");
