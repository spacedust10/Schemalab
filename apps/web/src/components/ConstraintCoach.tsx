"use client";

import { useState } from "react";
import type { Suggestion } from "@/lib/algebra";
import { errorText } from "@/lib/post";

const LABEL: Record<string, string> = {
  "primary key": "Primary key",
  "foreign key": "Foreign key",
  "not null": "Not null",
  unique: "Unique",
  check: "Check",
};

/** Constraints a table's data already satisfies, with the evidence and the DDL to add them. */
export function ConstraintCoach({ table }: { table: string }) {
  const [items, setItems] = useState<Suggestion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true);
    setError(null);
    const res = await fetch(`/api/constraints?${new URLSearchParams({ table, dataset: "mine" })}`);
    setBusy(false);
    if (!res.ok) return setError(await errorText(res, "Could not check this table."));
    setItems((await res.json()) as Suggestion[]);
  }

  if (!items) {
    return (
      <div className="coach">
        <button type="button" className="button quiet" disabled={busy} onClick={() => void load()}>
          {busy ? "Checking the data…" : "Suggest constraints"}
        </button>
        {error && <p className="alert" role="alert"><strong>Error</strong>{error}</p>}
      </div>
    );
  }
  return (
    <div className="coach">
      <h3>Constraints your data already satisfies</h3>
      {items.length === 0 ? (
        <p className="hint">None found: every candidate is broken by at least one row.</p>
      ) : (
        <>
          <ul className="coach-list">
            {items.map((s) => (
              <li key={s.sql}>
                <span className="coach-kind">{LABEL[s.kind] ?? s.kind}</span> <span className="mono">{s.columns.join(", ")}</span>
                <p className="hint">{s.evidence}</p>
              </li>
            ))}
          </ul>
          <details>
            <summary>SQL to add them</summary>
            <pre className="sql"><code>{items.map((s) => s.sql).join("\n")}</code></pre>
          </details>
        </>
      )}
    </div>
  );
}
