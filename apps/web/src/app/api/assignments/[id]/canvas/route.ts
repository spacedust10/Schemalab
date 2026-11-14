import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, rejectCrossSite, upstream } from "@/lib/api";
import { validId } from "@/lib/assignments";

type Params = { params: Promise<{ id: string }> };

/** The Canvas gradebook CSV, as a download. */
export async function GET(request: NextRequest, { params }: Params): Promise<NextResponse> {
  const { id } = await params;
  if (!validId(id)) return NextResponse.json({ detail: "No such assignment." }, { status: 404 });
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const api = await upstream(`/assignments/${id}/canvas.csv`, {}, token);
  if (!api.ok) return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
  return new NextResponse(await api.text(), {
    headers: { "content-type": "text/csv; charset=utf-8", "content-disposition": 'attachment; filename="canvas-grades.csv"' },
  });
}

/** The same CSV with Canvas IDs filled in from the teacher's own gradebook export. */
export async function POST(request: NextRequest, { params }: Params): Promise<NextResponse> {
  const refused = rejectCrossSite(request, "text/csv");
  if (refused) return refused;
  const { id } = await params;
  if (!validId(id)) return NextResponse.json({ detail: "No such assignment." }, { status: 404 });
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const api = await upstream(
    `/assignments/${id}/canvas.csv`,
    { method: "POST", body: await request.text(), headers: { "content-type": "text/csv" } },
    token,
  );
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}
