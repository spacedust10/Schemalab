import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, clearSession, rejectCrossSite, setSession, upstream } from "@/lib/api";

const SIGN_IN = new Set(["login", "register"]);

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ action: string }> },
): Promise<NextResponse> {
  const refused = rejectCrossSite(request);
  if (refused) return refused;
  const { action } = await params;

  if (action === "logout") {
    const token = request.cookies.get(SESSION_COOKIE)?.value;
    if (token) await upstream("/auth/logout", { method: "POST" }, token);
    const res = new NextResponse(null, { status: 204 });
    clearSession(res);
    return res;
  }
  if (!SIGN_IN.has(action)) return NextResponse.json({ detail: "Not found." }, { status: 404 });

  const api = await upstream(`/auth/${action}`, { method: "POST", body: await request.text() });
  const body = await api.json();
  if (!api.ok) return NextResponse.json(body, { status: api.status, headers: retryAfter(api) });

  const res = NextResponse.json({ ok: true }); // the token never reaches page scripts
  setSession(res, body.token);
  return res;
}

function retryAfter(api: Response): HeadersInit {
  const value = api.headers.get("retry-after");
  return value ? { "retry-after": value } : {};
}
