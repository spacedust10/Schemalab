/** Types for /lab/patterns and /lab/pattern, and how a check reads in the comparison table. */
import { fmt } from "./format";

export type Check = { label: string; expected: unknown; actual: unknown; ok: boolean };
export type PatternResult = {
  name: string;
  mode: string;
  summary: string;
  checks: Check[];
  stats: Record<string, number>;
  ms: number;
};
export type PatternMode = { id: string; label: string; how: string };
export type Pattern = { id: string; title: string; question: string; modes: PatternMode[] };

/** The value, and on failure what it should have been, so the table never relies on colour. */
export function cell(check: Check): { text: string; ok: boolean } {
  const text = check.ok ? fmt(check.actual) : `${fmt(check.actual)}, expected ${fmt(check.expected)}`;
  return { text, ok: check.ok };
}
