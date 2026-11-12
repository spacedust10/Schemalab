"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { type Entry, type LabState, type Outcome, type Scenario, expectation, record, settle } from "@/lib/lab";
import { errorText, postJSON } from "@/lib/post";
import { ResultTable } from "./ResultTable";

type Name = "A" | "B";
const NAMES: Name[] = ["A", "B"];
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function LabView() {
  const router = useRouter();
  const [started, setStarted] = useState(false);
  const [scenarios, setScenarios] = useState<{ id: string; title: string; summary: string }[]>([]);
  const [chosen, setChosen] = useState("lost-update");
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [step, setStep] = useState(0);
  const [history, setHistory] = useState<Record<Name, Entry[]>>({ A: [], B: [] });
  const [input, setInput] = useState<Record<Name, string>>({ A: "", B: "" });
  const [waiting, setWaiting] = useState<Record<Name, boolean>>({ A: false, B: false });
  const [inside, setInside] = useState<LabState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    fetch("/api/lab/scenarios")
      .then((r) => (r.ok ? r.json() : []))
      .then(setScenarios)
      .catch(() => setScenarios([]));
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    const res = await fetch("/api/lab/state");
    if (res.ok) setInside((await res.json()) as LabState);
  }, []);

  async function begin(request: () => Promise<Response>): Promise<Response | null> {
    setBusy(true);
    setError(null);
    try {
      const res = await request();
      if (res.status === 401) {
        router.replace("/login");
        return null;
      }
      if (!res.ok) {
        setError(await errorText(res, "The lab could not start."));
        return null;
      }
      setStarted(true);
      setHistory({ A: [], B: [] });
      setWaiting({ A: false, B: false });
      return res;
    } catch {
      setError("Lost the connection to the server.");
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function startEmpty() {
    if (await begin(() => postJSON("/api/lab/start", {}))) {
      setScenario(null);
      await refresh();
    }
  }

  async function loadScenario() {
    const res = await begin(() => postJSON("/api/lab/scenario", { id: chosen }));
    if (!res) return;
    setScenario((await res.json()) as Scenario);
    setStep(0);
    await refresh();
  }

  async function watch(session: Name) {
    // A blocked statement finishes when the other session commits, rolls back or deadlocks.
    for (let i = 0; i < 80 && alive.current; i++) {
      await sleep(400);
      const res = await fetch(`/api/lab/result?session=${session}`);
      if (!res.ok) break;
      const outcome = (await res.json()) as Outcome;
      if (outcome.state !== "waiting") {
        if (outcome.state !== "idle") setHistory((h) => ({ ...h, [session]: settle(h[session], outcome) }));
        break;
      }
    }
    setWaiting((w) => ({ ...w, [session]: false }));
    await refresh();
  }

  async function run(session: Name, sql: string) {
    if (!sql.trim()) return;
    const res = await postJSON("/api/lab/run", { session, sql });
    const outcome: Outcome = res.ok
      ? ((await res.json()) as Outcome)
      : { state: "error", error: await errorText(res, "The statement was refused.") };
    setHistory((h) => ({ ...h, [session]: record(h[session], sql, outcome) }));
    if (outcome.state === "waiting") {
      setWaiting((w) => ({ ...w, [session]: true }));
      void refresh();
      void watch(session);
    } else {
      await refresh();
    }
  }

  async function nextStep() {
    if (!scenario || step >= scenario.steps.length) return;
    const s = scenario.steps[step];
    setStep(step + 1);
    await run(s.session, s.sql);
  }

  const current = scenario?.steps[step];
  return (
    <main id="main" className="er-page lab-page">
      <header className="data-head">
        <h1>Transaction lab</h1>
        <p className="lede">
          Two real PostgreSQL sessions, A and B, on tables only you can see. Run statements in
          either one and watch locks, waits and row versions as they happen.
        </p>
      </header>

      <section className="er-bar lab-controls" aria-label="Start">
        <div className="field">
          <label htmlFor="scenario">Guided scenario</label>
          <select id="scenario" className="nf-select" value={chosen} onChange={(e) => setChosen(e.target.value)}>
            {scenarios.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
        </div>
        <button type="button" className="button" disabled={busy} onClick={() => void loadScenario()}>
          {busy ? "Starting…" : "Load scenario"}
        </button>
        <button type="button" className="button quiet" disabled={busy} onClick={() => void startEmpty()}>
          {started ? "Reset to an empty lab" : "Start an empty lab"}
        </button>
      </section>

      {error && (
        <p className="alert" role="alert">
          <strong>Error</strong>
          {error}
        </p>
      )}

      {scenario && (
        <section className="lab-guide" aria-labelledby="guide">
          <h2 id="guide">{scenario.title}</h2>
          <p>{scenario.summary}</p>
          <ol className="lab-steps">
            {scenario.steps.map((s, i) => (
              <li key={i} aria-current={i === step ? "step" : undefined} className={i < step ? "done" : undefined}>
                <span className={`lab-badge lab-${s.session}`}>{s.session}</span>
                <code>{s.sql}</code>
                {s.note && <span className="hint"> {s.note}</span>}
                {i === step && expectation(s) && <span className="hint lab-expect"> {expectation(s)}</span>}
              </li>
            ))}
          </ol>
          {current ? (
            <button type="button" className="button" disabled={waiting[current.session]} onClick={() => void nextStep()}>
              Run step {step + 1} in session {current.session}
            </button>
          ) : (
            <p className="lab-lesson">
              <strong>What happened.</strong> {scenario.lesson}
            </p>
          )}
        </section>
      )}

      {started && (
        <>
          <div className="lab-consoles">
            {NAMES.map((n) => (
              <Console
                key={n}
                name={n}
                entries={history[n]}
                status={waiting[n] ? "waiting for a lock" : (inside?.sessions[n]?.transaction ?? "idle")}
                blockedBy={inside?.sessions[n]?.blocked_by ?? []}
                value={input[n]}
                onChange={(v) => setInput((i) => ({ ...i, [n]: v }))}
                onRun={() => {
                  void run(n, input[n]);
                  setInput((i) => ({ ...i, [n]: "" }));
                }}
                disabled={waiting[n]}
              />
            ))}
          </div>
          {inside && <Inside state={inside} />}
        </>
      )}
    </main>
  );
}

function Console(props: {
  name: Name;
  entries: Entry[];
  status: string;
  blockedBy: string[];
  value: string;
  onChange: (v: string) => void;
  onRun: () => void;
  disabled: boolean;
}) {
  const { name, entries, status, blockedBy } = props;
  const tone = status === "failed" ? "tone-low" : status.startsWith("waiting") ? "tone-medium" : status === "in transaction" ? "tone-high" : "";
  return (
    <section className="lab-console" aria-labelledby={`session-${name}`}>
      <div className="lab-console-head">
        <h2 id={`session-${name}`}>
          <span className={`lab-badge lab-${name}`}>{name}</span> Session {name}
        </h2>
        <span className={`lab-status ${tone}`} role="status">
          {status}
          {blockedBy.length > 0 && ` (blocked by ${blockedBy.join(", ")})`}
        </span>
      </div>
      <ol className="lab-log" aria-label={`Session ${name} history`}>
        {entries.map((e, i) => (
          <li key={i}>
            <code className="lab-sql">{e.sql}</code>
            <Result outcome={e.outcome} waited={e.waited} />
          </li>
        ))}
      </ol>
      <form
        className="lab-input"
        onSubmit={(e) => {
          e.preventDefault();
          props.onRun();
        }}
      >
        <label htmlFor={`sql-${name}`} className="sr-only">
          Statement for session {name}
        </label>
        <textarea
          id={`sql-${name}`}
          className="mono"
          rows={2}
          value={props.value}
          spellCheck={false}
          placeholder="One statement, e.g. BEGIN"
          onChange={(e) => props.onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              props.onRun();
            }
          }}
        />
        <button type="submit" className="button quiet" disabled={props.disabled || !props.value.trim()}>
          Run in {name}
        </button>
      </form>
    </section>
  );
}

