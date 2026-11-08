import { describe, expect, it } from "vitest";
import { createSSEParser } from "./sse";

describe("createSSEParser", () => {
  it("emits one event per data line", () => {
    const got: unknown[] = [];
    const push = createSSEParser((e) => got.push(e));
    push('data: {"step":"schema"}\n\ndata: {"step":"plan","text":"x"}\n\n');
    expect(got).toEqual([{ step: "schema" }, { step: "plan", text: "x" }]);
  });

  it("joins events split across chunks", () => {
    const got: unknown[] = [];
    const push = createSSEParser((e) => got.push(e));
    push('data: {"step":"sq');
    push('l","sql":"SELECT 1"}\n');
    push("\n");
    expect(got).toEqual([{ step: "sql", sql: "SELECT 1" }]);
  });

  it("ignores comments, blank lines and other fields", () => {
    const got: unknown[] = [];
    const push = createSSEParser((e) => got.push(e));
    push(': keep-alive\n\nevent: x\nretry: 10\ndata: {"step":"answer"}\n\n');
    expect(got).toEqual([{ step: "answer" }]);
  });
});
