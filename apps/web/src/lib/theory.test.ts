import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST } from "../app/api/theory/[action]/route";
import { SESSION_COOKIE } from "./api";
import { graphLayout } from "./theory";

describe("graphLayout", () => {
  const l = graphLayout([1, 2, 3], [
    { from: 1, to: 2, items: ["A"] },
    { from: 2, to: 1, items: ["B"] },
    { from: 2, to: 3, items: ["A"] },
  ]);

  it("puts the nodes on a circle inside the drawing", () => {
    for (const n of l.nodes) {
      expect(n.x - l.r).toBeGreaterThanOrEqual(0);
      expect(n.y - l.r).toBeGreaterThanOrEqual(0);
      expect(n.x + l.r).toBeLessThanOrEqual(l.width);
      expect(n.y + l.r).toBeLessThanOrEqual(l.height);
    }
  });

  it("starts and ends arrows on the node borders", () => {
    const e = l.edges.find((x) => x.from === 2 && x.to === 3)!;
    const a = l.nodes.find((n) => n.txn === 2)!;
    const b = l.nodes.find((n) => n.txn === 3)!;
    expect(Math.hypot(e.x1 - a.x, e.y1 - a.y)).toBeCloseTo(l.r, 0);
    expect(Math.hypot(e.x2 - b.x, e.y2 - b.y)).toBeCloseTo(l.r + 4, 0);
  });

  it("bends the two directions of a pair apart", () => {
    const there = l.edges.find((x) => x.from === 1 && x.to === 2)!;
    const back = l.edges.find((x) => x.from === 2 && x.to === 1)!;
    expect(there.bend).not.toBe(0);
    expect(back.bend).not.toBe(0);
    expect(l.edges.find((x) => x.from === 2 && x.to === 3)!.bend).toBe(0);
  });
});

describe("theory route", () => {
  afterEach(() => vi.unstubAllGlobals());
  const headers = { cookie: `${SESSION_COOKIE}=tok`, "content-type": "application/json", origin: "http://localhost:3000" };
  const params = (action: string) => ({ params: Promise.resolve({ action }) });

  it("forwards schedules and recovery only", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return Response.json({});
    });
    const post = (a: string) => POST(new NextRequest(`http://localhost:3000/api/theory/${a}`, { method: "POST", headers, body: "{}" }), params(a));
    expect((await post("schedules")).status).toBe(200);
    expect((await post("recovery")).status).toBe(200);
    expect((await post("lab")).status).toBe(404);
    expect(calls).toEqual(["http://localhost:8000/schedules", "http://localhost:8000/recovery"]);
  });
});
