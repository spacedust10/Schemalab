import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST } from "../app/api/normalize/route";
import { SESSION_COOKIE } from "./api";
import { fromDataset, showAttrs, showFD } from "./normalize";
import type { ColumnProfile, Dataset } from "./upload";

const col = (name: string, note: string | null = null): ColumnProfile => ({
  name, type: "INTEGER", min: null, max: null, distinct: 0, null_percent: 0, note,
});

describe("fromDataset", () => {
  it("uses the columns as attributes and the primary key as a dependency", () => {
    const t: Dataset = { name: "orders", rows: 3, columns: [col("id", "primary key"), col("customer_id", "references customers(id)"), col("total")] };
    expect(fromDataset(t)).toEqual({ schema: "orders(id, customer_id, total)", dependencies: "id -> customer_id, total" });
  });

  it("joins a composite key on the left side", () => {
    const t: Dataset = { name: "enrolled", rows: 1, columns: [col("sid", "part of the primary key"), col("cid", "part of the primary key"), col("grade")] };
    expect(fromDataset(t).dependencies).toBe("sid, cid -> grade");
  });

  it("leaves a hint when no key was recorded", () => {
    const t: Dataset = { name: "sales", rows: 1, columns: [col("region"), col("amount")] };
    expect(fromDataset(t).dependencies).toBe("# No key was recorded for sales. Add dependencies, e.g.\n# region -> amount");
  });

  it("refuses tables too wide to analyze", () => {
    const t: Dataset = { name: "wide", rows: 1, columns: Array.from({ length: 17 }, (_, i) => col(`c${i}`)) };
    expect(() => fromDataset(t)).toThrow("wide has 17 columns; the analysis handles at most 16.");
  });
});

describe("formatting", () => {
  it("runs one-letter names together and separates longer ones", () => {
    expect(showAttrs(["A", "B"])).toBe("AB");
    expect(showAttrs(["sid", "name"])).toBe("sid, name");
    expect(showFD({ lhs: ["A", "B"], rhs: ["C"] })).toBe("AB → C");
  });
});

describe("normalize route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("passes the JSON body through with the token and refuses cross-site posts", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return Response.json({ form: "BCNF" });
    });
    const body = JSON.stringify({ schema: "R(A)", dependencies: "" });
    const headers = { cookie: `${SESSION_COOKIE}=tok`, "content-type": "application/json" };
    const ok = await POST(new NextRequest("http://localhost:3000/api/normalize", { method: "POST", headers: { ...headers, origin: "http://localhost:3000" }, body }));
    expect(await ok.json()).toEqual({ form: "BCNF" });
    expect(calls[0].url).toBe("http://localhost:8000/normalize");
    expect(calls[0].init.body).toBe(body);
    const evil = await POST(new NextRequest("http://localhost:3000/api/normalize", { method: "POST", headers: { ...headers, origin: "https://evil.example" }, body }));
    expect(evil.status).toBe(403);
    expect(calls).toHaveLength(1);
  });
});
