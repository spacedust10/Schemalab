"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { type AssignmentView, type Grade, apiPath, scoreLine } from "@/lib/assignments";
import { fmt } from "@/lib/format";
import { errorText, postJSON } from "@/lib/post";

export function TakeView({ id }: { id: string }) {
  const router = useRouter();
  const [a, setA] = useState<AssignmentView | null>(null);
  const [sql, setSql] = useState("");
  const [grade, setGrade] = useState<Grade | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const path = apiPath(id); // null for an id that is not one of ours: nothing is requested
  const load = useCallback(() => {
    if (!path) return Promise.resolve();
    return fetch(path).then(async (res) => {
        if (res.status === 401) return router.replace("/login");
        if (!res.ok) return setError(await errorText(res, "Could not load the assignment."));
        setA((await res.json()) as AssignmentView);
      });
  }, [path, router]);

  useEffect(() => {
    load().catch(() => setError("Could not reach the server."));
  }, [load]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const path = apiPath(id, "submit");
      if (!path) throw new Error("No such assignment.");
      const res = await postJSON(path, { sql });
      if (res.status === 401) return router.replace("/login");
      if (!res.ok) throw new Error(await errorText(res, "The answer was not graded."));
      const g = (await res.json()) as Grade;
      setGrade(g);
      setA((prev) => (prev ? { ...prev, attempts_left: g.attempts_left, best: g.best } : prev));
    } catch (e) {
      setError((e as Error).message || "Lost the connection to the server.");
    } finally {
      setBusy(false);
    }
  }

  if (!path) return <main id="main" className="er-page"><p className="alert" role="alert"><strong>Error</strong>No such assignment.</p></main>;
  if (!a) return <main id="main" className="er-page">{error ? <p className="alert" role="alert"><strong>Error</strong>{error}</p> : <p className="hint">Loading…</p>}</main>;
  const out = a.attempts_left === 0;
  return (
    <main id="main" className="er-page">
      <header className="data-head">
        <h1>{a.title}</h1>
        <p className="lede">{a.prompt}</p>
        <p className="hint">
          {a.mode === "exam"
            ? `Exam: ${a.attempts_left} attempt${a.attempts_left === 1 ? "" : "s"} left; failed tests are named, hidden rows are never shown.`
            : "Tutor: as many attempts as you like, with a hint and example rows for each failed test."}
          {a.best !== null && ` Your best score so far: ${a.best}.`}
          {a.ordered && " Row order counts."}
        </p>
      </header>

      <section aria-labelledby="take-data">
        <h2 id="take-data">The data</h2>
        <pre className="sql">{a.schema}</pre>
        {Object.entries(a.tables).map(([name, t]) => (
          <div key={name} className="table-wrap">
            <table>
              <caption>{name}: sample rows</caption>
              <thead><tr>{t.columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
              <tbody>{t.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{fmt(v)}</td>)}</tr>)}</tbody>
            </table>
          </div>
        ))}
        <p className="hint">Graded on this sample and on {a.tests.length - 1} hidden database{a.tests.length === 2 ? "" : "s"}: {a.tests.slice(1).join("; ")}.</p>
      </section>

      <section aria-labelledby="take-answer">
        <h2 id="take-answer">Your answer</h2>
        <div className="field">
          <label htmlFor="take-sql">SQL</label>
          <textarea id="take-sql" className="mono" rows={6} value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false} />
        </div>
        <button type="button" className="button" disabled={busy || out || !sql.trim()} onClick={() => void submit()}>
          {busy ? "Grading…" : out ? "No attempts left" : "Submit"}
        </button>
      </section>

      {error && <p className="alert" role="alert"><strong>Error</strong>{error}</p>}

      {grade && (
        <section aria-labelledby="take-result" aria-live="polite">
          <h2 id="take-result">Score: {grade.score}</h2>
          <p>{scoreLine(grade)}</p>
          {grade.error && (
            <div className="alert">
              <strong>{grade.error.title}</strong>
              <p>{grade.error.explanation}</p>
              <p><strong>Try:</strong> {grade.error.fix}</p>
            </div>
          )}
          <ul className="notes">
            {grade.tests.map((t) => (
              <li key={t.name}>
                <strong>{t.passed ? "Passed" : "Failed"}:</strong> {t.name}
                {t.hint && <> — {t.hint}</>}
                {t.missing && t.missing.length > 0 && <div className="hint">Missing from yours: {t.missing.map((r) => `(${r.map(fmt).join(", ")})`).join(" ")}</div>}
                {t.extra && t.extra.length > 0 && <div className="hint">Not in the answer: {t.extra.map((r) => `(${r.map(fmt).join(", ")})`).join(" ")}</div>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
