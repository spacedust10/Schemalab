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
  crowLabels,
  crowLayout,
  exitPoint,
  inheritsFrom,
  relationshipText,
} from "@/lib/er";

export type Notation = "chen" | "crow";

export function ERDiagram({ model, notation }: { model: ERModel; notation: Notation }) {
  const specs = model.specializations?.length ?? 0;
  const summary = `${model.entities.length} entities, ${model.relationships.length} relationships${specs ? ` and ${specs} specialization${specs > 1 ? "s" : ""}` : ""}`;
  return (
    <figure className="figure er-figure">
      <div className="diagram">{notation === "chen" ? <Chen model={model} label={summary} /> : <Crow model={model} label={summary} />}</div>
      <figcaption>
        {notation === "chen"
          ? "Chen notation, with Elmasri and Navathe's extensions: rectangles are entities, diamonds relationships, ovals attributes (keys underlined). A double rectangle is a weak entity, its partial key underlined with dashes, tied to its owner by a double diamond (an identifying relationship). 1, N and M count the entity beside them; a double line means every one takes part. A circle joins a supertype to its subtypes: d for disjoint, o for overlapping, a double line to it when every supertype entity is in a subtype, and ⊂ on each subtype's line."
          : "Crow's foot: the mark nearest an entity is the most of it (bar one, foot many), the next is the least (circle zero, bar one). A solid line is an identifying relationship and a dashed line a non-identifying one; rounded boxes depend on another entity for their key. A circle joins a supertype to its subtypes: d for disjoint, o for overlapping, with two bars under it when every supertype entity is in a subtype, one when not."}
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
const seg = (a: Point, b: Point, key: string, className?: string) => <line key={key} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={className} />;
const width = (text: string) => text.length * 7.4;

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
  const labels = crowLabels(model, l);
  const center = (b: Box): Point => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });
  return (
    <svg width={l.width + 80} height={l.height + 60} viewBox={`0 0 ${l.width + 80} ${l.height + 60}`} role="img" aria-label={`Crow's-foot diagram: ${label}`} className="er">
      <g className="er-lines">
        {model.relationships.map((r) => {
          const a = l.boxes[r.left.entity];
          const b = l.boxes[r.right.entity];
          const ends = crowEnds(r);
          const style = r.identifying ? undefined : "er-dashed";
          if (r.left.entity === r.right.entity) {
            const s = { x: a.x + a.w, y: a.y + a.h * 0.3 };
            const e = { x: a.x + a.w * 0.75, y: a.y };
            const path = `M${s.x},${s.y} H${s.x + 46} V${a.y - 46} H${e.x} V${e.y}`;
            return (
              <g key={r.name}>
                <path d={path} className={`er-none ${style ?? ""}`} />
                {marks(s, { x: 1, y: 0 }, ends.left, `${r.name}l`)}
                {marks(e, { x: 0, y: -1 }, ends.right, `${r.name}r`)}
                <text x={s.x + 52} y={a.y - 52} className="er-label">{relationshipText(r)}</text>
              </g>
            );
          }
          const pa = exitPoint(a, center(b));
          const pb = exitPoint(b, center(a));
          const u = unit(pa, pb);
          const t = labels[r.name];
          return (
            <g key={r.name}>
              {seg(at(pa, u, 14), at(pb, u, -14), "line", style)}
              {seg(pa, at(pa, u, 14), "a")}
              {seg(at(pb, u, -14), pb, "b")}
              {marks(pa, u, ends.left, `${r.name}l`)}
              {marks(pb, { x: -u.x, y: -u.y }, ends.right, `${r.name}r`)}
              <text x={t.x + t.w / 2} y={t.y + t.h / 2 + 4} textAnchor="middle" className="er-label">{relationshipText(r)}</text>
            </g>
          );
        })}
        {(model.specializations ?? []).map((sp) => {
          const p = l.boxes[sp.parent];
          const top = { x: p.x + p.w / 2, y: p.y + p.h };
          const kids = sp.children.map((c) => l.boxes[c]);
          const c = { x: top.x, y: top.y + (Math.min(...kids.map((k) => k.y)) - top.y) / 2 - 8 };
          const bars = sp.total ? [16, 21] : [16];
          const below = c.y + bars[bars.length - 1];
          return (
            <g key={`isa-${sp.parent}`}>
              {seg(top, { x: c.x, y: c.y - 11 }, "up")}
              <circle cx={c.x} cy={c.y} r={11} className="er-hollow" />
              <text x={c.x} y={c.y + 4} textAnchor="middle" className="er-card">{sp.disjoint ? "d" : "o"}</text>
              {bars.map((d) => seg({ x: c.x - 14, y: c.y + d }, { x: c.x + 14, y: c.y + d }, `bar${d}`))}
              {sp.discriminator && <text x={c.x + 20} y={c.y + 4} className="er-label">{sp.discriminator}</text>}
              {kids.map((k, i) => seg({ x: c.x, y: below }, { x: k.x + k.w / 2, y: k.y }, `kid${i}`))}
            </g>
          );
        })}
      </g>
      {model.entities.map((e) => {
        const b = l.boxes[e.name];
        const from = inheritsFrom(model, e.name);
        return (
          <g key={e.name}>
            <rect x={b.x} y={b.y} width={b.w} height={b.h} rx={from ? 12 : 0} className="er-box" />
            <text x={b.x + 12} y={b.y + 21} className="er-title">{e.name}</text>
            <line x1={b.x} y1={b.y + HEADER} x2={b.x + b.w} y2={b.y + HEADER} className="er-rule" />
            {from && (
              <text x={b.x + 12} y={b.y + HEADER + 16} className="er-inherit">+ key of {from}</text>
            )}
            {e.attributes.map((a, i) => {
              const y = b.y + HEADER + 16 + (i + (from ? 1 : 0)) * ROW;
              return (
                <g key={a.name}>
                  <text x={b.x + 12} y={y} className={a.key ? "er-attr er-key" : "er-attr"}>
                    {a.key ? "PK " : ""}
                    {a.name}
                  </text>
                  <text x={b.x + b.w - 12} y={y} textAnchor="end" className="er-type">
                    {a.type}
                  </text>
                </g>
              );
            })}
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
  const weak = new Set(model.entities.filter((e) => e.weak).map((e) => e.name));
  const double = (from: Point, to: Point, key: string) => {
    const v = { x: -unit(from, to).y, y: unit(from, to).x };
    lines.push(seg(at(from, v, 0, v, 2.5), at(to, v, 0, v, 2.5), `${key}1`), seg(at(from, v, 0, v, -2.5), at(to, v, 0, v, -2.5), `${key}2`));
  };
  const connect = (from: Point, to: Point, total: boolean, card: string, key: string) => {
    const u = unit(from, to);
    const v = { x: -u.y, y: u.x };
    if (total) double(from, to, key);
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
  for (const sp of model.specializations ?? []) {
    const c = l.isa[sp.parent];
    const p = l.entities[sp.parent];
    if (sp.total) double(p, c, `isa-${sp.parent}`);
    else lines.push(seg(p, c, `isa-${sp.parent}`));
    if (sp.discriminator) {
      const m = { x: (p.x + c.x) / 2, y: (p.y + c.y) / 2 };
      labels.push(<text key={`disc-${sp.parent}`} x={m.x + 10} y={m.y + 4} className="er-label">{sp.discriminator}</text>);
    }
    for (const child of sp.children) {
      const k = l.entities[child];
      lines.push(seg(c, k, `isa-${sp.parent}-${child}`));
      // The subset mark: a ⊂ across the line, open toward the supertype.
      const u = unit(c, k);
      const v = { x: -u.y, y: u.x };
      const mid = { x: (c.x + k.x) / 2, y: (c.y + k.y) / 2 };
      const a = at(mid, u, -7, v, 11);
      const b = at(mid, u, -7, v, -11);
      const bend = at(mid, u, 15);
      lines.push(<path key={`sub-${child}`} d={`M${a.x},${a.y} Q${bend.x},${bend.y} ${b.x},${b.y}`} className="er-none" />);
    }
  }
  const owners = [...model.entities.map((e) => [e.name, l.entities[e.name]] as const), ...model.relationships.map((r) => [r.name, l.diamonds[r.name]] as const)];
  for (const [name, c] of owners) for (const a of l.attributes[name] ?? []) lines.push(seg(c, a, `${name}.${a.name}`));

  return (
    <svg width={l.width} height={l.height} viewBox={`0 0 ${l.width} ${l.height}`} role="img" aria-label={`Chen diagram: ${label}`} className="er">
      <g className="er-lines">{lines}</g>
      {owners.flatMap(([name]) =>
        (l.attributes[name] ?? []).map((a) => {
          const partial = a.key && weak.has(name);
          return (
            <g key={`${name}.${a.name}`}>
              <ellipse cx={a.x} cy={a.y} rx={a.rx} ry={16} className="er-oval" />
              <text x={a.x} y={a.y + 4} textAnchor="middle" className="er-attr" textDecoration={a.key && !partial ? "underline" : undefined}>
                {a.name}
              </text>
              {partial && (
                <line x1={a.x - width(a.name) / 2} y1={a.y + 7} x2={a.x + width(a.name) / 2} y2={a.y + 7} className="er-rule-ink" strokeDasharray="4 3" />
              )}
            </g>
          );
        }),
      )}
      {(model.specializations ?? []).map((sp) => {
        const c = l.isa[sp.parent];
        return (
          <g key={`isa-${sp.parent}`}>
            <circle cx={c.x} cy={c.y} r={16} className="er-diamond" />
            <text x={c.x} y={c.y + 5} textAnchor="middle" className="er-title">{sp.disjoint ? "d" : "o"}</text>
          </g>
        );
      })}
      {model.relationships.map((r) => {
        const d = l.diamonds[r.name];
        const w = Math.max(96, r.name.length * 8 + 36) / 2;
        const diamond = (dw: number, dh: number) => `${d.x - dw},${d.y} ${d.x},${d.y - dh} ${d.x + dw},${d.y} ${d.x},${d.y + dh}`;
        return (
          <g key={r.name}>
            <polygon points={diamond(w, 28)} className="er-diamond" />
            {r.identifying && <polygon points={diamond(w - 9, 23)} className="er-diamond" />}
            <text x={d.x} y={d.y + 4} textAnchor="middle" className="er-attr">{r.name}</text>
          </g>
        );
      })}
      {model.entities.map((e) => {
        const c = l.entities[e.name];
        const w = Math.max(EW, width(e.name) + 28);
        return (
          <g key={e.name}>
            <rect x={c.x - w / 2} y={c.y - EH / 2} width={w} height={EH} className="er-box" />
            {e.weak && <rect x={c.x - w / 2 + 4} y={c.y - EH / 2 + 4} width={w - 8} height={EH - 8} className="er-box" />}
            <text x={c.x} y={c.y + 5} textAnchor="middle" className="er-title">{e.name}</text>
          </g>
        );
      })}
      {labels}
    </svg>
  );
}
