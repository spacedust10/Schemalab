"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { errorText, postJSON } from "@/lib/post";
import { RECOVERY_EXAMPLE, type Recovery, SCHEDULE_EXAMPLES, type ScheduleAnalysis } from "@/lib/theory";
import { PrecedenceGraph } from "./PrecedenceGraph";

type Tab = "schedules" | "recovery";

export function TheoryView() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("schedules");
  const [schedule, setSchedule] = useState(SCHEDULE_EXAMPLES[0].text);
  const [log, setLog] = useState(RECOVERY_EXAMPLE);
  const [analysis, setAnalysis] = useState<ScheduleAnalysis | null>(null);
  const [recovery, setRecovery] = useState<Recovery | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const res = tab === "schedules" ? await postJSON("/api/theory/schedules", { schedule }) : await postJSON("/api/theory/recovery", { log });
      if (res.status === 401) return router.replace("/login");
      if (!res.ok) return setError(await errorText(res, "Could not read that."));
      if (tab === "schedules") setAnalysis((await res.json()) as ScheduleAnalysis);
      else setRecovery((await res.json()) as Recovery);
    } catch {
      setError("Lost the connection to the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="main" className="er-page">
      <header className="data-head">
        <h1>Schedules and recovery</h1>
        <p className="lede">
          Check a schedule for serializability, recoverability and two-phase locking, or replay
          a write-ahead log through ARIES crash recovery, pass by pass.
        </p>
      </header>

      <fieldset className="source">
        <legend>Tool</legend>
        {(["schedules", "recovery"] as const).map((t) => (
          <label key={t}>
            <input type="radio" name="tool" checked={tab === t} onChange={() => { setTab(t); setError(null); }} />
            {t === "schedules" ? "Schedule checker" : "ARIES recovery"}
          </label>
        ))}
      </fieldset>

      <section className="er-input" aria-label="Input">
        {tab === "schedules" ? (
          <>
            <div className="field">
              <label htmlFor="sched-example">Example</label>
              <select id="sched-example" className="nf-select" defaultValue="" onChange={(e) => { const ex = SCHEDULE_EXAMPLES.find((x) => x.label === e.currentTarget.value); if (ex) { setSchedule(ex.text); setAnalysis(null); } }}>
                <option value="" disabled>Pick a textbook case</option>
                {SCHEDULE_EXAMPLES.map((x) => <option key={x.label}>{x.label}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="schedule">Schedule</label>
              <textarea id="schedule" className="mono" rows={3} value={schedule} onChange={(e) => setSchedule(e.target.value)} spellCheck={false} aria-describedby="schedule-hint" />
              <p id="schedule-hint" className="hint"><code>r1(A)</code> read, <code>w2(B)</code> write, <code>c1</code> commit, <code>a2</code> abort, in the order they happen.</p>
            </div>
          </>
        ) : (
          <div className="field">
            <label htmlFor="log">Log</label>
            <textarea id="log" className="mono" rows={12} value={log} onChange={(e) => setLog(e.target.value)} spellCheck={false} aria-describedby="log-hint" />
            <p id="log-hint" className="hint"><code>T1 write A 10 20</code> (old, then new value), <code>T1 commit</code>, <code>T1 abort</code>, <code>checkpoint</code>, <code>flush A</code> (page A reaches disk), <code>crash</code>.</p>
          </div>
        )}
        <button type="button" className="button" disabled={busy} onClick={() => void submit()}>
          {busy ? "Working…" : tab === "schedules" ? "Check schedule" : "Crash and recover"}
        </button>
      </section>

      {error && <p className="alert" role="alert"><strong>Error</strong>{error}</p>}
      {tab === "schedules" && analysis && <ScheduleResult a={analysis} />}
      {tab === "recovery" && recovery && <RecoveryResult r={recovery} />}
    </main>
  );
}

const yes = (ok: boolean | null) => (ok === null ? "not checked" : ok ? "yes" : "no");
const order = (o: number[]) => o.map((t) => `T${t}`).join(" → ");

function ScheduleResult({ a }: { a: ScheduleAnalysis }) {
  return (
    <>
      <section aria-labelledby="serial">
        <h2 id="serial">Serializability</h2>
        <dl className="provenance">
          <dt>Conflict serializable</dt>
          <dd>{a.conflict_serializable ? `yes, like ${a.serial_orders.map(order).join(" or ")}` : `no: cycle ${a.cycle?.map((t) => `T${t}`).join(" → ")}`}</dd>
          <dt>View serializable</dt>
          <dd>{yes(a.view_serializable)}{a.view_serializable && !a.conflict_serializable && a.view_order ? `, like ${order(a.view_order)} (blind writes)` : ""}</dd>
          {a.aborted.length > 0 && (<><dt>Left out</dt><dd>aborted {a.aborted.map((t) => `T${t}`).join(", ")}</dd></>)}
        </dl>
        <PrecedenceGraph txns={a.transactions.filter((t) => !a.aborted.includes(t))} edges={a.edges} cycle={a.cycle} />
      </section>
      <section aria-labelledby="recover">
        <h2 id="recover">Recoverability</h2>
        <dl className="provenance">
          {(["recoverable", "cascadeless", "strict"] as const).map((k) => (
            <div key={k} className="contents">
              <dt>{k[0].toUpperCase() + k.slice(1)}</dt>
              <dd>{yes(a[k].ok)}{a[k].reason ? `: ${a[k].reason}` : ""}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section aria-labelledby="locks">
        <h2 id="locks">Two-phase locking</h2>
        <p className="hint">Could the lock manager have produced this schedule? sl and xl are shared and exclusive locks, u an unlock.</p>
        {Object.entries(a.locking).map(([name, v]) => (
          <div key={name} className="lock-row">
            <h3>{name}: {v.ok ? "yes" : `no, at step ${v.step}`}</h3>
            {v.reason && <p>{v.reason}</p>}
            <p className="lock-trace mono">
              {v.schedule.map((op, i) => (
                <span key={i} className={/^(sl|xl|u)\d/.test(op) ? "lock-op" : undefined}>{op} </span>
              ))}
            </p>
          </div>
        ))}
      </section>
    </>
  );
}

function RecoveryResult({ r }: { r: Recovery }) {
  const kept = new Set(r.analysis?.winners ?? []);
  return (
    <>
      <section aria-labelledby="the-log">
        <h2 id="the-log">The log</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col" className="num">LSN</th><th scope="col">Record</th><th scope="col">Txn</th><th scope="col">Page</th><th scope="col">Before → after</th><th scope="col" className="num">prevLSN</th><th scope="col" className="num">undoNext</th></tr></thead>
            <tbody>
              {r.log.map((x) => (
                <tr key={x.lsn}>
                  <td className="num mono">{x.lsn}</td><td>{x.kind}</td><td>{x.txn ? `T${x.txn}` : ""}</td><td className="mono">{x.page ?? ""}</td>
                  <td className="mono">{x.kind === "update" ? `${x.before} → ${x.after}` : x.kind === "CLR" ? `→ ${x.after}` : ""}</td>
                  <td className="num mono">{x.prev ?? ""}</td><td className="num mono">{x.kind === "CLR" ? (x.undo_next ?? "none") : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {!r.crashed ? <p>No crash in this log, so there is nothing to recover. Add a <code>crash</code> line.</p> : (
        <>
          <section aria-labelledby="crash">
            <h2 id="crash">At the crash</h2>
            <p className="hint">Memory is lost. The disk has only the pages that were flushed, each with the LSN of the last change it holds.</p>
            <KeyValues rows={Object.entries(r.at_crash ?? {}).map(([p, v]) => [p, `${v.value} (pageLSN ${v.page_lsn})`])} head={["Page", "On disk"]} />
          </section>
          {r.analysis && (
            <section aria-labelledby="analysis">
              <h2 id="analysis">1. Analysis</h2>
              <p>Starts at LSN {r.analysis.start_lsn}{r.analysis.start_lsn > 10 ? ", the last checkpoint" : ""}. Losers to undo: {r.analysis.losers.map((t) => `T${t}`).join(", ") || "none"}.</p>
              <div className="decompositions">
                <KeyValues rows={r.analysis.transactions.map((t) => [`T${t.txn}`, `${t.status}, lastLSN ${t.last_lsn}${kept.has(t.txn) ? " (winner)" : ""}`])} head={["Transaction", "State"]} />
                <KeyValues rows={r.analysis.dirty_pages.map((p) => [p.page, `recLSN ${p.rec_lsn}`])} head={["Dirty page", "First change not known on disk"]} />
              </div>
            </section>
          )}
          {r.redo && (
            <section aria-labelledby="redo">
              <h2 id="redo">2. Redo</h2>
              <p>Repeats history from LSN {r.redo.start_lsn ?? "none"}, the smallest recLSN.</p>
              <div className="table-wrap">
                <table>
                  <thead><tr><th scope="col" className="num">LSN</th><th scope="col">Page</th><th scope="col">Decision</th><th scope="col">Why</th></tr></thead>
                  <tbody>{r.redo.steps.map((s) => (
                    <tr key={s.lsn}><td className="num mono">{s.lsn}</td><td className="mono">{s.page} = {s.value}</td><td className={s.action === "redo" ? "tone-high" : undefined}>{s.action}</td><td>{s.reason}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </section>
          )}
          <section aria-labelledby="undo">
            <h2 id="undo">3. Undo</h2>
            {r.undo.length === 0 ? <p>Nothing to undo.</p> : (
              <ol className="lab-steps">
                {r.undo.map((s) => (
                  <li key={s.lsn}>
                    <span className="mono">LSN {s.lsn}</span>{" "}
                    {s.kind === "CLR"
                      ? `CLR for T${s.txn}: undo LSN ${s.undoes}, set ${s.page} back to ${s.value}; undoNext ${s.undo_next ?? "none"}.`
                      : `end of T${s.txn}${s.note ? ` (${s.note})` : ""}.`}
                  </li>
                ))}
              </ol>
            )}
          </section>
          <section aria-labelledby="final">
            <h2 id="final">After recovery</h2>
            <KeyValues rows={Object.entries(r.final).map(([p, v]) => [p, `${r.at_crash?.[p]?.value ?? "?"} → ${v}`])} head={["Page", "Disk at crash → after recovery"]} />
            <p className="hint">Committed changes survive; every change by a transaction that never committed is gone.</p>
          </section>
        </>
      )}
    </>
  );
}

function KeyValues({ rows, head }: { rows: string[][]; head: [string, string] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th scope="col">{head[0]}</th><th scope="col">{head[1]}</th></tr></thead>
        <tbody>{rows.map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td>{v}</td></tr>)}</tbody>
      </table>
    </div>
  );
}
