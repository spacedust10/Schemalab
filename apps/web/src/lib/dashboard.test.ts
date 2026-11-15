import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET as canvasCsv, POST as canvasMatched } from "../app/api/assignments/[id]/canvas/route";
import { GET as dashboard } from "../app/api/assignments/[id]/dashboard/route";
import { GET as answers } from "../app/api/assignments/[id]/submissions/route";
import { SESSION_COOKIE } from "./api";
import { histogram, median, takeaway } from "./dashboard";

describe("score histogram", () => {
  it("counts best scores in bands of ten, 100 in the top band", () => {
    expect(histogram([0, 9, 10, 55, 100, 100])).toEqual([2, 1, 0, 0, 0, 1, 0, 0, 0, 2]);
  });
});

describe("the dashboard's takeaway", () => {
  it("finds the median of best scores", () => {
    expect(median([65, 100])).toBe(82.5);
    expect(median([30, 65, 100])).toBe(65);
  });

  it("says in one sentence how the class did", () => {
    expect(takeaway([65, 100])).toBe("1 of 2 students reached full marks. Half scored 82.5 or more.");
    expect(takeaway([100, 100])).toBe("All 2 students reached full marks.");
    expect(takeaway([40])).toBe("No student has full marks yet. Half scored 40 or more.");
  });
});

describe("dashboard routes", () => {
  afterEach(() => vi.unstubAllGlobals());
  const headers = { cookie: `${SESSION_COOKIE}=tok`, origin: "http://localhost:3000" };
  const id = (value: string) => ({ params: Promise.resolve({ id: value }) });

  it("forwards each call, passing only the email parameter", async () => {
    const calls: [string, string][] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push([url, init?.method ?? "GET"]);
      return url.endsWith(".csv") ? new Response("a,b\n", { headers: { "content-type": "text/csv" } }) : Response.json({});
    });
    await dashboard(new NextRequest("http://localhost:3000/x", { headers }), id("abc123"));
    await answers(new NextRequest("http://localhost:3000/x?email=ada%40umich.edu&y=1", { headers }), id("abc123"));
    const csv = await canvasCsv(new NextRequest("http://localhost:3000/x", { headers }), id("abc123"));
    expect(csv.headers.get("content-type")).toContain("text/csv");
    expect(csv.headers.get("content-disposition")).toContain("attachment");
    await canvasMatched(
      new NextRequest("http://localhost:3000/x", { method: "POST", headers: { ...headers, "content-type": "text/csv" }, body: "a,b\n" }),
      id("abc123"),
    );
    expect(calls).toEqual([
      ["http://localhost:8000/assignments/abc123/dashboard", "GET"],
      ["http://localhost:8000/assignments/abc123/submissions?email=ada%40umich.edu", "GET"],
      ["http://localhost:8000/assignments/abc123/canvas.csv", "GET"],
      ["http://localhost:8000/assignments/abc123/canvas.csv", "POST"],
    ]);
  });

  it("refuses odd ids, cross-site uploads and uploads that are not CSV", async () => {
    vi.stubGlobal("fetch", async () => Response.json({}));
    expect((await dashboard(new NextRequest("http://localhost:3000/x", { headers }), id("../auth"))).status).toBe(404);
    const evil = { ...headers, origin: "https://evil.example", "content-type": "text/csv" };
    expect((await canvasMatched(new NextRequest("http://localhost:3000/x", { method: "POST", headers: evil, body: "a" }), id("abc"))).status).toBe(403);
    const json = { ...headers, "content-type": "application/json" };
    expect((await canvasMatched(new NextRequest("http://localhost:3000/x", { method: "POST", headers: json, body: "{}" }), id("abc"))).status).toBe(415);
  });
});
