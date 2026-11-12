import { type GraphEdge, graphLayout } from "@/lib/theory";

/** Transactions as circles, conflicts as arrows Ti → Tj; edges on the cycle are marked. */
export function PrecedenceGraph({ txns, edges, cycle }: { txns: number[]; edges: GraphEdge[]; cycle: number[] | null }) {
  const l = graphLayout(txns, edges);
  const onCycle = (e: GraphEdge) =>
    !!cycle && cycle.some((t, i) => i < cycle.length - 1 && t === e.from && cycle[i + 1] === e.to);
  return (
    <figure className="figure">
      <div className="diagram graph-box">
        <svg width={l.width} height={l.height} viewBox={`0 0 ${l.width} ${l.height}`} role="img" aria-label={`Precedence graph: ${edges.map((e) => `T${e.from} to T${e.to}`).join(", ") || "no edges"}`} className="er">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" className="graph-head" />
            </marker>
            <marker id="arrow-cycle" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" className="graph-head graph-cycle" />
            </marker>
          </defs>
          {l.edges.map((e) => {
            const mx = (e.x1 + e.x2) / 2;
            const my = (e.y1 + e.y2) / 2;
            const len = Math.hypot(e.x2 - e.x1, e.y2 - e.y1) || 1;
            const nx = -(e.y2 - e.y1) / len;
            const ny = (e.x2 - e.x1) / len;
            const cx = mx + nx * e.bend;
            const cy = my + ny * e.bend;
            const hot = onCycle(e);
            return (
              <g key={`${e.from}-${e.to}`}>
                <path d={`M${e.x1},${e.y1} Q${cx},${cy} ${e.x2},${e.y2}`} fill="none" className={hot ? "graph-edge graph-cycle" : "graph-edge"} markerEnd={`url(#${hot ? "arrow-cycle" : "arrow"})`} />
                <text x={cx + nx * 12} y={cy + ny * 12 + 4} textAnchor="middle" className="er-label">
                  {e.items.join(", ")}
                </text>
              </g>
            );
          })}
          {l.nodes.map((n) => (
            <g key={n.txn}>
              <circle cx={n.x} cy={n.y} r={l.r} className="er-box" />
              <text x={n.x} y={n.y + 5} textAnchor="middle" className="er-title">
                T{n.txn}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <figcaption>
        An arrow Ti → Tj means an operation of Ti conflicts with a later one of Tj on the item shown, so Ti must come first.
        {cycle && " The highlighted arrows form a cycle: no serial order can satisfy them."}
      </figcaption>
    </figure>
  );
}
