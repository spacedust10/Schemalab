import { describe, expect, it } from "vitest";
import { type Box, type ERModel, chenLayout, crowEnds, crowLabels, crowLayout, entityOrder, inheritsFrom, layers } from "./er";

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

// The report's schema, as /er/ddl reads it: two weak entities and a disjoint, total ISA.
const v20 = (name: string, key = false) => ({ name, type: "VARCHAR(20)", key });
const end = (entity: string, card: "1" | "N", total = false) => ({ entity, card, total });
const USERS: ERModel = {
  entities: [
    { name: "Departments", attributes: [v20("deptname", true), v20("deptaddr")], weak: false },
    { name: "Units", attributes: [v20("unitid", true), v20("unitaddr")], weak: true },
    { name: "Users", attributes: [v20("userid", true), v20("useraddr")], weak: true },
    { name: "VerifiedUsers", attributes: [v20("pin")], weak: false },
    { name: "UnverifiedUsers", attributes: [], weak: false },
  ],
  relationships: [
    { name: "DepartmentsUnits", kind: "1:N", left: end("Departments", "1"), right: end("Units", "N", true), attributes: [], identifying: true },
    { name: "UnitsUsers", kind: "1:N", left: end("Units", "1"), right: end("Users", "N", true), attributes: [], identifying: true },
    { name: "UnverifiedUsersR3", kind: "1:N", left: end("VerifiedUsers", "1"), right: end("UnverifiedUsers", "N", true), attributes: [], identifying: false },
  ],
  specializations: [{ parent: "Users", children: ["VerifiedUsers", "UnverifiedUsers"], disjoint: true, total: true, discriminator: "user_type" }],
};

const overlaps = (a: Box, b: Box) => !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y);

/** Does segment p-q pass through box b (shrunk a little, so touching an edge does not count)? */
function crosses(p: { x: number; y: number }, q: { x: number; y: number }, b: Box): boolean {
  for (let i = 1; i < 50; i++) {
    const x = p.x + ((q.x - p.x) * i) / 50;
    const y = p.y + ((q.y - p.y) * i) / 50;
    if (x > b.x + 2 && x < b.x + b.w - 2 && y > b.y + 2 && y < b.y + b.h - 2) return true;
  }
  return false;
}

describe("enhanced ER layout", () => {
  it("puts owners and supertypes above what depends on them, and subtypes on one row", () => {
    expect(layers(USERS)).toEqual({ Departments: 0, Units: 1, Users: 2, VerifiedUsers: 3, UnverifiedUsers: 3 });
    const l = crowLayout(USERS);
    expect(l.boxes.VerifiedUsers.y).toBe(l.boxes.UnverifiedUsers.y);
    expect(l.boxes.Departments.y).toBeLessThan(l.boxes.Units.y);
  });

  it("names what a weak entity or subtype takes its key from", () => {
    expect(inheritsFrom(USERS, "Units")).toBe("Departments");
    expect(inheritsFrom(USERS, "VerifiedUsers")).toBe("Users");
    expect(inheritsFrom(USERS, "Departments")).toBeNull();
  });

  for (const [label, model] of [["users", USERS], ["university", UNI]] as const) {
    it(`draws no relationship line through a third box (${label})`, () => {
      const l = crowLayout(model);
      const mid = (b: Box) => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });
      for (const r of model.relationships) {
        if (r.left.entity === r.right.entity) continue;
        for (const [name, b] of Object.entries(l.boxes)) {
          if (name === r.left.entity || name === r.right.entity) continue;
          expect(crosses(mid(l.boxes[r.left.entity]), mid(l.boxes[r.right.entity]), b), `${r.name} crosses ${name}`).toBe(false);
        }
      }
    });

    it(`keeps relationship labels off boxes and off each other (${label})`, () => {
      const l = crowLayout(model);
      const labels = Object.values(crowLabels(model, l));
      expect(labels.length).toBe(model.relationships.filter((r) => r.left.entity !== r.right.entity).length);
      for (const [i, a] of labels.entries()) {
        for (const b of Object.values(l.boxes)) expect(overlaps(a, b)).toBe(false);
        for (const b of labels.slice(i + 1)) expect(overlaps(a, b)).toBe(false);
      }
    });
  }

  it("puts each Chen ISA circle between the supertype and its subtypes", () => {
    const l = chenLayout(USERS);
    const c = l.isa.Users;
    expect(c.y).toBeGreaterThan(l.entities.Users.y);
    expect(c.y).toBeLessThan(l.entities.VerifiedUsers.y);
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

describe("designFileProblem", async () => {
  const { designFileProblem, MAX_DESIGN_FILE } = await import("./er");
  it("accepts any non-empty file up to the limit", () => {
    expect(designFileProblem(1)).toBeNull();
    expect(designFileProblem(MAX_DESIGN_FILE)).toBeNull();
  });
  it("explains empty and oversized files", () => {
    expect(designFileProblem(0)).toBe("That file is empty.");
    expect(designFileProblem(MAX_DESIGN_FILE + 1)).toBe("Files can be at most 1 MB here; for bigger dumps, upload them on Your data.");
  });
});
