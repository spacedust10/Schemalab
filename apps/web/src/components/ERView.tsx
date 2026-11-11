"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { ERModel } from "@/lib/er";
import { errorText, postJSON } from "@/lib/post";
import { ERDiagram, type Notation } from "./ERDiagram";

const DIALECTS = [
  ["postgres", "PostgreSQL"],
  ["mysql", "MySQL"],
  ["sqlite", "SQLite"],
  ["tsql", "SQL Server"],
  ["oracle", "Oracle"],
] as const;
type Dialect = (typeof DIALECTS)[number][0];

const EXAMPLE = `# A small university. 1, N and M count the entity beside them; ! means every row takes part.
entity Professor { pid int key, name text }
entity Student { sid int key, name text, gpa decimal(3,2) }
entity Course { cid varchar(10) key, title text, credits int }
entity Department { dname text key, building text }

relationship Advises: Professor 1 -- N Student
relationship Enrolls: Student M -- N Course! { grade text }
relationship Heads: Professor 1 -- 1 Department!
`;

type Result = { design: string; model: ERModel; ddl: string };

export function ERView() {
  const router = useRouter();
  const [design, setDesign] = useState(EXAMPLE);
  const [dialect, setDialect] = useState<Dialect>("postgres");
  const [notation, setNotation] = useState<Notation>("crow");
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [english, setEnglish] = useState("");
  const [sql, setSql] = useState("");
  const [copied, setCopied] = useState(false);

  const call = useCallback(
    async (label: string, request: () => Promise<Response>): Promise<{ design: string } | null> => {
      setBusy(label);
      setError(null);
      try {
        const res = await request();
        if (res.status === 401) {
          router.replace("/login");
          return null;
        }
        if (!res.ok) {
          setError(await errorText(res, "That did not work. Check the input and try again."));
          return null;
        }
        return (await res.json()) as { design: string };
      } catch {
        setError("Lost the connection to the server.");
        return null;
      } finally {
        setBusy(null);
      }
    },
    [router],
  );

  const generate = useCallback(
    async (text: string, to: Dialect) => {
      const got = await call("Drawing…", () => postJSON("/api/er/ddl", { design: text, dialect: to }));
      if (got) setResult(got as Result);
    },
    [call],
  );

  useEffect(() => {
    // First draw of the example; later draws go through generate, which shows progress.
    postJSON("/api/er/ddl", { design: EXAMPLE, dialect: "postgres" })
      .then((res) => (res.ok ? (res.json() as Promise<Result>) : null))
      .then((got) => got && setResult(got))
      .catch(() => undefined);
  }, []);

  async function load(label: string, request: () => Promise<Response>) {
    const got = await call(label, request);
    if (!got) return;
    setDesign(got.design);
    await generate(got.design, dialect);
  }

  function download() {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([result.ddl], { type: "application/sql" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `schema-${dialect}.sql`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main id="main" className="er-page">
      <header className="data-head">
        <h1>Design a schema</h1>
        <p className="lede">
          Write entities and relationships, or start from a description, existing CREATE TABLE
          statements or your uploaded tables. See the design as a Chen or crow&apos;s-foot
          diagram and get the tables in five SQL dialects.
        </p>
      </header>

      <section className="er-input" aria-label="Design">
        <div className="field">
          <label htmlFor="design">Design</label>
          <textarea id="design" className="mono" rows={12} value={design} onChange={(e) => setDesign(e.target.value)} spellCheck={false} aria-describedby="design-hint" />
          <p id="design-hint" className="hint">
            <code>entity Name {"{ id int key, name text }"}</code> and{" "}
            <code>relationship Name: A 1 -- N B {"{ attr type }"}</code>. Use <code>M -- N</code>{" "}
            for many-to-many and <code>!</code> after an entity when every row must take part.
          </p>
        </div>
        <button className="button" type="button" disabled={busy !== null} onClick={() => void generate(design, dialect)}>
          {busy ?? "Draw and generate SQL"}
        </button>

        <div className="er-sources">
          <details>
            <summary>Draft from a description</summary>
            <div className="field">
              <label htmlFor="english">Describe the data in plain English</label>
              <textarea id="english" rows={4} maxLength={4000} value={english} onChange={(e) => setEnglish(e.target.value)} placeholder="Students enroll in courses and get a grade. Each course is taught by one professor." />
              <p className="hint">The model drafts a design; it is kept only if it parses, and you can edit it.</p>
            </div>
            <button className="button quiet" type="button" disabled={busy !== null || !english.trim()} onClick={() => void load("Drafting…", () => postJSON("/api/er/from-english", { description: english }))}>
              Draft design
            </button>
          </details>
          <details>
            <summary>Read CREATE TABLE statements</summary>
            <div className="field">
              <label htmlFor="sql">SQL</label>
              <textarea id="sql" className="mono" rows={6} value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false} placeholder="CREATE TABLE student (sid INT PRIMARY KEY, ...);" />
            </div>
            <button className="button quiet" type="button" disabled={busy !== null || !sql.trim()} onClick={() => void load("Reading…", () => postJSON("/api/er/from-ddl", { ddl: sql }))}>
              Read SQL
            </button>
          </details>
          <button className="button quiet" type="button" disabled={busy !== null} onClick={() => void load("Reading…", () => fetch("/api/er/mine"))}>
            Use my uploaded tables
          </button>
        </div>
      </section>

      {error && (
        <p className="alert" role="alert">
          <strong>Error</strong>
          {error}
        </p>
      )}

      {result && (
        <>
          <section aria-labelledby="diagram">
            <div className="er-bar">
              <h2 id="diagram">Diagram</h2>
              <fieldset className="source">
                <legend>Notation</legend>
                {(["crow", "chen"] as const).map((n) => (
                  <label key={n}>
                    <input type="radio" name="notation" checked={notation === n} onChange={() => setNotation(n)} />
                    {n === "crow" ? "Crow's foot" : "Chen"}
                  </label>
                ))}
              </fieldset>
            </div>
            <ERDiagram model={result.model} notation={notation} />
          </section>

          <section aria-labelledby="ddl">
            <div className="er-bar">
              <h2 id="ddl">SQL</h2>
              <div className="field er-dialect">
                <label htmlFor="dialect">Dialect</label>
                <select
                  id="dialect"
                  value={dialect}
                  onChange={(e) => {
                    const to = e.target.value as Dialect;
                    setDialect(to);
                    void generate(design, to);
                  }}
                >
                  {DIALECTS.map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                className="button quiet"
                onClick={() => {
                  navigator.clipboard.writeText(result.ddl).then(() => setCopied(true), () => setCopied(false));
                }}
              >
                {copied ? "Copied" : "Copy"}
              </button>
              <button type="button" className="button quiet" onClick={download}>
                Download .sql
              </button>
            </div>
            <pre className="sql er-ddl">
              <code>{result.ddl}</code>
            </pre>
          </section>
        </>
      )}
    </main>
  );
}
