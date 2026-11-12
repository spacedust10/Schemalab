/** Types for /lab/* and the console history for each session. */
import type { Cell } from "./chart";

export type Outcome = {
  state: "done" | "waiting" | "error" | "idle";
  status?: string;
  columns?: string[];
  rows?: Cell[][];
  error?: string;
  code?: string;
  notices?: string[];
  transaction?: string;
  statement?: string;
};
export type Entry = { sql: string; outcome: Outcome; waited?: boolean };
export type SessionState = { pid: number | null; transaction: string; xid: string | null; blocked_by: string[] };
export type LabState = {
  sessions: Record<"A" | "B", SessionState>;
  locks: { session: string; type: string; relation: string | null; mode: string; granted: boolean; transaction: string | null }[];
  versions: { table: string; columns: string[]; rows: { xmin: string; xmax: string; ctid: string; values: Cell[] }[]; locked?: boolean }[];
};
export type Step = { session: "A" | "B"; sql: string; note: string; expect: Record<string, unknown>; then: Record<string, unknown> };
export type Scenario = { id: string; title: string; summary: string; setup: string[]; steps: Step[]; lesson: string };

export function record(history: Entry[], sql: string, outcome: Outcome): Entry[] {
  return [...history, { sql, outcome }];
}

/** A statement that was waiting has finished: replace its entry with the final outcome. */
export function settle(history: Entry[], outcome: Outcome): Entry[] {
  const i = history.findLastIndex((e) => e.outcome.state === "waiting");
  if (i < 0) return history;
  const next = [...history];
  next[i] = { ...next[i], outcome, waited: true };
  return next;
}

/** What a step's expectation says, in words. */
export function expectation(step: Step): string | null {
  const e = step.expect;
  const parts: string[] = [];
  if (e.state === "waiting") parts.push("waits for the other session");
  if (e.rows) parts.push(`returns ${JSON.stringify(e.rows)}`);
  if (e.status) parts.push(`reports ${e.status}`);
  if (e.code) parts.push(`fails with SQLSTATE ${e.code}`);
  if (step.then.status) parts.push(`then ${step.then.status}`);
  if (step.then.rows) parts.push(`then returns ${JSON.stringify(step.then.rows)}`);
  if (step.then.code) parts.push(`then fails with SQLSTATE ${step.then.code}`);
  return parts.length ? `Expect: ${parts.join(", ")}.` : null;
}

/**
 * xmax is a transaction id that deleted, updated or locked a row version. It only matters while
 * that transaction is open; afterwards it stays on the row but its effect is already settled.
 */
export function xmaxLabel(xmax: string, sessions: LabState["sessions"]): { text: string; live: boolean } {
  if (xmax === "0") return { text: "0", live: false };
  const owner = (["A", "B"] as const).find((n) => sessions[n]?.xid === xmax);
  return owner ? { text: `${xmax} · ${owner}'s open transaction`, live: true } : { text: `${xmax} · finished`, live: false };
}
