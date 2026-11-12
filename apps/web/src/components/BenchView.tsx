"use client";

import * as Plot from "@observablehq/plot";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { type BenchOverview, type BenchResult, comparisons, headline } from "@/lib/bench";
import { fmt } from "@/lib/format";
import { errorText, postJSON } from "@/lib/post";
import { useTokens, useWidth } from "./ResultChart";

export function BenchView() {
  const router = useRouter();
  const [data, setData] = useState<BenchOverview | null>(null);
  const [benchmark, setBenchmark] = useState("tpch");
  const [scale, setScale] = useState(0.01);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      fetch("/api/bench").then(async (res) => {
        if (res.status === 401) return router.replace("/login");
        if (!res.ok) return setError(await errorText(res, "Could not load the benchmarks."));
        setData((await res.json()) as BenchOverview);
      }),
    [router],
  );

  useEffect(() => {
    load().catch(() => setError("Could not reach the server."));
  }, [load]);

  const running = data?.job.running ?? false;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => load().catch(() => setError("Lost the connection to the server.")), 1000);
    return () => clearInterval(timer);
  }, [running, load]);

  async function start() {
    setError(null);
    const res = await postJSON("/api/bench", { benchmark, scale });
    if (res.status === 401) return router.replace("/login");
    if (!res.ok) return setError(await errorText(res, "Could not start the benchmark."));
    await load();
  }

  const spec = data?.benchmarks.find((b) => b.id === benchmark);
  const result = data?.results.find((r) => r.benchmark === benchmark && r.scale === scale);
  return (
    <main id="main" className="er-page">
      <header className="data-head">
        <h1>DuckDB against PostgreSQL</h1>
        <p className="lede">
          The same TPC-H or TPC-DS data and the same standard queries on both engines: DuckDB, a
          columnar engine built for analytics, and PostgreSQL, a row store built for transactions.
          Every query is timed on both, and their answers are checked against each other.
        </p>
      </header>

      <section className="er-input" aria-label="Run a benchmark">
        <div className="er-bar">
          <div className="field">
            <label htmlFor="bench-name">Benchmark</label>
            <select id="bench-name" className="nf-select" value={benchmark} onChange={(e) => setBenchmark(e.currentTarget.value)}>
              {data?.benchmarks.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="bench-scale">Scale factor</label>
            <select id="bench-scale" className="nf-select" value={scale} onChange={(e) => setScale(Number(e.currentTarget.value))}>
              {(spec?.scales ?? [0.01]).map((s) => <option key={s} value={s}>{s} (about {fmt(s * 1000)} MB)</option>)}
            </select>
          </div>
        </div>
        <p className="hint">
          Scale 0.01 takes about a minute and anyone can run it. Larger scales take minutes to hours,
          so only operator accounts can start them. One run at a time on this server.
        </p>
        <button type="button" className="button" disabled={running || !data} onClick={() => void start()}>
          {running ? "Running…" : result ? "Run again" : "Run"}
        </button>
        {running && (
          <p role="status" className="hint">
            {data?.job.benchmark === "tpch" ? "TPC-H" : "TPC-DS"} at scale {data?.job.scale}: {data?.job.message}
          </p>
        )}
      </section>

      {error && <p className="alert" role="alert"><strong>Error</strong>{error}</p>}
      {!running && data?.job.error && <p className="alert" role="alert"><strong>The last run failed</strong>{data.job.error}</p>}

      {result ? <Result result={result} /> : data && !running && <p className="hint">No results for {spec?.name} at scale {scale} yet. Run it to see them.</p>}

      <section aria-labelledby="bench-about">
        <h2 id="bench-about">About these numbers</h2>
        <ul className="notes">
          <li>Each query runs {result?.reps ?? 3} times on each engine; the page shows the median. Both engines run on this server, so its load affects them.</li>
          <li>The data comes from DuckDB&apos;s dbgen and dsdgen, then is copied into PostgreSQL and analyzed. Neither engine gets keys or indexes, so PostgreSQL scans where an index would help; the Plans page shows what one changes.</li>
          <li>Queries are the standard TPC text, translated to PostgreSQL by sqlglot. A query that times out after 30 seconds counts as failed.</li>
          <li>Derived from the TPC-H and TPC-DS kits; not comparable to published TPC results.</li>
        </ul>
      </section>
    </main>
  );
}

