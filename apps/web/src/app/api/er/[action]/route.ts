import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, rejectCrossSite, upstream } from "@/lib/api";

const POSTS = new Set(["ddl", "from-english"]);
type Params = { params: Promise<{ action: string }> };

export async function POST(request: NextRequest, { params }: Params): Promise<NextResponse> {
  const refused = rejectCrossSite(request);
  if (refused) return refused;
  const { action } = await params;
  if (!POSTS.has(action)) return NextResponse.json({ detail: "Not found." }, { status: 404 });
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const api = await upstream(`/er/${action}`, { method: "POST", body: await request.text() }, token);
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}

export async function GET(request: NextRequest, { params }: Params): Promise<NextResponse> {
  const { action } = await params;
  if (action !== "mine") return NextResponse.json({ detail: "Not found." }, { status: 404 });
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const api = await upstream("/er/mine", {}, token);
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}
