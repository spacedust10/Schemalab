import { describe, expect, it } from "vitest";
import { emptyTrace, reduceTrace, type AgentEvent, type Trace } from "./trace";

const run = (events: AgentEvent[]): Trace => events.reduce(reduceTrace, emptyTrace);

describe("reduceTrace", () => {
  it("groups SQL, retries and critic verdicts by attempt", () => {
    const t = run([
      { step: "schema", tables: 3 },
      { step: "plan", text: "Join trips to zones." },
      { step: "retry", stage: "guard", message: "DROP is not allowed", sql: "DROP TABLE x", attempt: 1 },
      { step: "sql", sql: "SELECT 1", attempt: 2 },
      { step: "result", columns: ["n"], rows: [[1]], truncated: false },
      { step: "critic", ok: false, issue: "Wrong join.", attempt: 2 },
      { step: "sql", sql: "SELECT 2", attempt: 3 },
      { step: "result", columns: ["n"], rows: [[2]], truncated: false },
      { step: "critic", ok: true, issue: null, attempt: 3 },
    ]);
    expect(t.plan).toBe("Join trips to zones.");
    expect(t.attempts.map((a) => [a.n, a.status, a.critic?.ok ?? "-"])).toEqual([
      [1, "rejected", "-"],
      [2, "ran", false],
      [3, "ran", true],
    ]);
    expect(t.attempts[0].message).toBe("DROP is not allowed");
    expect(t.attempts[1].rows).toEqual([[1]]);
    expect(t.result?.rows).toEqual([[2]]); // the latest result is the one answered
    expect(t.done).toBe(false);
  });

  it("finishes on the answer", () => {
    const t = run([
      { step: "sql", sql: "SELECT 1", attempt: 1 },
      { step: "result", columns: ["n"], rows: [[1]], truncated: false },
      {
        step: "answer",
        text: "n is 1.",
        confidence: { label: "high", reasons: ["ok"] },
        provenance: { sql: "SELECT 1", tables: ["t"], attempts: 1, models: ["m"], results_shared_with_model: false, categories_shared_with_model: false },
      },
    ]);
    expect(t.done).toBe(true);
    expect(t.answer?.confidence.label).toBe("high");
  });

  it("records a final error and the attempt that caused it", () => {
    const t = run([
      { step: "retry", stage: "run", message: "no column", sql: "SELECT a", attempt: 1 },
      { step: "error", stage: "run", message: "no column b", sql: "SELECT b", attempts: 2 },
    ]);
    expect(t.done).toBe(true);
    expect(t.error).toEqual({ stage: "run", message: "no column b" });
    expect(t.attempts.map((a) => [a.n, a.status])).toEqual([
      [1, "failed"],
      [2, "failed"],
    ]);
  });

  it("treats a model or data error as final with no attempt", () => {
    const t = run([{ step: "error", stage: "model", message: "Ollama is down" }]);
    expect(t.error?.stage).toBe("model");
    expect(t.attempts).toEqual([]);
  });
});
