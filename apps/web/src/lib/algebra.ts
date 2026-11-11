/** Types for POST /algebra and GET /constraints, and the query-tree layout. */
import type { Result } from "./trace";

export type TreeNode = { op: string; label: string; children: TreeNode[] };
export type AlgebraResult = {
  ra: string;
  sql: string;
  tree: TreeNode;
  notes: string[];
  result: Result | null;
  run_error: string | null;
};
export type Suggestion = { kind: string; columns: string[]; sql: string; evidence: string };

export type Placed = { label: string; op: string; x: number; y: number; w: number; h: number };
export type Edge = { x1: number; y1: number; x2: number; y2: number };

const GAP = 22;
const LEVEL = 74;
const HEIGHT = 32;
const PAD = 12;

/**
 * Each subtree gets its own horizontal band, as wide as its children side by side or its own
 * label, whichever is wider; a parent sits over the middle of its children. Nothing on one level
 * can overlap, however long a σ or γ label is.
 */
export function treeLayout(root: TreeNode): { nodes: Placed[]; edges: Edge[]; width: number; height: number } {
  const w = (n: TreeNode) => Math.max(52, n.label.length * 7.6 + 26);
  const span = (n: TreeNode): number =>
    Math.max(w(n), n.children.reduce((sum, c) => sum + span(c), 0) + GAP * Math.max(0, n.children.length - 1));
  const nodes: Placed[] = [];
  const edges: Edge[] = [];
  let depth = 0;
  const place = (n: TreeNode, left: number, level: number): Placed => {
    depth = Math.max(depth, level);
    const total = span(n);
    const block = n.children.reduce((sum, c) => sum + span(c), 0) + GAP * Math.max(0, n.children.length - 1);
    let cursor = left + (total - block) / 2;
    const kids = n.children.map((c) => {
      const placed = place(c, cursor, level + 1);
      cursor += span(c) + GAP;
      return placed;
    });
    // Over the middle of its children, but never outside its own band.
    const middle = kids.length ? (kids[0].x + kids[kids.length - 1].x) / 2 : left + total / 2;
    const x = Math.min(Math.max(middle, left + w(n) / 2), left + total - w(n) / 2);
    const me: Placed = { label: n.label, op: n.op, x, y: PAD + level * LEVEL + HEIGHT / 2, w: w(n), h: HEIGHT };
    for (const k of kids) edges.push({ x1: me.x, y1: me.y + HEIGHT / 2, x2: k.x, y2: k.y - HEIGHT / 2 });
    nodes.push(me);
    return me;
  };
  place(root, PAD, 0);
  return { nodes, edges, width: span(root) + PAD * 2, height: PAD * 2 + depth * LEVEL + HEIGHT };
}

export const SYMBOLS = ["σ", "π", "ρ", "γ", "τ", "⋈", "×", "∪", "−", "∩", "∧", "∨", "¬", "≠", "≤", "≥"];

export const ALGEBRA_EXAMPLES = [
  { label: "Airport zones", mode: "ra", text: "π[Zone, Borough](σ[service_zone = 'Airports'](taxi_zones))" },
  { label: "Zones per borough", mode: "ra", text: "γ[Borough; count(*) AS zones](taxi_zones)" },
  { label: "Boroughs without airports", mode: "ra", text: "π[Borough](taxi_zones) − π[Borough](σ[service_zone = 'Airports'](taxi_zones))" },
  {
    label: "Trips by pickup borough (SQL)",
    mode: "sql",
    text: "SELECT z.Borough, count(*) AS trips\nFROM taxi_trips t JOIN taxi_zones z ON t.PULocationID = z.LocationID\nGROUP BY z.Borough\nORDER BY trips DESC",
  },
] as const;