function Result({ result }: { result: BenchResult }) {
  const s = result.summary;
  return (
    <section aria-labelledby="bench-result">
      <h2 id="bench-result">{result.name} at scale {result.scale}</h2>
      <p>{headline(result)}</p>
      <p className="hint">
        {s.agreed} of {s.queries} queries returned the same rows on both engines
        {s.failed ? `; ${s.failed} failed on at least one` : ""}. DuckDB {result.versions.duckdb}, PostgreSQL{" "}
        {Math.floor(result.versions.postgres / 10000)}. Ran {new Date(result.ran_at).toLocaleString()}.
      </p>
      <RatioChart result={result} />
      <div className="table-wrap">
        <table>
          <caption>Median time per query</caption>
          <thead>
            <tr>
              <th scope="col">Query</th>
              <th scope="col" className="num">DuckDB (ms)</th>
              <th scope="col" className="num">PostgreSQL (ms)</th>
              <th scope="col" className="num">Rows</th>
              <th scope="col">Same answer</th>
            </tr>
          </thead>
          <tbody>
            {result.queries.map((q) => (
              <tr key={q.nr}>
                <th scope="row">Q{q.nr}</th>
                <td className="num">{fmt(q.duckdb_ms)}</td>
                <td className="num">{fmt(q.postgres_ms)}</td>
                <td className="num">{fmt(q.rows)}</td>
                <td>{q.error ?? (q.agree ? "Yes" : "No")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** One dot per query on a log scale centered on 1×, so twice as fast either way is the same distance. */
function RatioChart({ result }: { result: BenchResult }) {
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref);
  const c = useTokens();
  const rows = comparisons(result);

  useEffect(() => {
    const el = ref.current;
    if (!el || !width || !rows.length) return;
    const span = Math.max(...rows.map((r) => Math.max(r.ratio, 1 / r.ratio)), 2) * 1.2;
    const chart = Plot.plot({
      width,
      height: rows.length * 14 + 60,
      marginLeft: 44,
      style: { fontFamily: "var(--sans)", fontSize: "12px", color: c.ink, background: "transparent" },
      x: {
        type: "log",
        domain: [1 / span, span],
        ticks: [-4, -3, -2, -1, 0, 1, 2, 3, 4].map((e) => 10 ** e).filter((t) => t >= 1 / span && t <= span),
        grid: true,
        label: "← PostgreSQL faster · DuckDB faster →",
        labelAnchor: "center",
        tickFormat: (v: number) => (v >= 1 ? `${fmt(v)}×` : `${fmt(1 / v)}×`),
      },
      y: { label: null, domain: rows.map((r) => `Q${r.nr}`), tickSize: 0 },
      marks: [
        Plot.ruleX([1], { stroke: c.ink }),
        Plot.ruleY(rows, { y: (d) => `Q${d.nr}`, x1: 1, x2: "ratio", stroke: c.rule }),
        Plot.dot(rows, {
          x: "ratio",
          y: (d) => `Q${d.nr}`,
          r: 3.5,
          fill: (d) => (d.faster === "DuckDB" ? c.mark : c.muted),
          title: (d) => `Q${d.nr}: ${d.faster} ${fmt(Math.max(d.ratio, 1 / d.ratio))}× faster`,
        }),
      ],
    });
    el.replaceChildren(chart);
    return () => chart.remove();
  }, [rows, width, c]);

  if (!rows.length) return null;
  const best = rows.reduce((a, b) => (b.ratio > a.ratio ? b : a));
  return (
    <figure className="figure">
      <div
        ref={ref}
        role="img"
        aria-label={`Speed ratio per query. DuckDB was faster on ${rows.filter((r) => r.faster === "DuckDB").length} of ${rows.length}; the largest gap is Q${best.nr}, ${fmt(best.ratio)}× in DuckDB's favor.`}
      />
      <figcaption>How many times faster the faster engine was, per query. The table below has the times.</figcaption>
    </figure>
  );
}
