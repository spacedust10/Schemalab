"use client";

import * as Plot from "@observablehq/plot";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { validId } from "@/lib/assignments";
import { type Answer, type Dashboard, histogram } from "@/lib/dashboard";
import { fmt } from "@/lib/format";
import { errorText } from "@/lib/post";
import { useTokens, useWidth } from "./ResultChart";

const BANDS = ["0–9", "10–19", "20–29", "30–39", "40–49", "50–59", "60–69", "70–79", "80–89", "90–100"];

export function AssignmentDashboard({ id }: { id: string }) {
  const router = useRouter();
  const ok = validId(id);
  const base = `/api/assignments/${encodeURIComponent(id)}`;
  const [data, setData] = useState<Dashboard | null>(null);
  const [answers, setAnswers] = useState<{ email: string; list: Answer[] } | null>(null);
  const [matched, setMatched] = useState<{ csv: string; unmatched: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!ok) return Promise.resolve();
    return fetch(`${base}/dashboard`).then(async (res) => {
      if (res.status === 401) return router.replace("/login");
      if (!res.ok) return setError(await errorText(res, "Only the assignment's creator can see its dashboard."));
      setData((await res.json()) as Dashboard);
    });
  }, [ok, base, router]);

  useEffect(() => {
    load().catch(() => setError("Could not reach the server."));
  }, [load]);

  async function showAnswers(email: string) {
    const res = await fetch(`${base}/submissions?${new URLSearchParams({ email })}`);
    if (res.ok) setAnswers({ email, list: (await res.json()) as Answer[] });
  }

  async function match(input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = "";
    if (!file) return;
    setError(null);
    const res = await fetch(`${base}/canvas`, { method: "POST", headers: { "content-type": "text/csv" }, body: await file.text() });
    if (!res.ok) return setError(await errorText(res, "The Canvas export could not be read."));
    setMatched((await res.json()) as { csv: string; unmatched: string[] });
  }

  function save(text: string) {
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "canvas-grades.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!ok || error) return <main id="main" className="er-page"><p className="alert" role="alert"><strong>Error</strong>{ok ? error : "No such assignment."}</p></main>;
  if (!data) return <main id="main" className="er-page"><p className="hint">Loading…</p></main>;
  const s = data.summary;
  return (
    <main id="main" className="er-page">
      <header className="data-head">
        <h1>{data.title}: dashboard</h1>
        <p className="lede">
          {s.students} student{s.students === 1 ? "" : "s"}, {s.submissions} submission{s.submissions === 1 ? "" : "s"}.
          {s.mean !== undefined && ` Best scores: mean ${fmt(s.mean)}, median ${fmt(s.median)}, from ${s.min} to ${s.max}.`}
        </p>
      </header>

      {s.students > 0 && <ScoreChart scores={data.students.map((x) => x.best)} />}

      <section aria-labelledby="dash-tests">
        <h2 id="dash-tests">Tests passed by students&apos; latest answers</h2>
        <ul className="notes">
          {data.tests.map((t) => (
            <li key={t.name}>
              <strong>{t.name}:</strong> {t.of ? `${t.passed} of ${t.of} (${Math.round((100 * t.passed) / t.of)}%)` : "no answers yet"}
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="dash-students">
        <h2 id="dash-students">Students</h2>
        <div className="table-wrap">
          <table>
            <caption>Best, latest and attempts per student. Choose a student to see their answers.</caption>
            <thead>
              <tr><th scope="col">Student</th><th scope="col" className="num">Best</th><th scope="col" className="num">Latest</th><th scope="col" className="num">Attempts</th><th scope="col">Last submitted</th></tr>
            </thead>
            <tbody>
              {data.students.map((x) => (
                <tr key={x.email}>
                  <th scope="row"><button type="button" className="button quiet" onClick={() => void showAnswers(x.email)}>{x.email}</button></th>
                  <td className="num">{x.best}</td>
                  <td className="num">{x.latest}</td>
                  <td className="num">{x.attempts}</td>
                  <td>{new Date(x.last_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {answers && (
          <div aria-live="polite">
            <h3>Answers from {answers.email}</h3>
            {answers.list.map((a, i) => (
              <div key={i}>
                <p className="hint">Attempt {i + 1}, {new Date(a.at).toLocaleString()}: score {a.score}</p>
                <pre className="sql">{a.sql}</pre>
              </div>
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="dash-canvas">
        <h2 id="dash-canvas">Send grades to Canvas</h2>
        <p>
          Download a gradebook file and import it in Canvas (Grades, Import). Each student&apos;s best score is out of 100.
          Canvas matches students by their Canvas login, not their email, so this file only matches where logins are emails.
        </p>
        <div className="er-bar">
          <a className="button" href={`${base}/canvas`} download="canvas-grades.csv">Download the Canvas CSV</a>
        </div>
        <div className="field">
          <label htmlFor="dash-match">Match students with your Canvas export (optional)</label>
          <input id="dash-match" type="file" accept=".csv,text/csv" aria-describedby="dash-match-hint" onChange={(e) => void match(e.currentTarget)} />
          <p id="dash-match-hint" className="hint">
            In Canvas, export the gradebook (Grades, Export) and choose that file here: every student then gets their
            Canvas ID. The file is read once and not kept.
          </p>
        </div>
        {matched && (
          <div role="status">
            <p>
              {matched.unmatched.length === 0
                ? "Every student was found in your Canvas export."
                : `Not found in your Canvas export (left with their email): ${matched.unmatched.join(", ")}.`}
            </p>
            <button type="button" className="button" onClick={() => save(matched.csv)}>Download the matched CSV</button>
          </div>
        )}
      </section>
    </main>
  );
}

function ScoreChart({ scores }: { scores: number[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref);
  const c = useTokens();
  const counts = useMemo(() => histogram(scores), [scores]);
  useEffect(() => {
    const el = ref.current;
    if (!el || !width) return;
    const data = counts.map((n, i) => ({ band: BANDS[i], n }));
    const chart = Plot.plot({
      width,
      height: 220,
      style: { fontFamily: "var(--sans)", fontSize: "12px", color: c.ink, background: "transparent" },
      x: { label: "Best score", domain: BANDS },
      // Whole students only: no more ticks than the tallest bar, so none fall between counts.
      y: { label: "Students", grid: true, ticks: Math.min(5, Math.max(1, ...counts)), tickFormat: "d" },
      marks: [Plot.barY(data, { x: "band", y: "n", fill: c.mark }), Plot.ruleY([0], { stroke: c.ink })],
    });
    el.replaceChildren(chart);
    return () => chart.remove();
  }, [counts, width, c]);
  const top = counts.indexOf(Math.max(...counts));
  return (
    <figure className="figure">
      <div ref={ref} role="img" aria-label={`Best scores in bands of ten. Most students scored ${BANDS[top]}.`} />
      <figcaption>Best score per student, in bands of ten.</figcaption>
    </figure>
  );
}
