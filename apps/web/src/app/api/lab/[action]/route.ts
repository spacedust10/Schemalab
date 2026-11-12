import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, rejectCrossSite, upstream } from "@/lib/api";

const POSTS = new Set(["start", "stop", "run", "scenario"]);
const GETS = new Set(["state", "result", "scenarios"]);
type Params = { params: Promise<{ action: string }> };

async function pass(api: Response): Promise<NextResponse> {
  if (api.status === 204) return new NextResponse(null, { status: 204 });
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}

export async function POST(request: NextRequest, { params }: Params): Promise<NextResponse> {
  const refused = rejectCrossSite(request);
  if (refused) return refused;
  const { action } = await params;
  if (!POSTS.has(action)) return NextResponse.json({ detail: "Not found." }, { status: 404 });
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  return pass(await upstream(`/lab/${action}`, { method: "POST", body: await request.text() }, token));
}

export async function GET(request: NextRequest, { params }: Params): Promise<NextResponse> {
  const { action } = await params;
  if (!GETS.has(action)) return NextResponse.json({ detail: "Not found." }, { status: 404 });
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const session = request.nextUrl.searchParams.get("session");
  const query = action === "result" && session ? `?${new URLSearchParams({ session })}` : "";
  return pass(await upstream(`/lab/${action}${query}`, {}, token));
}
