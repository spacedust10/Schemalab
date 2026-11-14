import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, rejectCrossSite, upstream } from "@/lib/api";
import { validId } from "@/lib/assignments";

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }): Promise<NextResponse> {
  const refused = rejectCrossSite(request);
  if (refused) return refused;
  const { id } = await params;
  if (!validId(id)) return NextResponse.json({ detail: "No such assignment." }, { status: 404 });
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const api = await upstream(`/assignments/${id}/submit`, { method: "POST", body: await request.text() }, token);
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}
