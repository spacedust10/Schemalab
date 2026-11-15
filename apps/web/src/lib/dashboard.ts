/** Types for the teacher's dashboard, and the score histogram. */

export type StudentRow = { email: string; attempts: number; best: number; latest: number; last_at: string };
export type TestRate = { name: string; passed: number; of: number };
export type Dashboard = {
  title: string;
  students: StudentRow[];
  tests: TestRate[];
  summary: { students: number; submissions: number; mean?: number; median?: number; min?: number; max?: number };
};
export type Answer = { sql: string; score: number; at: string };

/** Counts of scores in bands 0-9, 10-19, ..., 90-100 (100 joins the top band). */
export function histogram(scores: number[]): number[] {
  const bands = Array(10).fill(0) as number[];
  for (const s of scores) bands[Math.min(9, Math.floor(s / 10))] += 1;
  return bands;
}

export function median(scores: number[]): number {
  const s = [...scores].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** The dashboard's opening finding: how many reached full marks, and where the middle is. */
export function takeaway(scores: number[]): string {
  const full = scores.filter((s) => s === 100).length;
  if (full === scores.length) return `All ${scores.length} students reached full marks.`;
  const first = full === 0 ? "No student has full marks yet." : `${full} of ${scores.length} students reached full marks.`;
  const m = median(scores);
  return `${first} Half scored ${Number.isInteger(m) ? m : m.toFixed(1)} or more.`;
}
