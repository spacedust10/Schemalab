import { type ReactNode } from "react";
import {
  type Box,
  type CrowEnd,
  type ERModel,
  type Point,
  HEADER,
  ROW,
  chenLayout,
  crowEnds,
  crowLayout,
  exitPoint,
} from "@/lib/er";

export type Notation = "chen" | "crow";

export function ERDiagram({ model, notation }: { model: ERModel; notation: Notation }) {
  const summary = `${model.entities.length} entities and ${model.relationships.length} relationships`;
  return (
    <figure className="figure er-figure">
      <div className="diagram">{notation === "chen" ? <Chen model={model} label={summary} /> : <Crow model={model} label={summary} />}</div>
      <figcaption>
        {notation === "chen"
          ? "Chen notation: rectangles are entities, diamonds relationships, ovals attributes (keys underlined). 1, N and M count the entity beside them; a double line means every row takes part."
          : "Crow's foot: the mark nearest an entity is the most of it (bar one, foot many), the next is the least (circle zero, bar one)."}
      </figcaption>
    </figure>
  );
}

const unit = (from: Point, to: Point): Point => {
  const len = Math.hypot(to.x - from.x, to.y - from.y) || 1;
  return { x: (to.x - from.x) / len, y: (to.y - from.y) / len };
};
const at = (p: Point, u: Point, d: number, v?: Point, s = 0): Point => ({
  x: p.x + u.x * d + (v ? v.x * s : 0),
  y: p.y + u.y * d + (v ? v.y * s : 0),
});
const seg = (a: Point, b: Point, key: string) => <line key={key} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />;

/** The two crow's-foot marks at point p on a box edge, pointing along u away from the box. */
function marks(p: Point, u: Point, end: CrowEnd, key: string): ReactNode[] {
  const v = { x: -u.y, y: u.x };
  const out: ReactNode[] = [];
  if (end.max === "many") {
    const tip = at(p, u, 14);
    out.push(seg(tip, at(p, u, 0, v, 9), `${key}a`), seg(tip, p, `${key}b`), seg(tip, at(p, u, 0, v, -9), `${key}c`));
  } else {
    out.push(seg(at(p, u, 9, v, 8), at(p, u, 9, v, -8), `${key}a`));
  }
  if (end.min === "one") out.push(seg(at(p, u, 20, v, 8), at(p, u, 20, v, -8), `${key}d`));
  else {
    const c = at(p, u, 25);
    out.push(<circle key={`${key}d`} cx={c.x} cy={c.y} r={5} className="er-hollow" />);
  }
  return out;
}

function Crow({ model, label }: { model: ERModel; label: string }) {
  const l = crowLayout(model);
  const center = (b: Box): Point => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });
  return (
    <svg width={l.width + 80} height={l.height + 60} viewBox={`0 0 ${l.width + 80} ${l.height + 60}`} role="img" aria-label={`Crow's-foot diagram: ${label}`} className="er">
      <g className="er-lines">
        {model.relationships.map((r) => {
          const a = l.boxes[r.left.entity];
          const b = l.boxes[r.right.entity];
          const ends = crowEnds(r);
          const name = r.attributes.length ? `${r.name} (${r.attributes.map((x) => x.name).join(", ")})` : r.name;
          if (r.left.entity === r.right.entity) {
            const s = { x: a.x + a.w, y: a.y + a.h * 0.3 };
            const e = { x: a.x + a.w * 0.75, y: a.y };
            const path = `M${s.x},${s.y} H${s.x + 46} V${a.y - 46} H${e.x} V${e.y}`;
            return (
              <g key={r.name}>
                <path d={path} fill="none" />
                {marks(s, { x: 1, y: 0 }, ends.left, `${r.name}l`)}
                {marks(e, { x: 0, y: -1 }, ends.right, `${r.name}r`)}
                <text x={s.x + 52} y={a.y - 52} className="er-label">{name}</text>
              </g>
            );
          }
          const pa = exitPoint(a, center(b));
          const pb = exitPoint(b, center(a));
          const u = unit(pa, pb);
          const mid = { x: (pa.x + pb.x) / 2, y: (pa.y + pb.y) / 2 };
          return (
            <g key={r.name}>
              {seg(pa, pb, "line")}
              {marks(pa, u, ends.left, `${r.name}l`)}
              {marks(pb, { x: -u.x, y: -u.y }, ends.right, `${r.name}r`)}
              <text x={mid.x} y={mid.y - 6} textAnchor="middle" className="er-label">{name}</text>
            </g>
          );
        })}
      </g>
      {model.entities.map((e) => {
        const b = l.boxes[e.name];
        return (
          <g key={e.name}>
            <rect x={b.x} y={b.y} width={b.w} height={b.h} rx={4} className="er-box" />
            <text x={b.x + 12} y={b.y + 21} className="er-title">{e.name}</text>
            <line x1={b.x} y1={b.y + HEADER} x2={b.x + b.w} y2={b.y + HEADER} className="er-rule" />
            {e.attributes.map((a, i) => (
              <g key={a.name}>
                <text x={b.x + 12} y={b.y + HEADER + 16 + i * ROW} className={a.key ? "er-attr er-key" : "er-attr"}>
                  {a.key ? "PK " : ""}
                  {a.name}
                </text>
                <text x={b.x + b.w - 12} y={b.y + HEADER + 16 + i * ROW} textAnchor="end" className="er-type">
                  {a.type}
                </text>
              </g>
            ))}
          </g>
        );
      })}
    </svg>
  );
}

