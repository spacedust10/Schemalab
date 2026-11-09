"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { errorText } from "@/lib/post";
import { type Dataset, MAX_MB, checkFile, uploadFile } from "@/lib/upload";
import { ProfileTable } from "./ProfileTable";

export function DataView() {
  const router = useRouter();
  const [tables, setTables] = useState<Dataset[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(
    () =>
      fetch("/api/datasets").then(async (res) => {
        if (res.status === 401) return router.replace("/login");
        if (!res.ok) return setError(await errorText(res, "Could not load your tables."));
        setTables((await res.json()) as Dataset[]);
      }),
    [router],
  );

  useEffect(() => {
    load().catch(() => setError("Could not reach the server."));
  }, [load]);

  async function upload(input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = ""; // choosing the same file again should upload it again
    if (!file) return;
    setError(null);
    setNotice(null);
    const problem = checkFile(file.name, file.size);
    if (problem) return setError(problem);
    setBusy(true);
    try {
      const res = await uploadFile(file);
      if (!res.ok) return setError(await errorText(res, "The upload failed. Try again."));
      const body = (await res.json()) as { created: string[]; tables: Dataset[] };
      setTables(body.tables);
      setNotice(`Added ${body.created.join(", ")}.`);
    } catch {
      setError("Lost the connection while uploading.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string) {
    if (!window.confirm(`Delete the table ${name}? This cannot be undone.`)) return;
    setError(null);
    setNotice(null);
    const res = await fetch(`/api/datasets/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (!res.ok) return setError(await errorText(res, "Could not delete that table."));
    setNotice(`Deleted ${name}.`);
    await load();
  }

  return (
    <main id="main" className="data">
      <header className="data-head">
        <h1>Your data</h1>
        <p className="lede">
          Upload a .csv or .xlsx file of up to {MAX_MB} MB. Each file, or each sheet of a
          workbook, becomes a table that only you can see. The model is told column names and
          types, not your rows.
        </p>
        <div className="field upload">
          <label htmlFor="file">File to upload</label>
          <input
            id="file"
            type="file"
            accept=".csv,.xlsx"
            disabled={busy}
            aria-describedby="file-hint"
            onChange={(e) => void upload(e.currentTarget)}
          />
          <p id="file-hint" className="hint">
            The first row should hold column names.
          </p>
        </div>
        <p className="status" role="status">
          {busy ? "Uploading and profiling…" : notice}
        </p>
        {error && (
          <p className="alert" role="alert">
            <strong>Error</strong>
            {error}
          </p>
        )}
      </header>

      {tables?.length === 0 && <p className="hint">No tables yet. Upload a file to start.</p>}
      {tables?.map((t) => (
        <section key={t.name} className="dataset" aria-labelledby={`t-${t.name}`}>
          <div className="dataset-head">
            <h2 id={`t-${t.name}`}>{t.name}</h2>
            <p className="hint">
              {t.rows.toLocaleString("en-US")} rows · {t.columns.length} columns
            </p>
            <button type="button" className="button quiet" onClick={() => void remove(t.name)}>
              Delete<span className="sr-only"> {t.name}</span>
            </button>
          </div>
          <ProfileTable columns={t.columns} />
        </section>
      ))}
    </main>
  );
}
