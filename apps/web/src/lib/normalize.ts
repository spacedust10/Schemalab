/** Types for POST /normalize and helpers for the Normalize page. */
import type { Dataset } from "./upload";

export const MAX_ATTRIBUTES = 16; // matches normalize.MAX_ATTRIBUTES in the API

export type Dep = { lhs: string[]; rhs: string[] };
export type Form = "1NF" | "2NF" | "3NF" | "BCNF";
export type NFRelation = { attrs: string[]; fds: Dep[]; keys: string[][]; form: Form };
export type Decomposition = {
  relations: NFRelation[];
  steps: (Dep & { relation: string[]; split: [string[], string[]] })[];
  lossless: boolean;
  chase_table: string[][];
  kept: Dep[];
  lost: Dep[];
};
export type Analysis = {
  attributes: string[];
  dependencies: Dep[];
  cover: Dep[];
  keys: string[][];
  prime: string[];
  form: Form;
  violations: (Dep & { form: Form; reason: string })[];
  bcnf: Decomposition;
  third: Decomposition;
  closure?: { of: string[]; is: string[]; superkey: boolean };
};

export function showAttrs(attrs: string[]): string {
  return attrs.join(attrs.every((a) => a.length === 1) ? "" : ", ");
}

export function showFD(d: Dep): string {
  return `${showAttrs(d.lhs)} → ${showAttrs(d.rhs)}`;
}

/** A relation and its known dependency from an uploaded table: the recorded primary key. */
export function fromDataset(t: Dataset): { schema: string; dependencies: string } {
  const names = t.columns.map((c) => c.name);
  if (names.length > MAX_ATTRIBUTES) {
    throw new Error(`${t.name} has ${names.length} columns; the analysis handles at most ${MAX_ATTRIBUTES}.`);
  }
  const key = t.columns.filter((c) => c.note === "primary key" || c.note === "part of the primary key").map((c) => c.name);
  const rest = names.filter((n) => !key.includes(n));
  const dependencies =
    key.length && rest.length
      ? `${key.join(", ")} -> ${rest.join(", ")}`
      : `# No key was recorded for ${t.name}. Add dependencies, e.g.\n# ${names[0]} -> ${names.slice(1).join(", ") || names[0]}`;
  return { schema: `${t.name}(${names.join(", ")})`, dependencies };
}

export const EXAMPLES = [
  { label: "Transitive dependency", schema: "R(A, B, C)", dependencies: "A -> B\nB -> C" },
  { label: "Four candidate keys", schema: "R(A, B, C, D, E)", dependencies: "A -> B, C\nC, D -> E\nB -> D\nE -> A" },
  { label: "3NF but not BCNF", schema: "R(A, B, C)", dependencies: "A, B -> C\nC -> B" },
  {
    label: "Student records",
    schema: "Enrolled(sid, name, major, course, grade, instructor)",
    dependencies: "sid -> name, major\nsid, course -> grade\ncourse -> instructor",
  },
];
