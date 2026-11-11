import { type TreeNode, treeLayout } from "@/lib/algebra";

/** The operator tree, root at the top and relations at the leaves, as textbooks draw it. */
export function QueryTree({ tree }: { tree: TreeNode }) {
  const l = treeLayout(tree);
  return (
    <figure className="figure">
      <div className="diagram">
        <svg width={l.width} height={l.height} viewBox={`0 0 ${l.width} ${l.height}`} role="img" aria-label={`Query tree with ${l.nodes.length} nodes`} className="er">
          <g className="er-lines">
            {l.edges.map((e, i) => (
              <line key={i} x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} />
            ))}
          </g>
          {l.nodes.map((n, i) => (
            <g key={i}>
              <rect x={n.x - n.w / 2} y={n.y - n.h / 2} width={n.w} height={n.h} rx={n.op === "relation" ? 2 : 16} className={n.op === "relation" ? "er-box" : "er-oval"} />
              <text x={n.x} y={n.y + 4} textAnchor="middle" className={n.op === "relation" ? "er-title" : "er-attr"}>
                {n.label}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <figcaption>Read from the leaves up: each operator works on the result below it. Relations are the square boxes.</figcaption>
    </figure>
  );
}
