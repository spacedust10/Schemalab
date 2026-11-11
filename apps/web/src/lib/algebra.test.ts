import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST as algebra } from "../app/api/algebra/route";
import { GET as coach } from "../app/api/constraints/route";
import { type TreeNode, treeLayout } from "./algebra";
import { SESSION_COOKIE } from "./api";

const leaf = (label: string): TreeNode => ({ op: "relation", label, children: [] });
const TREE: TreeNode = {
  op: "project",
  label: "π name",
  children: [{ op: "join", label: "⋈", children: [leaf("student"), { op: "select", label: "σ grade = 'A'", children: [leaf("enrolls")] }] }],
};

describe("treeLayout", () => {
  const l = treeLayout(TREE);
  const byLabel = Object.fromEntries(l.nodes.map((n) => [n.label, n]));

  it("puts the root on top and each level lower", () => {
    expect(byLabel["π name"].y).toBeLessThan(byLabel["⋈"].y);
    expect(byLabel["⋈"].y).toBeLessThan(byLabel.student.y);
    expect(byLabel.student.y).toBeLessThan(byLabel.enrolls.y);
  });

  it("centers a parent over its children", () => {
    const join = byLabel["⋈"];
    expect(join.x).toBeCloseTo((byLabel.student.x + byLabel["σ grade = 'A'"].x) / 2, 5);
    expect(byLabel["π name"].x).toBeCloseTo(join.x, 5);
  });

  it("keeps nodes on one level apart and inside the drawing", () => {
    for (const a of l.nodes) {
      expect(a.x - a.w / 2).toBeGreaterThanOrEqual(0);
      expect(a.x + a.w / 2).toBeLessThanOrEqual(l.width);
      for (const b of l.nodes) {
        if (a !== b && a.y === b.y) expect(Math.abs(a.x - b.x)).toBeGreaterThanOrEqual((a.w + b.w) / 2);
      }
    }
    expect(l.edges).toHaveLength(4);
  });
});

describe("algebra and constraint routes", () => {
  afterEach(() => vi.unstubAllGlobals());
  const headers = { cookie: `${SESSION_COOKIE}=tok`, "content-type": "application/json", origin: "http://localhost:3000" };

  it("forwards algebra posts and coach queries with the token", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return Response.json({});
    });
    await algebra(new NextRequest("http://localhost:3000/api/algebra", { method: "POST", headers, body: "{}" }));
    await coach(new NextRequest("http://localhost:3000/api/constraints?table=Sales Data&dataset=mine&extra=1", { headers }));
    expect(calls).toEqual([
      "http://localhost:8000/algebra",
      "http://localhost:8000/constraints?table=Sales+Data&dataset=mine",
    ]);
  });

  it("refuses a cross-site algebra post", async () => {
    vi.stubGlobal("fetch", async () => Response.json({}));
    const res = await algebra(new NextRequest("http://localhost:3000/api/algebra", { method: "POST", headers: { ...headers, origin: "https://evil.example" }, body: "{}" }));
    expect(res.status).toBe(403);
  });
});
