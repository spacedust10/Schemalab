import type { Attempt, Trace } from "@/lib/trace";

/**
 * The trace: the plan, then one numbered row per SQL attempt with what happened to it. Each
 * outcome is spelled out in words; color only repeats what the words say.
 */
export function TraceRail({ trace, busy }: { trace: Trace; busy: boolean }) {
  return (
    <>
      <section>
        <h2>Plan</h2>
        {trace.plan ? <p className="plan">{trace.plan}</p> : <p className="hint">{busy ? "Planning…" : "No plan."}</p>}
      </section>
      {trace.attempts.length > 0 && (
        <section>
          <h2>Attempts</h2>
          <ol className="attempts">
            {trace.attempts.map((a) => (
              <AttemptRow key={a.n} attempt={a} />
            ))}
          </ol>
        </section>
      )}
    </>
  );
}

function AttemptRow({ attempt: a }: { attempt: Attempt }) {
  const tone =
    a.status !== "ran" || a.critic?.ok === false ? "var(--object)" : a.critic?.ok ? "var(--ok)" : undefined;
  return (
    <li className="attempt" style={tone ? ({ "--tone": tone } as React.CSSProperties) : undefined}>
      <span className="n" aria-label={`Attempt ${a.n}`}>
        {a.n}
      </span>
      <pre>
        <code>{a.sql}</code>
      </pre>
      <div className="marks">
        {a.status === "rejected" && <span className="mark mark-bad">✕ guard rejected</span>}
        {a.status !== "rejected" && <span className="mark mark-ok">✓ guard passed</span>}
        {a.status === "failed" && <span className="mark mark-bad">✕ database error</span>}
        {a.status === "ran" && (
          <span className="mark mark-ok">
            ✓ ran, {a.rows?.length ?? 0} {a.rows?.length === 1 ? "row" : "rows"}
          </span>
        )}
        {a.critic && <CriticMark ok={a.critic.ok} />}
      </div>
      {a.message && <p className="note">{a.message}</p>}
      {a.critic?.issue && <p className="note">Reviewer: {a.critic.issue}</p>}
    </li>
  );
}

function CriticMark({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span className="mark mark-none">– reviewer gave no verdict</span>;
  return ok ? (
    <span className="mark mark-ok">✓ reviewer agrees</span>
  ) : (
    <span className="mark mark-bad">✕ reviewer objects</span>
  );
}
