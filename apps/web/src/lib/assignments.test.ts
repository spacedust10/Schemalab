import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET as list, POST as create } from "../app/api/assignments/route";
import { GET as view } from "../app/api/assignments/[id]/route";
import { POST as submit } from "../app/api/assignments/[id]/submit/route";
import { POST as check } from "../app/api/assignments/check/route";
import { SESSION_COOKIE } from "./api";
import { apiPath, scoreLine, validId } from "./assignments";

describe("assignment helpers", () => {
  it("accepts sample and random ids, nothing else", () => {
    expect(validId("sample:top-paid")).toBe(true);
    expect(validId("aB3_-xYz9QwErTy0")).toBe(true);
    expect(validId("../auth/me")).toBe(false);
    expect(validId("a".repeat(41))).toBe(false);
  });

  it("builds API paths only for valid ids, encoded", () => {
    expect(apiPath("sample:top-paid")).toBe("/api/assignments/sample%3Atop-paid");
    expect(apiPath("abc123", "submit")).toBe("/api/assignments/abc123/submit");
    expect(apiPath("../auth/me")).toBeNull();
    expect(apiPath("x/../../lab/start", "submit")).toBeNull();
  });

  it("sums a score up with its parts", () => {
    expect(scoreLine({ score: 77, parts: { runs: 10, columns: 20, rows: 47 } })).toBe(
      "77 of 100: 10 for running, 20 for the columns, 47 for the rows.",
    );
  });
});

describe("assignment routes", () => {
  afterEach(() => vi.unstubAllGlobals());
  const headers = { cookie: `${SESSION_COOKIE}=tok`, "content-type": "application/json", origin: "http://localhost:3000" };
  const id = (value: string) => ({ params: Promise.resolve({ id: value }) });

  it("forwards each call to its API path", async () => {
    const calls: [string, string][] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push([url, init?.method ?? "GET"]);
      return Response.json({});
    });
    await list(new NextRequest("http://localhost:3000/api/assignments", { headers }));
    await create(new NextRequest("http://localhost:3000/api/assignments", { method: "POST", headers, body: "{}" }));
    await check(new NextRequest("http://localhost:3000/api/assignments/check", { method: "POST", headers, body: "{}" }));
    await view(new NextRequest("http://localhost:3000/api/assignments/sample:top-paid", { headers }), id("sample:top-paid"));
    await submit(new NextRequest("http://localhost:3000/api/assignments/x/submit", { method: "POST", headers, body: "{}" }), id("abc123"));
    expect(calls).toEqual([
      ["http://localhost:8000/assignments", "GET"],
      ["http://localhost:8000/assignments", "POST"],
      ["http://localhost:8000/assignments/check", "POST"],
      ["http://localhost:8000/assignments/sample:top-paid", "GET"],
      ["http://localhost:8000/assignments/abc123/submit", "POST"],
    ]);
  });

  it("refuses odd ids and cross-site posts", async () => {
    vi.stubGlobal("fetch", async () => Response.json({}));
    expect((await view(new NextRequest("http://localhost:3000/api/assignments/x", { headers }), id("../auth"))).status).toBe(404);
    const evil = { ...headers, origin: "https://evil.example" };
    expect((await submit(new NextRequest("http://localhost:3000/api/assignments/x/submit", { method: "POST", headers: evil, body: "{}" }), id("abc"))).status).toBe(403);
  });
});
