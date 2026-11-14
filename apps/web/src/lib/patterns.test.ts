import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "../app/api/lab/[action]/route";
import { SESSION_COOKIE } from "./api";
import { cell } from "./patterns";

describe("pattern check cells", () => {
  it("shows a passing value plainly", () => {
    expect(cell({ label: "Money in the bank", expected: 3000, actual: 3000, ok: true })).toEqual({ text: "3,000", ok: true });
  });

  it("says what was expected when a check fails", () => {
    expect(cell({ label: "Money in the bank", expected: 3000, actual: 3170, ok: false })).toEqual({
      text: "3,170, expected 3,000",
      ok: false,
    });
  });
});

describe("pattern routes", () => {
  afterEach(() => vi.unstubAllGlobals());
  const headers = { cookie: `${SESSION_COOKIE}=tok`, "content-type": "application/json", origin: "http://localhost:3000" };
  const params = (action: string) => ({ params: Promise.resolve({ action }) });

  it("forwards the list and the run", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return Response.json({});
    });
    await GET(new NextRequest("http://localhost:3000/api/lab/patterns", { headers }), params("patterns"));
    await POST(new NextRequest("http://localhost:3000/api/lab/pattern", { method: "POST", headers, body: "{}" }), params("pattern"));
    expect(calls).toEqual(["http://localhost:8000/lab/patterns", "http://localhost:8000/lab/pattern"]);
  });

  it("forwards the experiment group, and nothing else", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return Response.json([]);
    });
    await GET(new NextRequest("http://localhost:3000/api/lab/patterns?group=pipelines&x=1", { headers }), params("patterns"));
    expect(calls).toEqual(["http://localhost:8000/lab/patterns?group=pipelines"]);
  });
});
