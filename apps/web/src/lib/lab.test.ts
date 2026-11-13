import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "../app/api/lab/[action]/route";
import { SESSION_COOKIE } from "./api";
import { type Entry, record, settle } from "./lab";

describe("console history", () => {
  it("records each statement with its outcome", () => {
    const h = record([], "SELECT 1", { state: "done", status: "SELECT 1", columns: ["?column?"], rows: [[1]], transaction: "idle" });
    expect(h).toHaveLength(1);
    expect(h[0].sql).toBe("SELECT 1");
    expect(h[0].outcome.state).toBe("done");
  });

  it("replaces the waiting entry when the statement finishes", () => {
    let h: Entry[] = record([], "BEGIN", { state: "done", status: "BEGIN", transaction: "in transaction" });
    h = record(h, "UPDATE t SET n = 2", { state: "waiting", transaction: "waiting" });
    h = settle(h, { state: "done", status: "UPDATE 1", transaction: "in transaction" });
    expect(h.map((e) => e.outcome.status)).toEqual(["BEGIN", "UPDATE 1"]);
    expect(h[1].waited).toBe(true);
  });

  it("leaves history alone if nothing was waiting", () => {
    const h = record([], "BEGIN", { state: "done", status: "BEGIN", transaction: "in transaction" });
    expect(settle(h, { state: "done", status: "X", transaction: "idle" })).toBe(h);
  });
});

describe("slow statements", () => {
  it("replaces a running entry without calling it a lock wait", () => {
    let h: Entry[] = record([], "SELECT pg_sleep(5)", { state: "running", transaction: "running" });
    h = settle(h, { state: "done", status: "SELECT 1", transaction: "idle" });
    expect(h[0].outcome.state).toBe("done");
    expect(h[0].waited).toBe(false);
  });

  it("treats running and waiting as unfinished, and nothing else", async () => {
    const { unfinished } = await import("./lab");
    expect(["waiting", "running", "done", "error", "idle"].map((state) => unfinished({ state } as never))).toEqual([true, true, false, false, false]);
  });
});

describe("lab routes", () => {
  afterEach(() => vi.unstubAllGlobals());
  const headers = { cookie: `${SESSION_COOKIE}=tok`, "content-type": "application/json", origin: "http://localhost:3000" };
  const params = (action: string) => ({ params: Promise.resolve({ action }) });

  it("forwards known actions only, keeping only the session parameter on GET", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return Response.json({});
    });
    await POST(new NextRequest("http://localhost:3000/api/lab/run", { method: "POST", headers, body: "{}" }), params("run"));
    await POST(new NextRequest("http://localhost:3000/api/lab/explain", { method: "POST", headers, body: "{}" }), params("explain"));
    await GET(new NextRequest("http://localhost:3000/api/lab/result?session=B&x=1", { headers }), params("result"));
    expect((await POST(new NextRequest("http://localhost:3000/api/lab/x", { method: "POST", headers, body: "{}" }), params("../auth"))).status).toBe(404);
    expect(calls).toEqual([
      "http://localhost:8000/lab/run",
      "http://localhost:8000/lab/explain",
      "http://localhost:8000/lab/result?session=B",
    ]);
  });

  it("refuses cross-site posts", async () => {
    vi.stubGlobal("fetch", async () => Response.json({}));
    const evil = { ...headers, origin: "https://evil.example" };
    const res = await POST(new NextRequest("http://localhost:3000/api/lab/run", { method: "POST", headers: evil, body: "{}" }), params("run"));
    expect(res.status).toBe(403);
  });
});

describe("xmaxLabel", async () => {
  const { xmaxLabel } = await import("./lab");
  const sessions = {
    A: { pid: 1, transaction: "in transaction", xid: "771", blocked_by: [] },
    B: { pid: 2, transaction: "idle", xid: null, blocked_by: [] },
  };

  it("names the open transaction that is changing or locking the row", () => {
    expect(xmaxLabel("771", sessions)).toEqual({ text: "771 · A's open transaction", live: true });
  });

  it("says when the transaction in xmax has already ended", () => {
    // Live case: after a lost-update run, a row kept xmax 766 from a finished lock.
    expect(xmaxLabel("766", sessions)).toEqual({ text: "766 · finished", live: false });
    expect(xmaxLabel("0", sessions)).toEqual({ text: "0", live: false });
  });
});
