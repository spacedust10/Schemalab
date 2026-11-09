import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, rejectCrossSite, upstream } from "@/lib/api";

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const refused = rejectCrossSite(request, null);
  if (refused) return refused;
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const { id } = await params;
  const api = await upstream(`/pins/${encodeURIComponent(id)}`, { method: "DELETE" }, token);
  if (api.status === 204) return new NextResponse(null, { status: 204 });
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}
