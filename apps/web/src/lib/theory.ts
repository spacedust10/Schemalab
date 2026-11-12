/** Types for /schedules and /recovery, and the precedence graph's geometry. */

export type GraphEdge = { from: number; to: number; items: string[] };
export type Verdict = { ok: boolean; reason: string | null };
export type Locking = { ok: boolean; step: number | null; reason: string | null; schedule: string[] };
export type ScheduleAnalysis = {
  operations: string[];
  transactions: number[];
  aborted: number[];
  edges: GraphEdge[];
  cycle: number[] | null;
  conflict_serializable: boolean;
  serial_orders: number[][];
  view_serializable: boolean | null;
  view_order: number[] | null;
  recoverable: Verdict;
  cascadeless: Verdict;
  strict: Verdict;
  locking: Record<string, Locking>;
};

export type LogRecord = {
  lsn: number;
  kind: string;
  txn: number | null;
  page: string | null;
  before: string | null;
  after: string | null;
  prev: number | null;
  undo_next: number | null;
};
export type Recovery = {
  crashed: boolean;
  log: LogRecord[];
  at_crash: Record<string, { value: string; page_lsn: number }> | null;
  analysis: {
    start_lsn: number;
    transactions: { txn: number; status: string; last_lsn: number }[];
    dirty_pages: { page: string; rec_lsn: number }[];
    losers: number[];
    winners: number[];
  } | null;
  redo: { start_lsn: number | null; steps: { lsn: number; kind: string; page: string; value: string; action: string; reason: string }[] } | null;
  undo: { lsn: number; kind: string; txn: number; page?: string; value?: string; undoes?: number; undo_next?: number | null; note?: string }[];
  final: Record<string, string>;
};

const R = 24;
const ARROW_GAP = 4; // the arrowhead needs a little room in front of the node

export type PlacedEdge = GraphEdge & { x1: number; y1: number; x2: number; y2: number; bend: number };

/**
 * Transactions on a circle; each arrow runs border to border. When both directions of a pair
 * exist (a cycle of two), each bends to its own side so both stay readable.
 */
export function graphLayout(txns: number[], edges: GraphEdge[]) {
  const n = txns.length;
  const radius = n <= 1 ? 0 : Math.max(70, n * 26);
  const size = 2 * radius + 2 * R + 40;
  const nodes = txns.map((txn, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, n);
    return { txn, x: size / 2 + radius * Math.cos(angle), y: size / 2 + radius * Math.sin(angle) };
  });
  const at = (t: number) => nodes.find((p) => p.txn === t) ?? nodes[0];
  const placed: PlacedEdge[] = edges.map((e) => {
    const a = at(e.from);
    const b = at(e.to);
    const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    const ux = (b.x - a.x) / len;
    const uy = (b.y - a.y) / len;
    const paired = edges.some((o) => o.from === e.to && o.to === e.from);
    return {
      ...e,
      x1: a.x + ux * R,
      y1: a.y + uy * R,
      x2: b.x - ux * (R + ARROW_GAP),
      y2: b.y - uy * (R + ARROW_GAP),
      bend: paired ? 34 : 0,
    };
  });
  return { nodes, edges: placed, r: R, width: size, height: size };
}

export const SCHEDULE_EXAMPLES = [
  { label: "Serializable, and 2PL can produce it", text: "r1(A) w1(A) r2(A) w2(A) r1(B) w1(B) r2(B) w2(B) c1 c2" },
  { label: "A cycle: not serializable", text: "r1(A) r2(A) w1(A) w2(A) c1 c2" },
  { label: "View but not conflict serializable", text: "r1(A) w2(A) w1(A) w3(A) c1 c2 c3" },
  { label: "Not recoverable", text: "w1(A) r2(A) c2 c1" },
  { label: "Cascadeless but not strict", text: "w1(A) w2(A) c1 c2" },
];

export const RECOVERY_EXAMPLE = `# Three transactions, a checkpoint, one page flushed, then a crash.
T1 begin
T1 write A 10 20
T2 begin
T2 write B 5 7
checkpoint
T1 commit
T3 begin
T3 write A 20 30
flush A
T2 write C 1 2
crash`;