const EW = 124;
const EH = 44;

function Chen({ model, label }: { model: ERModel; label: string }) {
  const l = chenLayout(model);
  const lines: ReactNode[] = [];
  const labels: ReactNode[] = [];
  const connect = (from: Point, to: Point, total: boolean, card: string, key: string) => {
    const u = unit(from, to);
    const v = { x: -u.y, y: u.x };
    if (total) lines.push(seg(at(from, u, 0, v, 2.5), at(to, u, 0, v, 2.5), `${key}1`), seg(at(from, u, 0, v, -2.5), at(to, u, 0, v, -2.5), `${key}2`));
    else lines.push(seg(from, to, key));
    const p = at(from, u, 78, v, 12);
    labels.push(<text key={`${key}c`} x={p.x} y={p.y + 4} textAnchor="middle" className="er-card">{card}</text>);
  };
  for (const r of model.relationships) {
    const d = l.diamonds[r.name];
    const many = r.kind === "M:N";
    const left = l.entities[r.left.entity];
    const right = l.entities[r.right.entity];
    if (r.left.entity === r.right.entity) {
      connect({ x: left.x + EW / 2 - 10, y: left.y }, d, r.left.total, r.left.card, `${r.name}l`);
      connect({ x: left.x, y: left.y - EH / 2 }, d, r.right.total, r.right.card, `${r.name}r`);
    } else {
      connect(left, d, r.left.total, many ? "M" : r.left.card, `${r.name}l`);
      connect(right, d, r.right.total, r.right.card, `${r.name}r`);
    }
  }
  const owners = [...model.entities.map((e) => [e.name, l.entities[e.name]] as const), ...model.relationships.map((r) => [r.name, l.diamonds[r.name]] as const)];
  for (const [name, c] of owners) for (const a of l.attributes[name] ?? []) lines.push(seg(c, a, `${name}.${a.name}`));

  return (
    <svg width={l.width} height={l.height} viewBox={`0 0 ${l.width} ${l.height}`} role="img" aria-label={`Chen diagram: ${label}`} className="er">
      <g className="er-lines">{lines}</g>
      {owners.flatMap(([name]) =>
        (l.attributes[name] ?? []).map((a) => (
          <g key={`${name}.${a.name}`}>
            <ellipse cx={a.x} cy={a.y} rx={a.rx} ry={16} className="er-oval" />
            <text x={a.x} y={a.y + 4} textAnchor="middle" className="er-attr" textDecoration={a.key ? "underline" : undefined}>
              {a.name}
            </text>
          </g>
        )),
      )}
      {model.relationships.map((r) => {
        const d = l.diamonds[r.name];
        const w = Math.max(96, r.name.length * 8 + 36) / 2;
        return (
          <g key={r.name}>
            <polygon points={`${d.x - w},${d.y} ${d.x},${d.y - 28} ${d.x + w},${d.y} ${d.x},${d.y + 28}`} className="er-diamond" />
            <text x={d.x} y={d.y + 4} textAnchor="middle" className="er-attr">{r.name}</text>
          </g>
        );
      })}
      {model.entities.map((e) => {
        const c = l.entities[e.name];
        return (
          <g key={e.name}>
            <rect x={c.x - EW / 2} y={c.y - EH / 2} width={EW} height={EH} className="er-box" />
            <text x={c.x} y={c.y + 5} textAnchor="middle" className="er-title">{e.name}</text>
          </g>
        );
      })}
      {labels}
    </svg>
  );
}
