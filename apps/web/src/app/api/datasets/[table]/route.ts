import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, rejectCrossSite, upstream } from "@/lib/api";

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ table: string }> },
): Promise<NextResponse> {
  const refused = rejectCrossSite(request, null);
  if (refused) return refused;
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const { table } = await params;

  // One encoded path segment, so a name like "../auth/logout" cannot reach another endpoint.
  const api = await upstream(`/datasets/${encodeURIComponent(table)}`, { method: "DELETE" }, token);
  if (api.status === 204) return new NextResponse(null, { status: 204 });
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}
