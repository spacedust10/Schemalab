import { describe, expect, it } from "vitest";
import { humanize, pickChart } from "./chart";

describe("pickChart", () => {
  it("shows a single value as a number", () => {
    expect(pickChart(["count_star()"], [[273128]])).toEqual({ kind: "number", value: 273128, label: "count" });
  });

  it("shows label and value pairs as bars sorted largest first", () => {
    const spec = pickChart(["Borough", "trips"], [["Queens", 273128], ["Manhattan", 2646948], ["Bronx", 30]]);
    expect(spec.kind).toBe("bar");
    if (spec.kind !== "bar") return;
    expect(spec.bars.map((b) => b.label)).toEqual(["Manhattan", "Queens", "Bronx"]);
    expect(spec.measure).toBe("trips");
  });

  it("uses a line when the first column is a year or date", () => {
    expect(pickChart(["year", "pct"], [[2015, 7.9], [2014, 11]]).kind).toBe("line");
    expect(pickChart(["day", "n"], [["2024-01-02", 5], ["2024-01-01", 3]]).kind).toBe("line");
  });

  it("orders line points by time", () => {
    const spec = pickChart(["year", "pct"], [[2015, 7.9], [2014, 11]]);
    if (spec.kind !== "line") throw new Error(spec.kind);
    expect(spec.points.map((p) => p.x)).toEqual([2014, 2015]);
  });

  it("falls back to the table for shapes it cannot chart honestly", () => {
    expect(pickChart(["a", "b", "c"], [[1, 2, 3]]).kind).toBe("table");
    expect(pickChart(["name", "city"], [["a", "b"]]).kind).toBe("table");
    expect(pickChart(["x"], []).kind).toBe("table");
    const many = Array.from({ length: 41 }, (_, i) => [`k${i}`, i]);
    expect(pickChart(["k", "v"], many).kind).toBe("table");
  });

  it("drops rows with a missing value from bars", () => {
    const spec = pickChart(["b", "v"], [["x", 1], [null, 2], ["y", null]]);
    if (spec.kind !== "bar") throw new Error(spec.kind);
    expect(spec.bars).toEqual([{ label: "x", value: 1 }]);
  });
});

describe("humanize", () => {
  it.each([
    ["count_star()", "count"],
    ["avg_fare", "avg fare"],
    ["num_trips", "num trips"],
    ["Borough", "Borough"],
  ])("%s -> %s", (raw, want) => expect(humanize(raw)).toBe(want));
});
