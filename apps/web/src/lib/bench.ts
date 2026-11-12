/** Types for /bench, and the sums the Benchmark page shows. */
import { fmt } from "./format";
import { speedup } from "./plans";

export type BenchQuery = {
  nr: number;
  duckdb_ms: number | null;
  postgres_ms: number | null;
  rows: number | null;
  agree: boolean | null;
  error: string | null;
};
export type BenchResult = {
  benchmark: string;
  name: string;
  scale: number;
  reps: number;
  ran_at: string;
  versions: { duckdb: string; postgres: number };
  queries: BenchQuery[];
  summary: {
    queries: number;
    agreed: number;
    failed: number;
    geomean_duckdb_ms: number | null;
    geomean_postgres_ms: number | null;
    duckdb_faster: number;
    compared: number;
  };
};
export type BenchJob = { running: boolean; benchmark: string | null; scale: number | null; message: string | null; error: string | null };
export type BenchOverview = {
  benchmarks: { id: string; name: string; scales: number[] }[];
  job: BenchJob;
  results: BenchResult[];
};
export type Comparison = { nr: number; ratio: number; faster: "DuckDB" | "PostgreSQL" };

/** PostgreSQL's time over DuckDB's, for the queries both engines finished. */
export function comparisons(r: BenchResult): Comparison[] {
  return r.queries.flatMap((q) =>
    q.duckdb_ms && q.postgres_ms
      ? [{ nr: q.nr, ratio: q.postgres_ms / q.duckdb_ms, faster: q.duckdb_ms < q.postgres_ms ? "DuckDB" : "PostgreSQL" }]
      : [],
  );
}

export function headline(r: BenchResult): string {
  const s = r.summary;
  const first = `DuckDB was faster on ${s.duckdb_faster} of ${s.compared} queries both engines finished.`;
  if (!s.geomean_duckdb_ms || !s.geomean_postgres_ms) return first;
  return `${first} Geometric mean ${fmt(s.geomean_duckdb_ms)} ms against ${fmt(s.geomean_postgres_ms)} ms: ${speedup(s.geomean_postgres_ms, s.geomean_duckdb_ms)}.`;
}
