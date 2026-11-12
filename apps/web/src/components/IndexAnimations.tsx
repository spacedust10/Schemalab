"use client";

import { useEffect, useState } from "react";
import { type BStep, type HStep, KEY_W, bplusInsertSteps, bplusSearchSteps, btreeLayout, ehInsertSteps } from "@/lib/indexes";

type Kind = "btree" | "hash";
const parseKeys = (text: string) => text.split(/[\s,]+/).map(Number).filter((n) => Number.isInteger(n) && n >= 0 && n < 10_000).slice(0, 40);

export function IndexAnimations() {
  const [kind, setKind] = useState<Kind>("btree");
  const [keys, setKeys] = useState("10 20 5 6 12 30 7 17 3 25 28 1");
  const [find, setFind] = useState("17");
  const [steps, setSteps] = useState<(BStep | HStep)[]>(() => bplusInsertSteps(parseKeys("10 20 5 6 12 30 7 17 3 25 28 1"), 3));
  const [at, setAt] = useState(0);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => setAt((i) => (i + 1 < steps.length ? i + 1 : (setPlaying(false), i))), 900);
    return () => clearInterval(timer);
  }, [playing, steps.length]);

  function build(next: Kind = kind) {
    setPlaying(false);
    setAt(0);
    setSteps(next === "btree" ? bplusInsertSteps(parseKeys(keys), 3) : ehInsertSteps(parseKeys(keys), 2));
  }

  function search() {
    const last = steps[steps.length - 1];
    if (!("tree" in last)) return;
    const searchSteps = bplusSearchSteps(last.tree, Number(find));
    setPlaying(false);
    setSteps([...steps, ...searchSteps]);
    setAt(steps.length);
  }

  const step = steps[Math.min(at, steps.length - 1)];
  return (
    <section className="indexes" aria-labelledby="how-indexes">
      <h2 id="how-indexes">How indexes work</h2>
      <fieldset className="source">
        <legend>Structure</legend>
        {(["btree", "hash"] as const).map((k) => (
          <label key={k}>
            <input type="radio" name="index-kind" checked={kind === k} onChange={() => { setKind(k); build(k); }} />
            {k === "btree" ? "B+ tree (3 keys per node)" : "Extendible hashing (2 keys per bucket)"}
          </label>
        ))}
      </fieldset>
      <div className="er-bar">
        <div className="field">
          <label htmlFor="index-keys">Keys to insert</label>
          <input id="index-keys" className="mono" value={keys} onChange={(e) => setKeys(e.target.value)} />
        </div>
        <button type="button" className="button quiet" onClick={() => build()}>Build</button>
        {kind === "btree" && (
          <>
            <div className="field">
              <label htmlFor="index-find">Search for</label>
              <input id="index-find" className="mono" value={find} onChange={(e) => setFind(e.target.value)} size={6} />
            </div>
            <button type="button" className="button quiet" onClick={search}>Search</button>
          </>
        )}
      </div>
      <div className="er-bar step-controls">
        <button type="button" className="button quiet" disabled={at === 0} onClick={() => { setPlaying(false); setAt((i) => Math.max(0, i - 1)); }}>Back</button>
        <button type="button" className="button quiet" onClick={() => setPlaying(!playing)} disabled={at >= steps.length - 1 && !playing}>{playing ? "Pause" : "Play"}</button>
        <button type="button" className="button quiet" disabled={at >= steps.length - 1} onClick={() => { setPlaying(false); setAt((i) => Math.min(steps.length - 1, i + 1)); }}>Next</button>
        <span className="hint">Step {Math.min(at, steps.length - 1) + 1} of {steps.length}</span>
      </div>
      <p className="step-note" aria-live="polite">{step?.note}</p>
      <div className="diagram">{step && ("tree" in step ? <TreeView step={step} /> : <HashView step={step} />)}</div>
    </section>
  );
}

function TreeView({ step }: { step: BStep }) {
  const l = btreeLayout(step.tree);
  const at = new Map(l.nodes.map((n) => [n.id, n]));
  const hot = new Set(step.highlight);
  return (
    <svg width={l.width} height={l.height} viewBox={`0 0 ${l.width} ${l.height}`} role="img" aria-label={`B+ tree with ${l.nodes.length} nodes`} className="er">
      <g className="er-lines">
        {l.edges.map(([p, c]) => {
          const a = at.get(p)!;
          const b = at.get(c)!;
          return <line key={`${p}-${c}`} x1={a.x + a.w / 2} y1={a.y + 30} x2={b.x + b.w / 2} y2={b.y} />;
        })}
        {l.nodes.filter((n) => n.leaf && step.tree.nodes[n.id].next !== null).map((n) => {
          const b = at.get(step.tree.nodes[n.id].next!)!;
          return <line key={`next-${n.id}`} x1={n.x + n.w} y1={n.y + 15} x2={b.x} y2={b.y + 15} strokeDasharray="4 3" />;
        })}
      </g>
      {l.nodes.map((n) => (
        <g key={n.id}>
          <rect x={n.x} y={n.y} width={n.w} height={30} rx={3} className={hot.has(n.id) ? "er-box idx-hot" : "er-box"} />
          {n.keys.map((k, i) => (
            <text key={i} x={n.x + 4 + i * KEY_W + KEY_W / 2} y={n.y + 20} textAnchor="middle" className="er-attr mono">{k}</text>
          ))}
        </g>
      ))}
    </svg>
  );
}

function HashView({ step }: { step: HStep }) {
  const h = step.hashing;
  const rowH = 30;
  const ids = [...new Set(h.directory)];
  const hot = new Set(step.highlight);
  const height = Math.max(h.directory.length, ids.length) * (rowH + 6) + 40;
  const bucketY = (id: number) => 30 + ids.indexOf(id) * (rowH + 6);
  return (
    <svg width={460} height={height} viewBox={`0 0 460 ${height}`} role="img" aria-label={`Directory of ${h.directory.length} entries and ${ids.length} buckets`} className="er">
      <text x={10} y={18} className="er-attr">global depth {h.globalDepth}</text>
      <g className="er-lines">
        {h.directory.map((id, i) => <line key={i} x1={110} y1={30 + i * (rowH + 6) + rowH / 2} x2={250} y2={bucketY(id) + rowH / 2} />)}
      </g>
      {h.directory.map((_, i) => (
        <g key={i}>
          <rect x={30} y={30 + i * (rowH + 6)} width={80} height={rowH} className="er-oval" />
          <text x={70} y={30 + i * (rowH + 6) + 20} textAnchor="middle" className="er-attr mono">{i.toString(2).padStart(h.globalDepth, "0")}</text>
        </g>
      ))}
      {ids.map((id) => (
        <g key={id}>
          <rect x={250} y={bucketY(id)} width={180} height={rowH} className={hot.has(id) ? "er-box idx-hot" : "er-box"} />
          <text x={260} y={bucketY(id) + 20} className="er-attr mono">{h.buckets[id].keys.join(", ") || "empty"}</text>
          <text x={422} y={bucketY(id) + 20} textAnchor="end" className="er-type">d={h.buckets[id].localDepth}</text>
        </g>
      ))}
    </svg>
  );
}
