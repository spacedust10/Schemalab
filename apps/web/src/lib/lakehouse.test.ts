import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET, POST, PUT } from "../app/api/lakehouse/[[...path]]/route";
import { SESSION_COOKIE } from "./api";
import { busy, changeLine, columnList, type Run, runLine, upstreamPath } from "./lakehouse";

const q = (s = "") => new URLSearchParams(s);

describe("which lakehouse calls the browser may make", () => {
  it("maps known paths to the API and keeps only the parameters each takes", () => {
    expect(upstreamPath("GET", [], q("x=1"))).toBe("/lakehouse");
    expect(upstreamPath("GET", ["tables", "gold", "daily_revenue"], q("version=7&x=1"))).toBe("/lakehouse/tables/gold/daily_revenue?version=7");
    expect(upstreamPath("GET", ["tables", "bronze", "orders"], q("version=abc"))).toBe("/lakehouse/tables/bronze/orders");
    expect(upstreamPath("POST", ["files"], q("name=Orders 2026.csv&table=orders&x=1"))).toBe("/lakehouse/files?name=Orders+2026.csv&table=orders");
    expect(upstreamPath("POST", ["files"], q("name=o.csv"))).toBe("/lakehouse/files?name=o.csv");
    expect(upstreamPath("POST", ["quakes"], q())).toBe("/lakehouse/quakes");
    expect(upstreamPath("POST", ["sheets"], q())).toBe("/lakehouse/sheets");
    expect(upstreamPath("POST", ["transform"], q())).toBe("/lakehouse/transform");
    expect(upstreamPath("PUT", ["models"], q())).toBe("/lakehouse/models");
  });

  it("refuses everything else", () => {
    expect(upstreamPath("POST", [], q())).toBeNull();
    expect(upstreamPath("GET", ["quakes"], q())).toBeNull();
    expect(upstreamPath("POST", ["files"], q())).toBeNull(); // a file needs its name
    expect(upstreamPath("GET", ["tables", "public", "x"], q())).toBeNull();
    expect(upstreamPath("GET", ["tables", "gold", "../../auth"], q())).toBeNull();
    expect(upstreamPath("GET", ["tables", "gold"], q())).toBeNull();
    expect(upstreamPath("PUT", ["models", "x"], q())).toBeNull();
    expect(upstreamPath("POST", ["..", "auth", "logout"], q())).toBeNull();
  });
});

describe("lakehouse routes", () => {
  afterEach(() => vi.unstubAllGlobals());
  const json = { cookie: `${SESSION_COOKIE}=tok`, "content-type": "application/json", origin: "http://localhost:3000" };
  const params = (...path: string[]) => ({ params: Promise.resolve({ path }) });

  it("forwards allowed calls with the session token", async () => {
    const calls: [string, string | null][] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push([url, new Headers(init.headers).get("authorization")]);
      return Response.json({}, { status: 202 });
    });
    const res = await POST(new NextRequest("http://localhost:3000/api/lakehouse/quakes", { method: "POST", headers: json, body: "{}" }), params("quakes"));
    expect(res.status).toBe(202);
    await PUT(new NextRequest("http://localhost:3000/api/lakehouse/models", { method: "PUT", headers: json, body: "{}" }), params("models"));
    await GET(new NextRequest("http://localhost:3000/api/lakehouse", { headers: json }), { params: Promise.resolve({}) });
    expect(calls).toEqual([
      ["http://localhost:8000/lakehouse/quakes", "Bearer tok"],
      ["http://localhost:8000/lakehouse/models", "Bearer tok"],
      ["http://localhost:8000/lakehouse", "Bearer tok"],
    ]);
  });

  it("refuses unknown paths, cross-site posts and anonymous calls", async () => {
    vi.stubGlobal("fetch", async () => Response.json({}));
    const evil = { ...json, origin: "https://evil.example" };
    expect((await POST(new NextRequest("http://localhost:3000/api/lakehouse/x", { method: "POST", headers: json, body: "{}" }), params("..", "auth"))).status).toBe(404);
    expect((await POST(new NextRequest("http://localhost:3000/api/lakehouse/quakes", { method: "POST", headers: evil, body: "{}" }), params("quakes"))).status).toBe(403);
    const anon = { "content-type": "application/json", origin: "http://localhost:3000" };
    expect((await POST(new NextRequest("http://localhost:3000/api/lakehouse/quakes", { method: "POST", headers: anon, body: "{}" }), params("quakes"))).status).toBe(401);
  });

  it("sends files as raw bytes only", async () => {
    vi.stubGlobal("fetch", async () => Response.json({}, { status: 202 }));
    const asJson = await POST(new NextRequest("http://localhost:3000/api/lakehouse/files?name=a.csv", { method: "POST", headers: json, body: "{}" }), params("files"));
    expect(asJson.status).toBe(415);
    const raw = { ...json, "content-type": "application/octet-stream" };
    const ok = await POST(new NextRequest("http://localhost:3000/api/lakehouse/files?name=a.csv", { method: "POST", headers: raw, body: "id\n1\n" }), params("files"));
    expect(ok.status).toBe(202);
  });
});