function Result({ outcome, waited }: { outcome: Outcome; waited?: boolean }) {
  if (outcome.state === "waiting") return <p className="lab-out tone-medium">Waiting for a lock held by the other session…</p>;
  if (outcome.state === "error") {
    return (
      <p className="lab-out tone-low">
        {waited ? "After waiting: " : ""}
        {outcome.error}
        {outcome.code && <span className="hint"> SQLSTATE {outcome.code}</span>}
      </p>
    );
  }
  return (
    <div className="lab-out">
      <span className="hint">
        {waited ? "After waiting: " : ""}
        {outcome.status}
      </span>
      {outcome.columns && outcome.columns.length > 0 && outcome.rows && (
        <ResultTable result={{ columns: outcome.columns, rows: outcome.rows, truncated: false }} />
      )}
    </div>
  );
}

function Inside({ state }: { state: LabState }) {
  const waits = (["A", "B"] as const).flatMap((n) => state.sessions[n].blocked_by.map((b) => `${n} waits for ${b}`));
  return (
    <section className="lab-inside" aria-labelledby="inside">
      <h2 id="inside">Inside the database</h2>
      <p>{waits.length ? waits.join("; ") + "." : "Nobody is waiting."}</p>
      <h3>Locks</h3>
      {state.locks.length === 0 ? (
        <p className="hint">No locks on lab tables right now.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Session</th>
                <th scope="col">Kind</th>
                <th scope="col">On</th>
                <th scope="col">Mode</th>
                <th scope="col">Held</th>
              </tr>
            </thead>
            <tbody>
              {state.locks.map((l, i) => (
                <tr key={i}>
                  <td>{l.session}</td>
                  <td>{l.type}</td>
                  <td className="mono">{l.relation ?? (l.transaction ? `transaction ${l.transaction}` : "")}</td>
                  <td className="mono">{l.mode}</td>
                  <td className={l.granted ? undefined : "tone-medium"}>{l.granted ? "held" : "waiting"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h3>Row versions</h3>
      <p className="hint">
        Every row version records xmin, the transaction that created it, and xmax, the one deleting
        or locking it (0 means none). An UPDATE writes a new version; others keep reading the old
        one until the change commits.
      </p>
      {state.versions.map((t) => (
        <div key={t.table} className="table-wrap">
          <table>
            <caption>{t.locked ? `${t.table}: locked by a session, versions hidden for now` : t.table}</caption>
            <thead>
              <tr>
                <th scope="col" className="num">xmin</th>
                <th scope="col" className="num">xmax</th>
                <th scope="col">ctid</th>
                {t.columns.map((c) => (
                  <th key={c} scope="col">{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {t.rows.map((r) => (
                <tr key={r.ctid}>
                  <td className="num mono">{r.xmin}</td>
                  <td className={`num mono${r.xmax !== "0" ? " tone-medium" : ""}`}>{r.xmax}</td>
                  <td className="mono">{r.ctid}</td>
                  {r.values.map((v, i) => (
                    <td key={i}>{String(v)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}
