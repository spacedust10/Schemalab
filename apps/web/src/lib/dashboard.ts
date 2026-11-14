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
