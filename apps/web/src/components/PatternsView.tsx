"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { fmt } from "@/lib/format";
import { type Pattern, type PatternResult, cell } from "@/lib/patterns";
import { errorText, postJSON } from "@/lib/post";

export function PatternsView() {
  const router = useRouter();
  const [catalog, setCatalog] = useState<Pattern[] | null>(null);
  const [results, setResults] = useState<Record<string, Record<string, PatternResult>>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      fetch("/api/lab/patterns").then(async (res) => {
        if (res.status === 401) return router.replace("/login");
        if (!res.ok) return setError(await errorText(res, "Could not load the experiments."));
        setCatalog((await res.json()) as Pattern[]);
      }),
    [router],
  );

  useEffect(() => {
    load().catch(() => setError("Could not reach the server."));
  }, [load]);

  async function runAll(p: Pattern) {
    setError(null);
    try {
      const started = await postJSON("/api/lab/start", {});
      if (started.status === 401) return router.replace("/login");
      if (!started.ok) throw new Error(await errorText(started, "Could not start your lab."));
      for (const m of p.modes) {
        setBusy(`${p.title}: ${m.label}…`);
        const res = await postJSON("/api/lab/pattern", { id: p.id, mode: m.id });
        if (!res.ok) throw new Error(await errorText(res, "The experiment did not run."));
        const result = (await res.json()) as PatternResult;
        setResults((r) => ({ ...r, [p.id]: { ...r[p.id], [m.id]: result } }));
      }
    } catch (e) {
      setError((e as Error).message || "Lost the connection to the server.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main id="main" className="er-page">
      <header className="data-head">
        <h1>Transaction patterns</h1>
        <p className="lede">
          Each experiment runs real concurrent PostgreSQL connections in your lab: first the
          obvious way, then the pattern that makes it safe. The checks say what an outsider
          would notice: missing money, double charges, lost edits, missing events.
        </p>
      </header>
      {busy && <p role="status" className="hint">Running {busy}</p>}
      {error && <p className="alert" role="alert"><strong>Error</strong>{error}</p>}
      {catalog?.map((p) => {
        const done = results[p.id] ?? {};
        const labels = p.modes.flatMap((m) => done[m.id]?.checks.map((c) => c.label) ?? []);
        const checks = [...new Set(labels)];
        return (
          <section key={p.id} aria-labelledby={`pattern-${p.id}`}>
            <h2 id={`pattern-${p.id}`}>{p.title}</h2>
            <p>{p.question}</p>
            <dl className="notes">
              {p.modes.map((m) => (
                <div key={m.id}>
                  <dt><strong>{m.label}</strong></dt>
                  <dd>{m.how}</dd>
                </div>
              ))}
            </dl>
            <button type="button" className="button" disabled={busy !== null} onClick={() => void runAll(p)}>
              {Object.keys(done).length ? "Run again" : "Run all ways"}
            </button>
            {checks.length > 0 && (
              <div className="table-wrap">
                <table>
                  <caption>{p.title}: each way, side by side</caption>
                  <thead>
                    <tr>
                      <th scope="col">Check</th>
                      {p.modes.map((m) => <th key={m.id} scope="col">{m.label}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {checks.map((label) => (
                      <tr key={label}>
                        <th scope="row">{label}</th>
                        {p.modes.map((m) => {
                          const c = done[m.id]?.checks.find((x) => x.label === label);
                          if (!c) return <td key={m.id}>—</td>;
                          const v = cell(c);
                          return <td key={m.id} className={v.ok ? undefined : "tone-low"}>{v.ok ? "Pass: " : "Fail: "}{v.text}</td>;
                        })}
                      </tr>
                    ))}
                    <tr>
                      <th scope="row">What happened</th>
                      {p.modes.map((m) => (
                        <td key={m.id}>
                          {done[m.id] ? Object.entries(done[m.id].stats).map(([k, v]) => `${fmt(v)} ${k.replace("_", " ")}`).join(", ") : "—"}
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <th scope="row">Time</th>
                      {p.modes.map((m) => <td key={m.id}>{done[m.id] ? `${fmt(done[m.id].ms)} ms` : "—"}</td>)}
                    </tr>
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}
      <section aria-labelledby="patterns-about">
        <h2 id="patterns-about">About these runs</h2>
        <ul className="notes">
          <li>Three worker connections run at once, as your lab role, with its 30-second statement limit. Each experiment builds its own demo_ tables in your lab schema first.</li>
          <li>The naive ways pause a few milliseconds between reading and writing, as an application computing a value would; that is what lets the races show up every time.</li>
          <li>Results vary a little from run to run; the patterns&apos; checks pass every time.</li>
        </ul>
      </section>
    </main>
  );
}
