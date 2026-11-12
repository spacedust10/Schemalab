import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, rejectCrossSite, upstream } from "@/lib/api";

async function pass(api: Response): Promise<NextResponse> {
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  return pass(await upstream("/bench", {}, token));
}

/** Starts a run. */
export async function POST(request: NextRequest): Promise<NextResponse> {
  const refused = rejectCrossSite(request);
  if (refused) return refused;
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  return pass(await upstream("/bench/run", { method: "POST", body: await request.text() }, token));
}
