import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, clearSession, rejectCrossSite, upstream } from "@/lib/api";

export async function POST(request: NextRequest): Promise<Response> {
  const refused = rejectCrossSite(request);
  if (refused) return refused;
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });

  const api = await upstream("/ask", { method: "POST", body: await request.text() }, token);
  if (!api.ok || !api.body) {
    const res = NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
    if (api.status === 401) clearSession(res);
    return res;
  }
  // Pass the event stream straight through, unbuffered.
  return new Response(api.body, {
    headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
  });
}
