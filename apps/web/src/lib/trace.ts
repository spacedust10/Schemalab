/** Folds the /ask event stream into what the Ask view draws: plan, attempts, result, answer. */
import type { Cell } from "./chart";

type Stage = "guard" | "run";

export type AgentEvent =
  | { step: "schema"; tables: number }
  | { step: "plan"; text: string }
  | { step: "sql"; sql: string; attempt: number }
  | { step: "result"; columns: string[]; rows: Cell[][]; truncated: boolean }
  | { step: "retry"; stage: Stage; message: string; sql: string; attempt: number }
  | { step: "critic"; ok: boolean | null; issue: string | null; attempt: number }
  | { step: "answer"; text: string; confidence: Confidence; provenance: Provenance }
  | { step: "error"; stage: Stage | "model" | "data"; message: string; sql?: string; attempts?: number };

export type Confidence = { label: "low" | "medium" | "high"; reasons: string[] };
export type Provenance = {
  sql: string;
  tables: string[];
  attempts: number;
  models: string[];
  results_shared_with_model: boolean;
  categories_shared_with_model: boolean;
};
export type Result = { columns: string[]; rows: Cell[][]; truncated: boolean };

export type Attempt = {
  n: number;
  sql: string;
  status: "rejected" | "failed" | "ran";
  message?: string;
  rows?: Cell[][];
  critic?: { ok: boolean | null; issue: string | null };
};

export type Trace = {
  plan?: string;
  attempts: Attempt[];
  result?: Result;
  answer?: Extract<AgentEvent, { step: "answer" }>;
  error?: { stage: string; message: string };
  done: boolean;
};

export const emptyTrace: Trace = { attempts: [], done: false };

export function reduceTrace(trace: Trace | undefined, event: AgentEvent): Trace {
  const t: Trace = trace ? { ...trace, attempts: [...trace.attempts] } : { ...emptyTrace, attempts: [] };
  const update = (n: number, patch: Partial<Attempt>) => {
    const i = t.attempts.findIndex((a) => a.n === n);
    if (i >= 0) t.attempts[i] = { ...t.attempts[i], ...patch };
  };

  switch (event.step) {
    case "plan":
      t.plan = event.text;
      break;
    case "retry":
      t.attempts.push({ n: event.attempt, sql: event.sql, ...failure(event.stage, event.message) });
      break;
    case "sql":
      t.attempts.push({ n: event.attempt, sql: event.sql, status: "ran" });
      break;
    case "result": {
      t.result = { columns: event.columns, rows: event.rows, truncated: event.truncated };
      const last = t.attempts[t.attempts.length - 1];
      if (last) update(last.n, { rows: event.rows });
      break;
    }
    case "critic":
      update(event.attempt, { critic: { ok: event.ok, issue: event.issue } });
      break;
    case "answer":
      t.answer = event;
      t.done = true;
      break;
    case "error":
      if ((event.stage === "guard" || event.stage === "run") && event.sql !== undefined) {
        const n = event.attempts ?? t.attempts.length + 1;
        t.attempts.push({ n, sql: event.sql, ...failure(event.stage, event.message) });
      }
      t.error = { stage: event.stage, message: event.message };
      t.done = true;
      break;
    case "schema":
      break;
  }
  return t;
}

function failure(stage: Stage, message: string): Pick<Attempt, "status" | "message"> {
  return { status: stage === "guard" ? "rejected" : "failed", message };
}