const run = (over: Partial<Run>): Run => ({
  id: 1, kind: "file", label: "orders.csv", status: "done", result: null, error: null,
  created_at: "2026-11-14T00:00:00Z", finished_at: null, snapshot: null, ...over,
});

describe("run lines", () => {
  it("say what each run did", () => {
    expect(runLine(run({ result: { orders: 3, quakes: 1 } }))).toBe("orders: 3 rows, quakes: 1 row");
    expect(runLine(run({ result: {} }))).toBe("Nothing new to load.");
    expect(runLine(run({ status: "failed", error: "The file could not be read." }))).toBe("The file could not be read.");
    expect(runLine(run({ status: "running" }))).toBe("Running…");
    expect(runLine(run({ kind: "transform", result: { "silver.paid": 2, "gold.revenue": 1 } }))).toBe("silver.paid: 2 rows, gold.revenue: 1 row");
    expect(runLine(run({ result: { orders: 3 }, snapshot: 12 }))).toBe("orders: 3 rows, as snapshot #12");
  });

  it("name the run behind each snapshot", async () => {
    const { snapshotLabel } = await import("./lakehouse");
    const runs = [run({ label: "orders.csv", snapshot: 12 }), run({ label: "2 models", status: "failed", snapshot: null })];
    expect(snapshotLabel(12, runs)).toBe("Snapshot #12, after orders.csv");
    expect(snapshotLabel(11, runs)).toBe("Snapshot #11");
  });

  it("know when to keep checking", () => {
    expect(busy([run({}), run({ status: "running" })])).toBe(true);
    expect(busy([run({}), run({ status: "failed" })])).toBe(false);
  });
});

describe("snapshot lines", () => {
  it("say what changed in plain words", () => {
    expect(changeLine({ tables_created: ["gold.revenue"], inlined_insert: ["12"] })).toBe("created gold.revenue; added rows");
    expect(changeLine({ schemas_created: ["silver"] })).toBe("created layer silver");
    expect(changeLine({ tables_inserted_into: ["4"], tables_deleted_from: ["4"] })).toBe("added rows; removed rows");
    expect(changeLine({ tables_dropped: ["bronze.orders"] })).toBe("dropped bronze.orders");
    expect(changeLine({ something_new: ["x"] })).toBe("something new");
    expect(changeLine({})).toBe("no change");
  });

  it("keep the loader's own bookkeeping out of the way", () => {
    expect(changeLine({ schemas_created: ["main"] })).toBe("made the lake");
    expect(changeLine({ schemas_created: ["bronze_staging"] })).toBe("loader bookkeeping");
    expect(changeLine({ tables_created: ["bronze._dlt_version", "bronze.quakes", "bronze._dlt_loads"], inlined_insert: ["7"] })).toBe("created bronze.quakes; added rows");
    expect(changeLine({ tables_created: ["bronze_staging._dlt_version", "bronze_staging.quakes"], inlined_insert: ["4"] })).toBe("loader bookkeeping; added rows");
  });
});

describe("numeric columns", () => {
  it("are the ones whose values are all numbers, nulls aside", async () => {
    const { numericColumns } = await import("./lakehouse");
    expect(numericColumns([[1, "a", null, 2.5], [null, "b", 3, "x"]])).toEqual([true, false, true, false]);
    expect(numericColumns([[null]])).toEqual([false]);
    expect(numericColumns([])).toEqual([]);
  });
});

describe("the preview in the URL", () => {
  it("reads back what it wrote", async () => {
    const { previewQuery, previewFromQuery } = await import("./lakehouse");
    const q = previewQuery({ layer: "gold", name: "revenue", version: "12" });
    expect(q).toBe("table=gold.revenue&as_of=12");
    expect(previewFromQuery(new URLSearchParams(q))).toEqual({ layer: "gold", name: "revenue", version: "12" });
    expect(previewQuery({ layer: "bronze", name: "orders", version: "" })).toBe("table=bronze.orders");
    expect(previewQuery(null)).toBe("");
  });

  it("ignores anything it would not have written", async () => {
    const { previewFromQuery } = await import("./lakehouse");
    for (const bad of ["table=public.x", "table=gold", "table=gold.Bad", "table=gold.x.y", "", "x=1"]) {
      expect(previewFromQuery(new URLSearchParams(bad))).toBeNull();
    }
    expect(previewFromQuery(new URLSearchParams("table=gold.x&as_of=abc"))).toEqual({ layer: "gold", name: "x", version: "" });
  });
});

describe("check columns typed as a comma list", () => {
  it("keeps what is being typed and drops empty entries only when sent", () => {
    for (const typed of ["id, ", "id,", "id, amount", " id"]) expect(columnList.show(columnList.parse(typed))).toBe(typed);
    expect(columnList.parse("")).toEqual([]);
    expect(columnList.clean([" ", "id", "", "amount"])).toEqual(["id", "amount"]);
  });
});
