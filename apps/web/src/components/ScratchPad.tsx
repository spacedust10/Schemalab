"use client";

import { useEffect, useRef, useState } from "react";
import type { SampleTable } from "@/lib/assignments";
import type { Scratchpad as Engine } from "@/lib/duckdb-browser";
import { DUCKDB_CORE } from "@/lib/duckdb-files";
import { fmt } from "@/lib/format";

/** Run queries on the sample rows in the browser: not graded, no attempts used. */
export function ScratchPad({ tables, starter }: { tables: Record<string, SampleTable>; starter: string }) {
  const engine = useRef<Engine | null>(null);
  const [sql, setSql] = useState(starter);
  const [state, setState] = useState<"off" | "loading" | "ready">("off");
  const [result, setResult] = useState<{ columns: string[]; rows: unknown[][] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cut = Object.entries(tables)
    .filter(([, t]) => t.total > t.rows.length)
    .map(([name, t]) => ({ name, shown: t.rows.length, total: t.total }));

  useEffect(() => () => void engine.current?.close(), []);

  async function start() {
    setState("loading");
    setError(null);
    try {
      const { openScratchpad } = await import("@/lib/duckdb-browser"); // only when asked: it is a large download
      engine.current = await openScratchpad(tables);
      setState("ready");
    } catch (e) {
      setState("off");
      setError((e as Error).message || "The scratchpad could not start.");
    }
  }

  async function run() {
    if (!engine.current) return;
    setError(null);
    try {
      setResult(await engine.current.run(sql));
    } catch (e) {
      setResult(null);
      setError(((e as Error).message || "The query failed.").split("\n")[0]);
    }
  }

  return (
    <section aria-labelledby="scratch">
      <h2 id="scratch">Scratchpad</h2>
      <p className="hint">
        Try queries on the sample rows, right in your browser. Nothing here is graded or counts as an attempt.
        It runs DuckDB {DUCKDB_CORE}; the grader runs a newer DuckDB, so a rare query may behave differently there.
      </p>
      {cut.length > 0 && (
        <p className="hint">
          Only the first rows of {cut.map((t) => `${t.name} (${t.shown} of ${t.total})`).join(", ")} are here; the
          grader uses every row, so counts and totals will differ.
        </p>
      )}
      {state === "off" && (
        <button type="button" className="button quiet" onClick={() => void start()}>
          Open the scratchpad (about an 8 MB download, once)
        </button>
      )}
      {state === "loading" && <p role="status" className="hint">Starting DuckDB in your browser…</p>}
      {state === "ready" && (
        <>
          <div className="field">
            <label htmlFor="scratch-sql">Query</label>
            <textarea id="scratch-sql" className="mono" rows={4} value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false} />
          </div>
          <button type="button" className="button quiet" disabled={!sql.trim()} onClick={() => void run()}>Run in the browser</button>
        </>
      )}
      {error && <p className="alert" role="alert"><strong>Error</strong>{error}</p>}
      {result && (
        <div className="table-wrap">
          <table>
            <caption>{result.rows.length} row{result.rows.length === 1 ? "" : "s"}</caption>
            <thead><tr>{result.columns.map((c, i) => <th key={i} scope="col">{c}</th>)}</tr></thead>
            <tbody>{result.rows.slice(0, 200).map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{fmt(v)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}
