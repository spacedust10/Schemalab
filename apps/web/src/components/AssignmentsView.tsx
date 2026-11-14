"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { Checked, Summary } from "@/lib/assignments";
import { errorText, postJSON } from "@/lib/post";

type Draft = {
  title: string;
  prompt: string;
  schema: string;
  data: string;
  reference: string;
  hidden: { name: string; data: string }[];
  ordered: boolean;
  mode: "tutor" | "exam";
  max_attempts: number;
};

const EMPTY: Draft = {
  title: "",
  prompt: "",
  schema: "CREATE TABLE ...",
  data: "INSERT INTO ...",
  reference: "SELECT ...",
  hidden: [{ name: "", data: "" }],
  ordered: false,
  mode: "tutor",
  max_attempts: 3,
};

export function AssignmentsView() {
  const router = useRouter();
  const [list, setList] = useState<Summary[] | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [checked, setChecked] = useState<Checked | null>(null);
  const [published, setPublished] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      fetch("/api/assignments").then(async (res) => {
        if (res.status === 401) return router.replace("/login");
        if (!res.ok) return setError(await errorText(res, "Could not load assignments."));
        setList((await res.json()) as Summary[]);
      }),
    [router],
  );

  useEffect(() => {
    load().catch(() => setError("Could not reach the server."));
  }, [load]);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setChecked(null);
  };
  const body = () => ({ ...draft, hidden: draft.hidden.filter((h) => h.name.trim() && h.data.trim()) });

  async function send(path: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await postJSON(path, body());
      if (res.status === 401) return router.replace("/login");
      const json = (await res.json().catch(() => ({}))) as { detail?: unknown; id?: string; errors?: string[]; warnings?: string[] };
      if (res.status === 400 && json.detail && typeof json.detail === "object") return setChecked(json.detail as Checked);
      if (!res.ok) throw new Error(typeof json.detail === "string" ? json.detail : "That did not work.");
      if (json.id) {
        setPublished(json.id);
        setChecked({ errors: [], warnings: json.warnings ?? [] });
        await load();
      } else setChecked(json as Checked);
    } catch (e) {
      setError((e as Error).message || "Lost the connection to the server.");
    } finally {
      setBusy(false);
    }
  }

  const area = (key: "schema" | "data" | "reference", label: string, hint: string, rows = 4) => (
    <div className="field">
      <label htmlFor={`b-${key}`}>{label}</label>
      <textarea id={`b-${key}`} className="mono" rows={rows} value={draft[key]} onChange={(e) => set(key, e.target.value)} spellCheck={false} aria-describedby={`b-${key}-hint`} />
      <p id={`b-${key}-hint`} className="hint">{hint}</p>
    </div>
  );

  return (
    <main id="main" className="er-page">
      <header className="data-head">
        <h1>Assignments</h1>
        <p className="lede">
          SQL exercises graded on hidden test databases, with partial credit. Take one below, or
          build your own and share its link.
        </p>
      </header>

      {error && <p className="alert" role="alert"><strong>Error</strong>{error}</p>}

      <section aria-labelledby="a-list">
        <h2 id="a-list">Take an assignment</h2>
        <ul className="notes">
          {list?.map((s) => (
            <li key={s.id}>
              <Link href={`/assignments/${encodeURIComponent(s.id)}`} prefetch={false}>{s.title}</Link>{" "}
              <span className="hint">{s.mode === "exam" ? "exam" : "tutor"}{s.mine ? ", yours" : ""}</span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="a-build" className="er-input">
        <h2 id="a-build">Build an assignment</h2>
        <div className="field">
          <label htmlFor="b-title">Title</label>
          <input id="b-title" value={draft.title} onChange={(e) => set("title", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="b-prompt">What students should find</label>
          <textarea id="b-prompt" rows={2} value={draft.prompt} onChange={(e) => set("prompt", e.target.value)} />
        </div>
        {area("schema", "Tables", "CREATE TABLE statements only, with lowercase names.")}
        {area("data", "Sample data", "INSERT statements; students see these rows.")}
        {area("reference", "Reference query", "The correct SELECT. Only you will see it.", 3)}
        <fieldset className="field">
          <legend>Hidden test databases</legend>
          <p className="hint">Each is its own set of INSERTs into the same tables, chosen to catch a mistake: a NULL, a tie, a row with no match. Students see only their names.</p>
          {draft.hidden.map((h, i) => (
            <div key={i} className="field">
              <label htmlFor={`b-h${i}`}>Test {i + 1}: what it checks</label>
              <input id={`b-h${i}`} value={h.name} onChange={(e) => set("hidden", draft.hidden.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <label htmlFor={`b-h${i}-data`}>Its INSERTs</label>
              <textarea id={`b-h${i}-data`} className="mono" rows={3} value={h.data} onChange={(e) => set("hidden", draft.hidden.map((x, j) => (j === i ? { ...x, data: e.target.value } : x)))} spellCheck={false} />
              <button type="button" className="button quiet" onClick={() => set("hidden", draft.hidden.filter((_, j) => j !== i))}>Remove test {i + 1}</button>
            </div>
          ))}
          <button type="button" className="button quiet" disabled={draft.hidden.length >= 10} onClick={() => set("hidden", [...draft.hidden, { name: "", data: "" }])}>Add a hidden test</button>
        </fieldset>
        <div className="field">
          <label><input type="checkbox" checked={draft.ordered} onChange={(e) => set("ordered", e.target.checked)} /> Row order counts</label>
        </div>
        <div className="field">
          <label htmlFor="b-mode">Mode</label>
          <select id="b-mode" className="nf-select" value={draft.mode} onChange={(e) => set("mode", e.target.value as Draft["mode"])}>
            <option value="tutor">Tutor: unlimited attempts, hints and example rows</option>
            <option value="exam">Exam: limited attempts, best score kept, no hidden rows shown</option>
          </select>
        </div>
        {draft.mode === "exam" && (
          <div className="field">
            <label htmlFor="b-attempts">Attempts</label>
            <input id="b-attempts" type="number" min={1} max={20} value={draft.max_attempts} onChange={(e) => set("max_attempts", Number(e.target.value))} />
          </div>
        )}
        <div className="er-bar">
          <button type="button" className="button quiet" disabled={busy} onClick={() => void send("/api/assignments/check")}>Check</button>
          <button type="button" className="button" disabled={busy || (checked?.errors.length ?? 0) > 0} onClick={() => void send("/api/assignments")}>Publish</button>
        </div>
        {checked && (
          <div role="status">
            {checked.errors.length === 0 && checked.warnings.length === 0 && <p className="hint">No problems found.</p>}
            {checked.errors.length > 0 && <ul className="notes">{checked.errors.map((e) => <li key={e}><strong>Must fix:</strong> {e}</li>)}</ul>}
            {checked.warnings.length > 0 && <ul className="notes">{checked.warnings.map((w) => <li key={w}><strong>Worth a look:</strong> {w}</li>)}</ul>}
          </div>
        )}
        {published && (
          <p>
            Published. Share this link with students:{" "}
            <Link href={`/assignments/${encodeURIComponent(published)}`} prefetch={false}>/assignments/{published}</Link>
          </p>
        )}
      </section>
    </main>
  );
}
