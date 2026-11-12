"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { EXAMPLE_SETUP, type Explained, PLAN_EXAMPLES, planError, speedup } from "@/lib/plans";
import { errorText, postJSON } from "@/lib/post";
import { IndexAnimations } from "./IndexAnimations";

export function PlansView() {
  const router = useRouter();
  const [sql, setSql] = useState(PLAN_EXAMPLES[0].sql);
  const [result, setResult] = useState<Explained | null>(null);
  const [before, setBefore] = useState<{ ms: number; sql: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function guarded<T>(label: string, work: () => Promise<T>): Promise<T | null> {
    setBusy(label);
    setError(null);
    try {
      return await work();
    } catch (e) {
      setError((e as Error).message || "Lost the connection to the server.");
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function call(path: string, body: unknown): Promise<Response> {
    const res = await postJSON(path, body);
    if (res.status === 401) router.replace("/login");
    if (!res.ok) throw new Error(planError(await errorText(res, "That did not work.")));
    return res;
  }

  async function loadExample() {
    await guarded("Creating 200,000 orders…", async () => {
      await call("/api/lab/start", {});
      for (const statement of EXAMPLE_SETUP) {
        const res = await call("/api/lab/run", { session: "A", sql: statement });
        const out = (await res.json()) as { state: string; error?: string };
        if (out.state === "error") throw new Error(out.error ?? "Setting up the example failed.");
      }
      setResult(null);
      setBefore(null);
    });
  }

  async function explain(query = sql) {
    const res = await guarded("Running EXPLAIN ANALYZE…", () => call("/api/lab/explain", { sql: query }));
    if (res) setResult((await res.json()) as Explained);
  }

  async function apply(index: string) {
    if (!result) return;
    setBefore({ ms: result.summary.execution_ms, sql: index });
    const ok = await guarded("Creating the index…", async () => {
      const res = await call("/api/lab/run", { session: "A", sql: index });
      const out = (await res.json()) as { state: string; error?: string };
      if (out.state === "error") throw new Error(out.error ?? "Creating the index failed.");
      return true;
    });
    if (ok) await explain();
  }

  const s = result?.summary;
  const maxSelf = s ? Math.max(...s.nodes.map((n) => n.self_ms), 0.001) : 1;
  return (
    <main id="main" className="er-page">
      <header className="data-head">
        <h1>Query plans and indexes</h1>
        <p className="lede">
          Run EXPLAIN ANALYZE on your lab tables in PostgreSQL, see where the time goes, and try the
          indexes it suggests. Below, watch a B+ tree and a hash index grow.
        </p>
      </header>

      <section className="er-input" aria-label="Query">
        <div className="er-bar">
          <button type="button" className="button quiet" disabled={busy !== null} onClick={() => void loadExample()}>
            Load example data (200,000 orders)
          </button>
          <span className="hint">This resets your lab tables.</span>
        </div>
        <div className="field">
          <label htmlFor="plan-example">Example query</label>
          <select id="plan-example" className="nf-select" defaultValue="" onChange={(e) => { const ex = PLAN_EXAMPLES.find((x) => x.label === e.currentTarget.value); if (ex) setSql(ex.sql); }}>
            <option value="" disabled>Pick a query on the example data</option>
            {PLAN_EXAMPLES.map((x) => <option key={x.label}>{x.label}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="plan-sql">Query</label>
          <textarea id="plan-sql" className="mono" rows={4} value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false} aria-describedby="plan-hint" />
          <p id="plan-hint" className="hint">A SELECT on your lab tables. EXPLAIN ANALYZE really runs it, inside a transaction that is always rolled back.</p>
        </div>
        <button type="button" className="button" disabled={busy !== null || !sql.trim()} onClick={() => void explain()}>
          {busy ?? "Explain"}
        </button>
      </section>

      {error && <p className="alert" role="alert"><strong>Error</strong>{error}</p>}

      {s && result && (
        <>
          <section aria-labelledby="plan">
            <h2 id="plan">Plan</h2>
            <p>
              Ran in <strong>{s.execution_ms.toFixed(2)} ms</strong> (planned in {s.planning_ms.toFixed(2)} ms).
              {before && <> Before <span className="mono">{before.sql}</span>: {before.ms.toFixed(2)} ms, so {speedup(before.ms, s.execution_ms)}.</>}
            </p>
            <p className="hint">
              Rows and times are totals over all loops and parallel workers, so a node&apos;s time
              can add up to more than the run time.
            </p>
            <ol className="plan-tree" aria-label="Plan nodes, outermost first">
              {s.nodes.map((n) => (
                <li key={n.id} style={{ paddingLeft: `${n.depth * 1.25}rem` }} className={n.id === s.hotspot ? "plan-hot" : undefined}>
                  <div className="plan-row">
                    <span className="mono plan-label">{n.label}</span>
                    {n.id === s.hotspot && <span className="plan-badge">most time</span>}
                    {n.misestimate >= 10 && <span className="plan-badge plan-warn">estimate {n.misestimate}× off</span>}
                  </div>
                  <div className="plan-bar" aria-hidden="true"><i style={{ width: `${(100 * n.self_ms) / maxSelf}%` }} /></div>
                  <div className="hint">
                    {n.self_ms.toFixed(2)} ms of its own · {n.rows.toLocaleString("en-US")} rows (estimated {n.est_rows.toLocaleString("en-US")})
                    {n.loops > 1 && ` · ${n.loops} loops`}
                    {n.removed > 0 && ` · ${n.removed.toLocaleString("en-US")} removed by the filter`}
                    {n.read_blocks + n.hit_blocks > 0 && ` · ${(n.read_blocks + n.hit_blocks).toLocaleString("en-US")} pages`}
                  </div>
                  {(n.filter || n.index_cond || n.join_filter || n.hash_cond) && (
                    <div className="hint mono plan-cond">{n.index_cond ? `Index Cond: ${n.index_cond}` : n.filter ? `Filter: ${n.filter}` : n.join_filter ? `Join Filter: ${n.join_filter}` : `Hash Cond: ${n.hash_cond}`}</div>
                  )}
                </li>
              ))}
            </ol>
            {s.warnings.length > 0 && <ul className="notes">{s.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
          </section>
          <section aria-labelledby="advice">
            <h2 id="advice">Index advice</h2>
            {result.advice.length === 0 ? (
              <p className="hint">Nothing to suggest: the plan does not read much more than it returns.</p>
            ) : (
              result.advice.map((a) => (
                <div key={a.sql} className="advice">
                  <p>{a.reason}</p>
                  <pre className="sql"><code>{a.sql}</code></pre>
                  <button type="button" className="button quiet" disabled={busy !== null} onClick={() => void apply(a.sql)}>
                    Create this index and explain again
                  </button>
                </div>
              ))
            )}
          </section>
        </>
      )}

      <IndexAnimations />
    </main>
  );
}
