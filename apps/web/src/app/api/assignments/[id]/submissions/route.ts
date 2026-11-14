import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, upstream } from "@/lib/api";
import { validId } from "@/lib/assignments";

/** One student's answers, for the assignment's creator. Only the email parameter is passed on. */
export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }): Promise<NextResponse> {
  const { id } = await params;
  if (!validId(id)) return NextResponse.json({ detail: "No such assignment." }, { status: 404 });
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const email = request.nextUrl.searchParams.get("email") ?? "";
  const api = await upstream(`/assignments/${id}/submissions?${new URLSearchParams({ email })}`, {}, token);
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}
