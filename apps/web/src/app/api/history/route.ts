import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, upstream } from "@/lib/api";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const api = await upstream("/history", {}, token);
  return NextResponse.json(await api.json(), { status: api.status });
}
