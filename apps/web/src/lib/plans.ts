/** Types for /lab/explain, and the example data the Plans page loads into the lab. */

export type PlanNode = {
  id: number;
  parent: number | null;
  depth: number;
  type: string;
  label: string;
  relation: string | null;
  index: string | null;
  loops: number;
  rows: number;
  est_rows: number;
  total_ms: number;
  self_ms: number;
  removed: number;
  filter: string | null;
  index_cond: string | null;
  join_filter: string | null;
  hash_cond: string | null;
  misestimate: number;
  hit_blocks: number;
  read_blocks: number;
};
export type Advice = { table: string; columns: string[]; sql: string; reason: string; node: number };
export type Explained = {
  summary: { planning_ms: number; execution_ms: number; nodes: PlanNode[]; hotspot: number; warnings: string[] };
  advice: Advice[];
};

export const EXAMPLE_SETUP = [
  "DROP TABLE IF EXISTS orders",
  "DROP TABLE IF EXISTS customers",
  "CREATE TABLE customers AS SELECT i AS id, 'customer ' || i AS name, (ARRAY['MI', 'OH', 'IN', 'IL'])[1 + i % 4] AS state FROM generate_series(0, 1999) i",
  "CREATE TABLE orders AS SELECT i AS id, i % 2000 AS customer_id, CASE WHEN i % 50 = 0 THEN 'returned' WHEN i % 7 = 0 THEN 'pending' ELSE 'shipped' END AS status, (i % 997) * 1.5 AS total, DATE '2024-01-01' + (i % 365) AS placed_on FROM generate_series(1, 200000) i",
  "ANALYZE customers",
  "ANALYZE orders",
];

export const PLAN_EXAMPLES = [
  { label: "Filter, sort and limit", sql: "SELECT id, total FROM orders WHERE status = 'returned' AND customer_id = 50 ORDER BY total DESC LIMIT 5" },
  { label: "A date range", sql: "SELECT count(*) FROM orders WHERE placed_on BETWEEN DATE '2024-02-01' AND DATE '2024-02-07'" },
  { label: "A join", sql: "SELECT c.name, o.total FROM customers c JOIN orders o ON o.customer_id = c.id WHERE c.id = 7" },
];

export function speedup(before: number, after: number): string {
  if (after <= 0) return "";
  const x = before / after;
  return x >= 1.5 ? `${x >= 10 ? Math.round(x) : x.toFixed(1)}× faster` : x <= 0.67 ? `${(1 / x).toFixed(1)}× slower` : "about the same";
}

/** The API's message, or what to do on this page when the lab has closed. */
export function planError(message: string): string {
  return message.startsWith("Start the lab")
    ? "Your lab is not running: it closes after 20 minutes idle or when the server restarts. Load the example data to start it again."
    : message;
}
