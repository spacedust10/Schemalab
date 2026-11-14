import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, rejectCrossSite, upstream } from "@/lib/api";

/** The builder's checks, without saving anything. */
export async function POST(request: NextRequest): Promise<NextResponse> {
  const refused = rejectCrossSite(request);
  if (refused) return refused;
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const api = await upstream("/assignments/check", { method: "POST", body: await request.text() }, token);
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}
