import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, upstream } from "@/lib/api";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  // Pass on only the two parameters the API takes.
  const q = request.nextUrl.searchParams;
  const params = new URLSearchParams({ table: q.get("table") ?? "", dataset: q.get("dataset") ?? "sample" });
  const api = await upstream(`/constraints?${params}`, {}, token);
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}
