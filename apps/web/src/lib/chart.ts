/**
 * Picks a chart from the shape of a query result, or none.
 *
 * Only shapes that can be drawn honestly get a chart (design_principles.md §2, §5):
 * one label column plus one number -> bars from zero, sorted; a time column plus a number ->
 * a line in time order; a single value -> the number itself. Anything else stays a table.
 */

export type Cell = string | number | boolean | null;

export type ChartSpec =
  | { kind: "number"; value: number; label: string }
  | { kind: "bar"; bars: { label: string; value: number }[]; measure: string; dimension: string }
  | { kind: "line"; points: { x: number | string; y: number }[]; measure: string; dimension: string }
  | { kind: "table" };

const MAX_BARS = 40;
const TIME_NAME = /^(year|date|day|month|week|hour|time)s?$|_(date|at|day|month|year)$/i;
const ISO_DATE = /^\d{4}-\d{2}(-\d{2})?([T ][\d:.]+)?/;

/** Column names as people would say them: "count_star()" -> "count". */
export function humanize(name: string): string {
  return name.replace(/\(\)$/, "").replace(/_star$/, "").replace(/_/g, " ").trim();
}

export function pickChart(columns: string[], rows: Cell[][]): ChartSpec {
  if (rows.length === 0) return { kind: "table" };

  if (columns.length === 1 && rows.length === 1 && typeof rows[0][0] === "number") {
    return { kind: "number", value: rows[0][0], label: humanize(columns[0]) };
  }
  if (columns.length !== 2 || !rows.some((r) => typeof r[1] === "number")) {
    return { kind: "table" };
  }

  const [dimension, measure] = [humanize(columns[0]), humanize(columns[1])];
  const usable = rows.filter((r) => r[0] !== null && typeof r[1] === "number");

  if (isTime(columns[0], usable.map((r) => r[0]))) {
    const points = usable
      .map((r) => ({ x: r[0] as number | string, y: r[1] as number }))
      .sort((a, b) => (a.x < b.x ? -1 : a.x > b.x ? 1 : 0));
    return { kind: "line", points, measure, dimension };
  }

  if (usable.length > MAX_BARS) return { kind: "table" };
  const bars = usable
    .map((r) => ({ label: String(r[0]), value: r[1] as number }))
    .sort((a, b) => b.value - a.value);
  return { kind: "bar", bars, measure, dimension };
}

function isTime(name: string, values: Cell[]): boolean {
  if (TIME_NAME.test(name)) return true;
  return values.length > 0 && values.every((v) => typeof v === "string" && ISO_DATE.test(v));
}
