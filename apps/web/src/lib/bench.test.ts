import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "../app/api/bench/route";
import { SESSION_COOKIE } from "./api";
import { type BenchResult, comparisons, headline } from "./bench";

const result: BenchResult = {
  benchmark: "tpch",
  name: "TPC-H",
  scale: 0.01,
  reps: 3,
  ran_at: "2026-11-12T14:00:00+00:00",
  versions: { duckdb: "1.4.1", postgres: 170006 },
  queries: [
    { nr: 1, duckdb_ms: 2, postgres_ms: 40, rows: 4, agree: true, error: null },
    { nr: 2, duckdb_ms: 10, postgres_ms: 5, rows: 100, agree: true, error: null },
    { nr: 3, duckdb_ms: 4, postgres_ms: null, rows: 10, agree: null, error: "PostgreSQL: canceling statement due to statement timeout" },
  ],
  summary: { queries: 3, agreed: 2, failed: 1, geomean_duckdb_ms: 4.472, geomean_postgres_ms: 14.142, duckdb_faster: 1, compared: 2 },
};

describe("benchmark helpers", () => {
  it("compares only queries both engines finished, as PostgreSQL time over DuckDB time", () => {
    expect(comparisons(result)).toEqual([
      { nr: 1, ratio: 20, faster: "DuckDB" },
      { nr: 2, ratio: 0.5, faster: "PostgreSQL" },
    ]);
  });

  it("sums a run up in one sentence", () => {
    expect(headline(result)).toBe(
      "DuckDB was faster on 1 of 2 queries both engines finished. Geometric mean 4.47 ms against 14.14 ms: 3.2× faster.",
    );
  });
});

describe("bench route", () => {
  afterEach(() => vi.unstubAllGlobals());
  const headers = { cookie: `${SESSION_COOKIE}=tok`, "content-type": "application/json", origin: "http://localhost:3000" };

  it("reads with GET and starts a run with POST", async () => {
    const calls: [string, string][] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push([url, init?.method ?? "GET"]);
      return Response.json({}, { status: init?.method === "POST" ? 202 : 200 });
    });
    expect((await GET(new NextRequest("http://localhost:3000/api/bench", { headers }))).status).toBe(200);
    const body = JSON.stringify({ benchmark: "tpch", scale: 0.01 });
    expect((await POST(new NextRequest("http://localhost:3000/api/bench", { method: "POST", headers, body }))).status).toBe(202);
    expect(calls).toEqual([
      ["http://localhost:8000/bench", "GET"],
      ["http://localhost:8000/bench/run", "POST"],
    ]);
  });

  it("refuses cross-site posts and missing sessions", async () => {
    vi.stubGlobal("fetch", async () => Response.json({}));
    const evil = { ...headers, origin: "https://evil.example" };
    expect((await POST(new NextRequest("http://localhost:3000/api/bench", { method: "POST", headers: evil, body: "{}" }))).status).toBe(403);
    expect((await GET(new NextRequest("http://localhost:3000/api/bench"))).status).toBe(401);
  });
});
