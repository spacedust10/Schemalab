/** Types for /assignments and small helpers for the pages. */

export type Explanation = { title: string; explanation: string; fix: string };
export type TestResult = { name: string; passed: boolean; hint?: string; missing?: unknown[][]; extra?: unknown[][] };
export type Grade = {
  score: number;
  parts: { runs: number; columns: number; rows: number };
  error: Explanation | null;
  tests: TestResult[];
  attempts_left: number | null;
  best: number | null;
};
export type Summary = { id: string; title: string; mode: "tutor" | "exam"; mine: boolean };
export type SampleTable = { columns: string[]; rows: unknown[][] };
export type AssignmentView = {
  id: string;
  title: string;
  prompt: string;
  schema: string;
  tables: Record<string, SampleTable>;
  tests: string[];
  ordered: boolean;
  mode: "tutor" | "exam";
  attempts_left: number | null;
  best: number | null;
  reference?: string;
  hidden?: { name: string; data: string }[];
};
export type Checked = { errors: string[]; warnings: string[] };

/** sample:<key> or the API's random ids: letters, digits, _ and -, at most 40. */
export function validId(id: string): boolean {
  return /^(sample:)?[A-Za-z0-9_-]{1,32}$/.test(id) && id.length <= 40;
}

export function scoreLine(g: Pick<Grade, "score" | "parts">): string {
  const p = g.parts;
  return `${g.score} of 100: ${p.runs} for running, ${p.columns} for the columns, ${p.rows} for the rows.`;
}
