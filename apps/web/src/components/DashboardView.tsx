"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { Cell } from "@/lib/chart";
import { pickChart } from "@/lib/chart";
import { errorText } from "@/lib/post";
import { takeaway } from "@/lib/takeaway";
import { ResultChart } from "./ResultChart";
import { ResultTable } from "./ResultTable";

type Pin = {
  id: number;
  question: string;
  sql: string;
  dataset: "sample" | "mine";
  columns: string[] | null;
  rows: Cell[][] | null;
  truncated: boolean;
  error: string | null;
};

export function DashboardView() {
  const router = useRouter();
  const [pins, setPins] = useState<Pin[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      fetch("/api/pins").then(async (res) => {
        if (res.status === 401) return router.replace("/login");
        if (!res.ok) return setError(await errorText(res, "Could not load your dashboard."));
        setPins((await res.json()) as Pin[]);
      }),
    [router],
  );

  useEffect(() => {
    load().catch(() => setError("Could not reach the server."));
  }, [load]);

  async function remove(id: number) {
    const res = await fetch(`/api/pins/${id}`, { method: "DELETE" });
    if (!res.ok) return setError(await errorText(res, "Could not remove that pin."));
    await load();
  }

  return (
    <main id="main" className="dashboard">
      <header className="data-head">
        <h1>Dashboard</h1>
        <p className="lede">
          Answers you pinned. Each one re-runs its query through the SQL guard when this page
          opens, so the numbers are current.
        </p>
        {error && (
          <p className="alert" role="alert">
            <strong>Error</strong>
            {error}
          </p>
        )}
      </header>
      {pins?.length === 0 && (
        <p className="hint">
          Nothing pinned yet. <Link href="/" prefetch={false}>Ask a question</Link> and choose Pin
          to dashboard under the answer.
        </p>
      )}
      <div className="cards">
        {pins?.map((p) => (
          <PinCard key={p.id} pin={p} onRemove={() => void remove(p.id)} />
        ))}
      </div>
    </main>
  );
}

function PinCard({ pin, onRemove }: { pin: Pin; onRemove: () => void }) {
  const result = pin.columns && pin.rows ? { columns: pin.columns, rows: pin.rows, truncated: pin.truncated } : null;
  const spec = result ? pickChart(result.columns, result.rows) : null;
  return (
    <section className="card" aria-labelledby={`pin-${pin.id}`}>
      <div className="dataset-head">
        <h2 id={`pin-${pin.id}`}>{pin.question}</h2>
        <button type="button" className="button quiet" onClick={onRemove}>
          Remove<span className="sr-only"> {pin.question}</span>
        </button>
      </div>
      <p className="hint">{pin.dataset === "mine" ? "Your tables" : "Sample data"}</p>
      {pin.error && (
        <p className="alert" role="alert">
          <strong>Cannot run</strong>
          {pin.error}
        </p>
      )}
      {spec && <p className="card-takeaway">{takeaway(spec)}</p>}
      {spec && <ResultChart spec={spec} />}
      {result && <ResultTable result={result} />}
      <details>
        <summary>SQL</summary>
        <pre className="sql">
          <code>{pin.sql}</code>
        </pre>
      </details>
    </section>
  );
}
