import { describe, expect, it } from "vitest";
import { type ERModel, chenLayout, crowEnds, crowLayout, entityOrder } from "./er";

const attr = (name: string, key = false) => ({ name, type: "INT", key });
const UNI: ERModel = {
  entities: [
    { name: "Professor", attributes: [attr("pid", true), attr("name")] },
    { name: "Student", attributes: [attr("sid", true), attr("name"), attr("gpa")] },
    { name: "Course", attributes: [attr("cid", true), attr("title")] },
    { name: "Department", attributes: [attr("dname", true)] },
    { name: "Room", attributes: [attr("rid", true)] },
  ],
  relationships: [
    { name: "Advises", kind: "1:N", left: { entity: "Professor", card: "1", total: false }, right: { entity: "Student", card: "N", total: false }, attributes: [] },
    { name: "Enrolls", kind: "M:N", left: { entity: "Student", card: "N", total: false }, right: { entity: "Course", card: "N", total: true }, attributes: [attr("grade")] },
    { name: "Heads", kind: "1:1", left: { entity: "Professor", card: "1", total: false }, right: { entity: "Department", card: "1", total: true }, attributes: [] },
  ],
};

describe("crowEnds", () => {
  it("draws the maximum from the entity's own count and the minimum from the other side", () => {
    const [advises, enrolls, heads] = UNI.relationships;
    expect(crowEnds(advises)).toEqual({ left: { max: "one", min: "zero" }, right: { max: "many", min: "zero" } });
    // Every course has an enrollment, so a course takes at least one student.
    expect(crowEnds(enrolls)).toEqual({ left: { max: "many", min: "one" }, right: { max: "many", min: "zero" } });
    // Every department has a head: exactly one professor at the professor end.
    expect(crowEnds(heads)).toEqual({ left: { max: "one", min: "one" }, right: { max: "one", min: "zero" } });
  });
});

describe("layout", () => {
  it("orders entities so related ones sit together, most connected first", () => {
    expect(entityOrder(UNI)).toEqual(["Professor", "Student", "Department", "Course", "Room"]);
  });

  it("places every crow's-foot box without overlap, inside the drawing", () => {
    const l = crowLayout(UNI);
    const boxes = Object.values(l.boxes);
    expect(boxes).toHaveLength(5);
    for (const [i, a] of boxes.entries()) {
      expect(a.x >= 0 && a.y >= 0 && a.x + a.w <= l.width && a.y + a.h <= l.height).toBe(true);
      for (const b of boxes.slice(i + 1)) {
        const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
        expect(apart).toBe(true);
      }
    }
  });

  it("puts each Chen diamond between its two entities", () => {
    const l = chenLayout(UNI);
    const p = l.entities.Professor;
    const s = l.entities.Student;
    const d = l.diamonds.Advises;
    expect(d.x).toBeCloseTo((p.x + s.x) / 2, 0);
    expect(d.y).toBeCloseTo((p.y + s.y) / 2, 0);
    expect(l.attributes.Student).toHaveLength(3);
    expect(l.attributes.Enrolls).toHaveLength(1);
  });

  it("keeps attribute ovals off relationship lines", () => {
    // The page's example: four entities, two above two, as drawn on the ER page.
    const four = { ...UNI, entities: UNI.entities.slice(0, 4) };
    const l = chenLayout(four);
    const distance = (p: { x: number; y: number }, a: { x: number; y: number }, b: { x: number; y: number }) => {
      const t = Math.max(0, Math.min(1, ((p.x - a.x) * (b.x - a.x) + (p.y - a.y) * (b.y - a.y)) / ((b.x - a.x) ** 2 + (b.y - a.y) ** 2)));
      return Math.hypot(p.x - (a.x + t * (b.x - a.x)), p.y - (a.y + t * (b.y - a.y)));
    };
    for (const r of four.relationships) {
      const d = l.diamonds[r.name];
      for (const end of [r.left.entity, r.right.entity]) {
        for (const ovals of Object.values(l.attributes)) {
          for (const o of ovals) expect(distance(o, l.entities[end], d)).toBeGreaterThan(30);
        }
      }
    }
  });

  it("gives a self relationship its own diamond beside the entity", () => {
    const self: ERModel = {
      entities: [{ name: "Employee", attributes: [attr("eid", true)] }],
      relationships: [{ name: "Manages", kind: "1:N", left: { entity: "Employee", card: "1", total: false }, right: { entity: "Employee", card: "N", total: false }, attributes: [] }],
    };
    const l = chenLayout(self);
    expect(l.diamonds.Manages.x).toBeGreaterThan(l.entities.Employee.x);
  });
});

describe("er routes", async () => {
  const { NextRequest } = await import("next/server");
  const { GET, POST } = await import("../app/api/er/[action]/route");
  const { SESSION_COOKIE } = await import("./api");
  const { vi, afterEach } = await import("vitest");
  afterEach(() => vi.unstubAllGlobals());
  const params = (action: string) => ({ params: Promise.resolve({ action }) });
  const headers = { cookie: `${SESSION_COOKIE}=tok`, "content-type": "application/json", origin: "http://localhost:3000" };

  it("forwards only the known actions", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return Response.json({ design: "" });
    });
    const post = (action: string) =>
      POST(new NextRequest(`http://localhost:3000/api/er/${action}`, { method: "POST", headers, body: "{}" }), params(action));
    expect((await post("ddl")).status).toBe(200);
    expect((await post("../auth/logout")).status).toBe(404);
    expect((await GET(new NextRequest("http://localhost:3000/api/er/mine", { headers }), params("mine"))).status).toBe(200);
    expect((await GET(new NextRequest("http://localhost:3000/api/er/x", { headers }), params("x"))).status).toBe(404);
    expect(calls).toEqual(["http://localhost:8000/er/ddl", "http://localhost:8000/er/mine"]);
  });
});
