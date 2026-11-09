"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { pickChart } from "@/lib/chart";
import { errorText } from "@/lib/post";
import { createSSEParser } from "@/lib/sse";
import { takeaway } from "@/lib/takeaway";
import { type AgentEvent, type Trace, emptyTrace, reduceTrace } from "@/lib/trace";
import type { Dataset } from "@/lib/upload";
import { ConfidenceNote } from "./ConfidenceNote";
import { Provenance } from "./Provenance";
import { ResultChart } from "./ResultChart";
import { ResultTable } from "./ResultTable";
import { TraceRail } from "./TraceRail";

const EXAMPLES = [
  "Which pickup borough had the most taxi trips?",
  "What was the average tip by payment type?",
  "How did the share of uninsured Michigan residents change by year?",
];

type Source = "sample" | "mine";

const RECOVERY: Record<string, string> = {
  model: "Check that Ollama is running (ollama serve) and the model is pulled.",
  data: "Build the sample data, then ask again.",
  guard: "Every attempt was rejected by the SQL guard. Try rephrasing the question.",
  run: "Every attempt failed in the database. Try naming the columns you mean.",
};

export function AskView() {
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const [trace, setTrace] = useState<Trace>(emptyTrace);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const [source, setSource] = useState<Source>("sample");
  const [uploads, setUploads] = useState<Dataset[]>([]);

  useEffect(() => {
    fetch("/api/datasets")
      .then((res) => (res.ok ? (res.json() as Promise<Dataset[]>) : []))
      .then(setUploads)
      .catch(() => setUploads([]));
  }, []);

  async function ask(q: string) {
    const text = q.trim();
    if (!text || busy) return;
    abort.current?.abort();
    abort.current = new AbortController();
    setAsked(text);
    setTrace(emptyTrace);
    setFailure(null);
    setBusy(true);
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: text, dataset: source }),
        signal: abort.current.signal,
      });
      if (res.status === 401) {
        router.replace("/login");
        return;
      }
      if (!res.ok || !res.body) {
        setFailure(await errorText(res, `The server answered ${res.status}.`));
        return;
      }
      const push = createSSEParser((e) => setTrace((t) => reduceTrace(t, e as AgentEvent)));
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        push(value);
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setFailure("Lost the connection to the server.");
    } finally {
      setBusy(false);
    }
  }

  const spec = trace.result ? pickChart(trace.result.columns, trace.result.rows) : null;
  const headline = spec ? takeaway(spec) : null;

  return (
    <main id="main" className="ask">
      <section className="question" aria-label="Question">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void ask(question);
          }}
        >
          <fieldset className="source">
            <legend>Ask about</legend>
            <label>
              <input
                type="radio"
                name="source"
                checked={source === "sample"}
                onChange={() => setSource("sample")}
              />
              Sample data
            </label>
            <label>
              <input
                type="radio"
                name="source"
                checked={source === "mine"}
                disabled={uploads.length === 0}
                onChange={() => setSource("mine")}
              />
              Your tables{uploads.length ? ` (${uploads.length})` : ""}
            </label>
            {uploads.length === 0 && (
              <Link href="/data" prefetch={false}>
                Upload a file
              </Link>
            )}
          </fieldset>
          <div className="field">
            <label htmlFor="q">Your question</label>
            <textarea
              id="q"
              rows={2}
              maxLength={2000}
              value={question}
              placeholder={
                source === "mine"
                  ? `For example: how many rows does ${uploads[0]?.name ?? "my table"} have?`
                  : "Which pickup borough had the most taxi trips?"
              }
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void ask(question);
                }
              }}
            />
          </div>
          <button className="button" type="submit" disabled={busy || !question.trim()}>
            {busy ? "Working…" : "Ask"}
          </button>
        </form>
        {!asked && source === "sample" && (
          <div className="examples" aria-label="Example questions">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => {
                  setQuestion(ex);
                  void ask(ex);
                }}
              >
                {ex}
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="finding" aria-live="polite" aria-busy={busy}>
        {!asked && <EmptyState />}
        {asked && (
          <p className="status">
            {busy ? statusLine(trace) : trace.done ? `Asked: ${asked}` : null}
          </p>
        )}
        {failure && (
          <p className="alert" role="alert">
            <strong>Error</strong>
            {failure}
          </p>
        )}
        {trace.error && (
          <p className="alert" role="alert">
            <strong>No answer</strong>
            {trace.error.message} {RECOVERY[trace.error.stage] ?? ""}
          </p>
        )}
        {trace.done && trace.answer && (
          <>
            <h1 className="takeaway">{headline ?? trace.answer.text}</h1>
            {/* A model-worded answer (results sharing on) adds to the computed headline. */}
            {headline && trace.answer.provenance.results_shared_with_model && (
              <p className="answer-text">{trace.answer.text}</p>
            )}
            <ConfidenceNote confidence={trace.answer.confidence} />
          </>
        )}
        {spec && trace.result && trace.done && <ResultChart spec={spec} />}
        {trace.result && trace.done && <ResultTable result={trace.result} />}
      </section>

      {asked && (
        <aside className="rail" aria-label="How this answer was made">
          <TraceRail trace={trace} busy={busy} />
          {trace.answer && <Provenance provenance={trace.answer.provenance} />}
        </aside>
      )}
    </main>
  );
}

function statusLine(t: Trace): string {
  if (!t.plan) return "Reading the schema and planning…";
  const last = t.attempts[t.attempts.length - 1];
  if (!last) return "Writing SQL…";
  if (last.status !== "ran") return `Attempt ${last.n} was rejected; writing another…`;
  if (!last.critic) return `Attempt ${last.n} ran; reviewing it against the question…`;
  return last.critic.ok === false ? "The reviewer objected; writing another attempt…" : "Writing the answer…";
}

function EmptyState() {
  return (
    <div className="empty">
      <h1>Ask in plain English.</h1>
      <p>
        The analyst plans a query, writes SQL, checks it against a safety guard, runs it on the
        data you picked and reviews it against your question. Every step shows up on the right as it
        happens. The model sees table and column names only, never your rows.
      </p>
    </div>
  );
}
