"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  type Analysis,
  type Decomposition,
  EXAMPLES,
  type Form,
  type NFRelation,
  fromDataset,
  showAttrs,
  showFD,
} from "@/lib/normalize";
import { errorText, postJSON } from "@/lib/post";
import type { Dataset } from "@/lib/upload";

const LADDER: Form[] = ["1NF", "2NF", "3NF", "BCNF"];

export function NormalizeView() {
  const router = useRouter();
  const [schema, setSchema] = useState(EXAMPLES[0].schema);
  const [deps, setDeps] = useState(EXAMPLES[0].dependencies);
  const [closureOf, setClosureOf] = useState("");
  const [tables, setTables] = useState<Dataset[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/datasets")
      .then((res) => (res.ok ? (res.json() as Promise<Dataset[]>) : []))
      .then(setTables)
      .catch(() => setTables([]));
  }, []);

  async function analyze() {
    setBusy(true);
    setError(null);
    try {
      const res = await postJSON("/api/normalize", { schema, dependencies: deps, closure_of: closureOf });
      if (res.status === 401) return router.replace("/login");
      if (!res.ok) {
        setAnalysis(null);
        return setError(await errorText(res, "Could not analyze that relation."));
      }
      setAnalysis((await res.json()) as Analysis);
    } catch {
      setError("Lost the connection to the server.");
    } finally {
      setBusy(false);
    }
  }

  function start(value: string) {
    setError(null);
    setAnalysis(null);
    const example = EXAMPLES.find((e) => `example:${e.label}` === value);
    const table = tables.find((t) => `table:${t.name}` === value);
    try {
      const picked = example ?? (table ? fromDataset(table) : null);
      if (!picked) return;
      setSchema(picked.schema);
      setDeps(picked.dependencies);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const name = schema.split("(")[0].trim() || "R";
  return (
    <main id="main" className="normalize">
      <header className="data-head">
        <h1>Normalize a relation</h1>
        <p className="lede">
          Give a relation and its functional dependencies. You get its keys, the highest normal
          form it reaches and why it stops there, and two decompositions with proofs.
        </p>
      </header>

      <form
        className="nf-form"
        onSubmit={(e) => {
          e.preventDefault();
          void analyze();
        }}
      >
        <div className="field">
          <label htmlFor="start">Start from</label>
          <select id="start" defaultValue="" onChange={(e) => start(e.currentTarget.value)}>
            <option value="" disabled>
              An example or one of your tables
            </option>
            <optgroup label="Textbook examples">
              {EXAMPLES.map((e) => (
                <option key={e.label} value={`example:${e.label}`}>
                  {e.label}
                </option>
              ))}
            </optgroup>
            {tables.length > 0 && (
              <optgroup label="Your tables">
                {tables.map((t) => (
                  <option key={t.name} value={`table:${t.name}`}>
                    {t.name} ({t.columns.length} columns)
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </div>
        <div className="field">
          <label htmlFor="schema">Relation</label>
          <input id="schema" className="mono" value={schema} onChange={(e) => setSchema(e.target.value)} spellCheck={false} />
        </div>
        <div className="field">
          <label htmlFor="deps">Functional dependencies</label>
          <textarea
            id="deps"
            className="mono"
            rows={6}
            value={deps}
            onChange={(e) => setDeps(e.target.value)}
            spellCheck={false}
            aria-describedby="deps-hint"
          />
          <p id="deps-hint" className="hint">
            One per line, like <code>sid, course -&gt; grade</code>. One-letter names can run
            together: <code>AB -&gt; C</code>. Lines starting with # are ignored.
          </p>
        </div>
        <div className="field">
          <label htmlFor="closure">Closure of (optional)</label>
          <input id="closure" className="mono" value={closureOf} onChange={(e) => setClosureOf(e.target.value)} placeholder="e.g. A, G" spellCheck={false} />
        </div>
        <button className="button" type="submit" disabled={busy}>
          {busy ? "Analyzing…" : "Analyze"}
        </button>
      </form>

      {error && (
        <p className="alert" role="alert">
          <strong>Error</strong>
          {error}
        </p>
      )}
      {analysis && <Result name={name} a={analysis} />}
    </main>
  );
}

function Result({ name, a }: { name: string; a: Analysis }) {
  const reached = LADDER.indexOf(a.form);
  return (
    <div className="nf-result" aria-live="polite">
      <section aria-labelledby="verdict">
        <h2 id="verdict" className="takeaway">
          {name} is in {a.form}
          {a.form === "BCNF" ? "." : `, not ${LADDER[reached + 1]}.`}
        </h2>
        <ol className="ladder" aria-label="Normal forms">
          {LADDER.map((f, i) => (
            <li key={f} className={i <= reached ? "met" : "unmet"}>
              {f} <span>{i <= reached ? "met" : "not met"}</span>
            </li>
          ))}
        </ol>
        {a.violations.length > 0 && (
          <ul className="violations">
            {a.violations.map((v) => (
              <li key={showFD(v)}>
                <code>{showFD(v)}</code> breaks {v.form}. {v.reason.replace(/^[^:]*: /, "")}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="keys">
        <h2 id="keys">Keys</h2>
        <dl className="provenance">
          <dt>Candidate keys</dt>
          <dd className="mono">{a.keys.map((k) => showAttrs(k)).join("   ")}</dd>
          <dt>Prime attributes</dt>
          <dd className="mono">{showAttrs(a.prime) || "none"}</dd>
          {a.closure && (
            <>
              <dt>Closure</dt>
              <dd className="mono">
                ({showAttrs(a.closure.of)})⁺ = {showAttrs(a.closure.is)}{" "}
                <span className="hint">{a.closure.superkey ? "a superkey" : "not a superkey"}</span>
              </dd>
            </>
          )}
          <dt>Canonical cover</dt>
          <dd className="mono">{a.cover.map(showFD).join(";  ") || "no dependencies"}</dd>
        </dl>
      </section>

      <div className="decompositions">
        <DecompositionCard
          title="Decompose into BCNF"
          d={a.bcnf}
          note="Splits on a dependency whose left side is not a superkey, until every relation is in BCNF. Always lossless; it can lose dependencies."
        />
        <DecompositionCard
          title="Synthesize 3NF"
          d={a.third}
          note="One relation per left side of the canonical cover, plus a key. Always lossless and dependency preserving."
        />
      </div>
    </div>
  );
}

function DecompositionCard({ title, d, note }: { title: string; d: Decomposition; note: string }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      <p className="hint">{note}</p>
      {d.steps.length > 0 && (
        <ol className="steps">
          {d.steps.map((s, i) => (
            <li key={i}>
              Split ({showAttrs(s.relation)}) on <code>{showFD(s)}</code> into ({showAttrs(s.split[0])}) and (
              {showAttrs(s.split[1])}).
            </li>
          ))}
        </ol>
      )}
      <ul className="relations">
        {d.relations.map((r, i) => (
          <li key={i}>
            <RelationName n={i + 1} r={r} /> <span className="hint">{r.form}</span>
            {r.fds.length > 0 && <div className="hint mono">{r.fds.map(showFD).join(";  ")}</div>}
          </li>
        ))}
      </ul>
      <dl className="provenance">
        <dt>Lossless join</dt>
        <dd>{d.lossless ? "Yes: the chase reaches a row of a-symbols." : "No: the chase never reaches a row of a-symbols."}</dd>
        <dt>Dependencies</dt>
        <dd>
          {d.lost.length === 0 ? "All preserved." : <>Lost: <code>{d.lost.map(showFD).join(";  ")}</code></>}
        </dd>
      </dl>
      <details>
        <summary>Chase tableau</summary>
        <div className="table-wrap">
          <table>
            <tbody>
              {d.chase_table.map((row, i) => (
                <tr key={i}>
                  <th scope="row">R{i + 1}</th>
                  {row.map((v, j) => (
                    <td key={j} className={v.startsWith("a") ? "mono strong" : "mono"}>
                      {v}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}

/** R1(A, B, C) with the first candidate key underlined, as textbooks write it. */
function RelationName({ n, r }: { n: number; r: NFRelation }) {
  const key = new Set(r.keys[0] ?? []);
  const sep = r.attrs.every((a) => a.length === 1) ? ", " : ", ";
  return (
    <span className="mono">
      R{n}(
      {r.attrs.map((a, i) => (
        <span key={a}>
          {i > 0 && sep}
          {key.has(a) ? <u>{a}</u> : a}
        </span>
      ))}
      )
    </span>
  );
}
