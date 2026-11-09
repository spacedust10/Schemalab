import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, rejectCrossSite, upstream } from "@/lib/api";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const api = await upstream("/datasets", {}, token);
  return NextResponse.json(await api.json(), { status: api.status });
}

/** The file is the raw body; the API enforces the size limit while it streams. */
export async function POST(request: NextRequest): Promise<NextResponse> {
  const refused = rejectCrossSite(request, "application/octet-stream");
  if (refused) return refused;
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  const name = request.nextUrl.searchParams.get("name");
  if (!name) return NextResponse.json({ detail: "Name the file." }, { status: 400 });

  const api = await upstream(
    `/datasets?${new URLSearchParams({ name })}`,
    {
      method: "POST",
      headers: { "content-type": "application/octet-stream" },
      body: request.body,
      duplex: "half", // stream the upload instead of buffering it here
    } as RequestInit,
    token,
  );
  return NextResponse.json(await api.json().catch(() => ({})), { status: api.status });
}
