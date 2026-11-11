"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { ALGEBRA_EXAMPLES, type AlgebraResult, SYMBOLS } from "@/lib/algebra";
import { errorText, postJSON } from "@/lib/post";
import { QueryTree } from "./QueryTree";
import { ResultTable } from "./ResultTable";

type Mode = "ra" | "sql";

export function AlgebraView() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("ra");
  const [text, setText] = useState<string>(ALGEBRA_EXAMPLES[0].text);
  const [dataset, setDataset] = useState<"sample" | "mine">("sample");
  const [result, setResult] = useState<AlgebraResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);

  function insert(symbol: string) {
    const el = box.current;
    if (!el) return;
    const { selectionStart: a, selectionEnd: b } = el;
    const next = text.slice(0, a) + symbol + text.slice(b);
    setText(next);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(a + symbol.length, a + symbol.length);
    });
  }

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const body = mode === "ra" ? { expression: text, dataset } : { sql: text, dataset };
      const res = await postJSON("/api/algebra", body);
      if (res.status === 401) return router.replace("/login");
      if (!res.ok) {
        setResult(null);
        return setError(await errorText(res, "Could not translate that."));
      }
      setResult((await res.json()) as AlgebraResult);
    } catch {
      setError("Lost the connection to the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="main" className="er-page">
      <header className="data-head">
        <h1>Relational algebra</h1>
        <p className="lede">
          Write an expression in relational algebra or a SQL query. You get the other form, the
          query tree, and the result on the sample data or your own tables.
        </p>
      </header>

      <section className="er-input" aria-label="Query">
        <fieldset className="source">
          <legend>Write</legend>
          {(["ra", "sql"] as const).map((m) => (
            <label key={m}>
              <input type="radio" name="mode" checked={mode === m} onChange={() => setMode(m)} />
              {m === "ra" ? "Relational algebra" : "SQL"}
            </label>
          ))}
        </fieldset>
        <div className="field">
          <label htmlFor="examples">Example</label>
          <select
            id="examples"
            className="nf-select"
            defaultValue=""
            onChange={(e) => {
              const ex = ALGEBRA_EXAMPLES.find((x) => x.label === e.currentTarget.value);
              if (ex) {
                setMode(ex.mode);
                setText(ex.text);
                setResult(null);
              }
            }}
          >
            <option value="" disabled>
              Pick an example on the sample data
            </option>
            {ALGEBRA_EXAMPLES.map((x) => (
              <option key={x.label}>{x.label}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="query">{mode === "ra" ? "Expression" : "Query"}</label>
          {mode === "ra" && (
            <div className="palette" role="group" aria-label="Insert a symbol">
              {SYMBOLS.map((s) => (
                <button key={s} type="button" onClick={() => insert(s)} aria-label={`Insert ${s}`}>
                  {s}
                </button>
              ))}
            </div>
          )}
          <textarea id="query" ref={box} className="mono" rows={5} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-describedby="query-hint" />
          <p id="query-hint" className="hint">
            {mode === "ra" ? (
              <>
                <code>σ[condition](R)</code>, <code>π[a, b](R)</code>, <code>ρ[s](R)</code>,{" "}
                <code>γ[group; count(*) AS n](R)</code>, <code>R ⋈ S</code>,{" "}
                <code>R ⋈[R.a = S.b] S</code>, <code>×</code>, <code>∪</code>, <code>−</code>,{" "}
                <code>∩</code>. Words work too: select, project, join, union, minus.
              </>
            ) : (
              "One SELECT, joins, GROUP BY, set operations and subqueries in FROM translate."
            )}
          </p>
        </div>
        <div className="er-bar">
          <fieldset className="source">
            <legend>Run on</legend>
            {(["sample", "mine"] as const).map((d) => (
              <label key={d}>
                <input type="radio" name="dataset" checked={dataset === d} onChange={() => setDataset(d)} />
                {d === "sample" ? "Sample data" : "Your tables"}
              </label>
            ))}
          </fieldset>
          <button className="button" type="button" disabled={busy || !text.trim()} onClick={() => void run()}>
            {busy ? "Running…" : "Translate and run"}
          </button>
        </div>
      </section>

      {error && (
        <p className="alert" role="alert">
          <strong>Error</strong>
          {error}
        </p>
      )}

      {result && (
        <>
          <section aria-labelledby="forms">
            <h2 id="forms">{mode === "ra" ? "As SQL" : "As relational algebra"}</h2>
            {mode === "sql" && <p className="ra-line mono">{result.ra}</p>}
            {mode === "ra" && (
              <pre className="sql">
                <code>{result.sql}</code>
              </pre>
            )}
            {result.notes.length > 0 && (
              <ul className="notes">
                {result.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            )}
          </section>
          <section aria-labelledby="tree">
            <h2 id="tree">Query tree</h2>
            <QueryTree tree={result.tree} />
          </section>
          <section aria-labelledby="rows">
            <h2 id="rows">Result</h2>
            {result.run_error && (
              <p className="alert" role="alert">
                <strong>Did not run</strong>
                {result.run_error}
              </p>
            )}
            {result.result && <ResultTable result={result.result} />}
          </section>
        </>
      )}
    </main>
  );
}
