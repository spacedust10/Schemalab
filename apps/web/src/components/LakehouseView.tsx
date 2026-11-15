"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { busy, changeLine, LAYERS, type Layer, type Model, type Overview, type Preview, runLine } from "@/lib/lakehouse";
import { errorText } from "@/lib/post";

const LAYER_NOTE: Record<Layer, string> = {
  bronze: "Raw, as loaded. Nothing here yet: load a file, the earthquake feed or a sheet.",
  silver: "Cleaned and typed. Write a silver model below to make one.",
  gold: "Summaries to answer questions with. Write a gold model below.",
};

export function LakehouseView() {
  const router = useRouter();
  const [data, setData] = useState<Overview | null>(null);
  const [models, setModels] = useState<Model[] | null>(null); // the draft, once loaded
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<{ layer: Layer; name: string } | null>(null);

  const load = useCallback(
    () =>
      fetch("/api/lakehouse").then(async (res) => {
        if (res.status === 401) return router.replace("/login");
        if (!res.ok) return setError(await errorText(res, "The lakehouse could not be loaded."));
        const body = (await res.json()) as Overview;
        setData(body);
        setModels((draft) => draft ?? body.models);
      }),
    [router],
  );

  useEffect(() => {
    load().catch(() => setError("Could not reach the server."));
  }, [load]);

  // Check back while a run is going; stop once none is.
  useEffect(() => {
    if (!data || !busy(data.runs)) return;
    const timer = setTimeout(() => void load(), 2000);
    return () => clearTimeout(timer);
  }, [data, load]);

  async function send(path: string, init: RequestInit): Promise<boolean> {
    setError(null);
    const res = await fetch(`/api/lakehouse/${path}`, init);
    if (!res.ok) {
      setError(await errorText(res, "That did not work."));
      return false;
    }
    await load();
    return true;
  }

  const json = (method: string, body: unknown): RequestInit => ({
    method,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

  async function loadFile(form: HTMLFormElement) {
    const input = form.elements.namedItem("file") as HTMLInputElement;
    const table = (form.elements.namedItem("table") as HTMLInputElement).value.trim();
    const file = input.files?.[0];
    if (!file) return setError("Choose a file first.");
    const query = new URLSearchParams(table ? { name: file.name, table } : { name: file.name });
    if (await send(`files?${query}`, { method: "POST", headers: { "content-type": "application/octet-stream" }, body: file })) form.reset();
  }

  async function loadSheet(form: HTMLFormElement) {
    const url = (form.elements.namedItem("url") as HTMLInputElement).value.trim();
    const table = (form.elements.namedItem("sheet-table") as HTMLInputElement).value.trim() || "sheet";
    if (await send("sheets", json("POST", { url, table }))) form.reset();
  }

  async function saveModels(run: boolean) {
    if (!models) return;
    if (!(await send("models", json("PUT", { models })))) return;
    if (run) await send("transform", json("POST", {}));
  }

  if (!data) {
    return (
      <main id="main" className="er-page">
        {error ? <p className="alert" role="alert"><strong>Error</strong>{error}</p> : <p className="hint">Loading…</p>}
      </main>
    );
  }
  const running = busy(data.runs);
  const bronze = data.tables.filter((t) => t.layer === "bronze");
  const draft = models ?? [];

  return (
    <main id="main" className="er-page">
      <header className="data-head">
        <h1>Lakehouse</h1>
        <p className="lede">
          Load data into bronze as it arrives, clean it into silver, and sum it up in gold. Every load and every model run is a
          snapshot you can go back to, and Ask can answer questions from your silver and gold tables.
        </p>
      </header>

      {error && <p className="alert" role="alert"><strong>Error</strong>{error}</p>}

      <section aria-labelledby="lake-load">
        <h2 id="lake-load">Load into bronze</h2>
        <form className="field" onSubmit={(e) => { e.preventDefault(); void loadFile(e.currentTarget); }}>
          <label htmlFor="lake-file">A file: .csv, .xlsx or .parquet, up to 50 MB</label>
          <input id="lake-file" name="file" type="file" accept=".csv,.xlsx,.parquet" />
          <label htmlFor="lake-table">Table name (optional; the file name otherwise)</label>
          <input id="lake-table" name="table" maxLength={63} autoComplete="off" />
          <button className="button" type="submit" disabled={running}>Load the file</button>
        </form>
        <div className="field">
          <p>
            Earthquakes from the USGS feed, magnitude 2.5 and up. The first load takes the last seven days; after that each load
            asks only for what happened since the newest one you have, and merges by event id.
          </p>
          <button className="button" type="button" disabled={running} onClick={() => void send("quakes", json("POST", {}))}>
            Load new earthquakes
          </button>
        </div>
        <form className="field" onSubmit={(e) => { e.preventDefault(); void loadSheet(e.currentTarget); }}>
          <label htmlFor="lake-sheet">A Google Sheet link</label>
          <input id="lake-sheet" name="url" type="url" required placeholder="https://docs.google.com/spreadsheets/d/…" aria-describedby="lake-sheet-hint" />
          <p id="lake-sheet-hint" className="hint">Share the sheet as “Anyone with the link”. The tab in the link is the one loaded.</p>
          <label htmlFor="lake-sheet-table">Table name</label>
          <input id="lake-sheet-table" name="sheet-table" maxLength={63} defaultValue="sheet" autoComplete="off" />
          <button className="button" type="submit" disabled={running}>Load the sheet</button>
        </form>
      </section>

      <section aria-labelledby="lake-runs">
        <h2 id="lake-runs">Runs</h2>
        {data.runs.length === 0 ? (
          <p className="hint">No runs yet.</p>
        ) : (
          <ul className="notes" aria-live="polite">
            {data.runs.map((r) => (
              <li key={r.id}>
                <strong>{r.label}</strong> ({new Date(r.created_at).toLocaleString()}): {r.status === "failed" ? "failed. " : ""}
                {runLine(r)}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="lake-tables">
        <h2 id="lake-tables">Tables</h2>
        <div className="lake-layers">
          {LAYERS.map((layer) => {
            const tables = data.tables.filter((t) => t.layer === layer);
            return (
              <div key={layer}>
                <h3>{layer}</h3>
                {tables.length === 0 ? (
                  <p className="hint">{LAYER_NOTE[layer]}</p>
                ) : (
                  <ul className="notes">
                    {tables.map((t) => (
                      <li key={t.name}>
                        <button type="button" className="button quiet" aria-pressed={open?.layer === layer && open.name === t.name} onClick={() => setOpen({ layer, name: t.name })}>
                          {t.name}
                        </button>{" "}
                        {t.rows.toLocaleString()} row{t.rows === 1 ? "" : "s"}
                        {layer !== "gold" && <span className="hint"> · read it as {layer}_{t.name}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
        {open && <TablePreview key={`${open.layer}.${open.name}`} {...open} snapshots={data.snapshots} />}
      </section>

      <section aria-labelledby="lake-models">
        <h2 id="lake-models">Silver and gold models</h2>
        <p>
          Each model is one SELECT that becomes a table. Read bronze and silver tables by their layer and name, like
          bronze_orders. Silver models run first, then gold, all in one snapshot: if one fails, nothing changes.
        </p>
        {draft.length === 0 && bronze.length > 0 && (
          <button
            type="button"
            className="button quiet"
            onClick={() =>
              setModels([
                { layer: "silver", name: bronze[0].name, sql: `SELECT DISTINCT *\nFROM bronze_${bronze[0].name}` },
                { layer: "gold", name: `${bronze[0].name}_count`, sql: `SELECT count(*) AS row_count\nFROM silver_${bronze[0].name}` },
              ])
            }
          >
            Start from an example
          </button>
        )}
        {draft.map((m, i) => (
          <fieldset key={i} className="lake-model">
            <legend>Model {i + 1}</legend>
            <label htmlFor={`m-layer-${i}`}>Layer</label>
            <select id={`m-layer-${i}`} value={m.layer} onChange={(e) => setModels(draft.map((x, j) => (j === i ? { ...x, layer: e.target.value as Model["layer"] } : x)))}>
              <option value="silver">silver</option>
              <option value="gold">gold</option>
            </select>
            <label htmlFor={`m-name-${i}`}>Table name</label>
            <input id={`m-name-${i}`} value={m.name} maxLength={63} pattern="[a-z_][a-z0-9_]*" autoComplete="off" onChange={(e) => setModels(draft.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
            <label htmlFor={`m-sql-${i}`}>SELECT</label>
            <textarea id={`m-sql-${i}`} className="sql" rows={4} maxLength={5000} spellCheck={false} value={m.sql} onChange={(e) => setModels(draft.map((x, j) => (j === i ? { ...x, sql: e.target.value } : x)))} />
            <button type="button" className="button quiet" onClick={() => setModels(draft.filter((_, j) => j !== i))}>Remove model {i + 1}</button>
          </fieldset>
        ))}
        <div className="er-bar">
          <button type="button" className="button quiet" disabled={draft.length >= 20} onClick={() => setModels([...draft, { layer: "silver", name: "", sql: "" }])}>
            Add a model
          </button>
          <button type="button" className="button quiet" onClick={() => void saveModels(false)}>Save</button>
          <button type="button" className="button" disabled={running || draft.length === 0} onClick={() => void saveModels(true)}>
            Save and run the models
          </button>
        </div>
      </section>

      <section aria-labelledby="lake-history">
        <h2 id="lake-history">Snapshots</h2>
        <p className="hint">Every change to the lake, newest first. Open a table above to see it as it was at any of them.</p>
        <ol className="notes" reversed>
          {data.snapshots.map((s) => (
            <li key={s.id}>
              <strong>#{s.id}</strong> {new Date(s.at).toLocaleString()}: {changeLine(s.changes)}
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}

function TablePreview({ layer, name, snapshots }: { layer: Layer; name: string; snapshots: Overview["snapshots"] }) {
  const [version, setVersion] = useState<string>("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    heading.current?.focus();
  }, []);

  useEffect(() => {
    const query = version ? `?${new URLSearchParams({ version })}` : "";
    fetch(`/api/lakehouse/tables/${layer}/${name}${query}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(await errorText(res, "This table could not be read."));
        setError(null);
        setPreview((await res.json()) as Preview);
      })
      .catch((e: Error) => {
        setPreview(null);
        setError(e.message);
      });
  }, [layer, name, version]);

  return (
    <div className="lake-preview">
      <h3 ref={heading} tabIndex={-1}>{layer}.{name}</h3>
      <div className="field">
        <label htmlFor="lake-version">As of</label>
        <select id="lake-version" value={version} onChange={(e) => setVersion(e.target.value)}>
          <option value="">Now</option>
          {snapshots.map((s) => (
            <option key={s.id} value={s.id}>
              Snapshot #{s.id}, {new Date(s.at).toLocaleString()}
            </option>
          ))}
        </select>
      </div>
      {error && <p className="alert" role="alert"><strong>Error</strong>{error}</p>}
      {preview && (
        <div className="table-wrap">
          <table>
            <caption>
              {preview.rows.length < preview.total
                ? `The first ${preview.rows.length} of ${preview.total.toLocaleString()} rows.`
                : `${preview.total.toLocaleString()} row${preview.total === 1 ? "" : "s"}.`}
            </caption>
            <thead>
              <tr>{preview.columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr>
            </thead>
            <tbody>
              {preview.rows.map((row, i) => (
                <tr key={i}>{row.map((v, j) => <td key={j}>{v === null ? "" : String(v)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
