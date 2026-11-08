import { describe, expect, it } from "vitest";
import { pickChart } from "./chart";
import { takeaway } from "./takeaway";

describe("takeaway", () => {
  it("names the leader and how far ahead it is", () => {
    const spec = pickChart(["Borough", "trips"], [["Manhattan", 2646948], ["Queens", 273128]]);
    expect(takeaway(spec)).toBe("Manhattan has the most trips: 2,646,948, 9.7× Queens.");
  });

  it("says when the top two are close", () => {
    const spec = pickChart(["k", "sales"], [["a", 100], ["b", 98]]);
    expect(takeaway(spec)).toBe("a has the most sales: 100, just ahead of b (98).");
  });

  it("describes a trend from first to last point", () => {
    const spec = pickChart(["year", "pct uninsured"], [[2014, 11], [2022, 6.4]]);
    expect(takeaway(spec)).toBe("pct uninsured fell from 11 in 2014 to 6.4 in 2022.");
  });

  it("states a single number", () => {
    expect(takeaway(pickChart(["count_star()"], [[273128]]))).toBe("count: 273,128.");
  });

  it("returns null when there is nothing honest to say", () => {
    expect(takeaway(pickChart(["a", "b", "c"], [[1, 2, 3]]))).toBeNull();
    expect(takeaway(pickChart(["k", "v"], [["only", 1]]))).toBe("only: 1.");
  });
});
