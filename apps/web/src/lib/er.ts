/** ER models from /er/* and the geometry for drawing them in Chen and crow's-foot notation. */

export type ERAttribute = { name: string; type: string; key: boolean };
/** weak: identified through its owner; its key attributes are the partial key. */
export type EREntity = { name: string; attributes: ERAttribute[]; weak?: boolean };
export type EREnd = { entity: string; card: "1" | "N"; total: boolean };
export type ERRelationship = {
  name: string;
  kind: string;
  left: EREnd;
  right: EREnd;
  attributes: ERAttribute[];
  identifying?: boolean;
};
/** A supertype and its subtypes (ISA). */
export type ERSpecialization = {
  parent: string;
  children: string[];
  disjoint: boolean;
  total: boolean;
  discriminator: string | null;
};
export type ERModel = { entities: EREntity[]; relationships: ERRelationship[]; specializations?: ERSpecialization[] };

/** The entity a weak entity or subtype takes its key from, or null for a strong entity. */
export function inheritsFrom(m: ERModel, name: string): string | null {
  const sp = m.specializations?.find((s) => s.children.includes(name));
  if (sp) return sp.parent;
  const weak = m.entities.find((e) => e.name === name)?.weak;
  if (!weak) return null;
  const r = m.relationships.find((x) => x.identifying && (x.left.entity === name || x.right.entity === name));
  if (!r) return null;
  return r.left.entity === name ? r.right.entity : r.left.entity;
}

/**
 * A row for each entity, top to bottom: owners above weak entities, supertypes above subtypes,
 * the one side of 1:N above the many side, and otherwise the left entity above the right.
 * Subtypes of one supertype share a row.
 */
export function layers(m: ERModel): Record<string, number> {
  const specs = m.specializations ?? [];
  const sibling = (a: string, b: string) => specs.some((s) => s.children.includes(a) && s.children.includes(b));
  const edges: [string, string][] = [];
  for (const r of m.relationships) {
    const [a, b] = [r.left.entity, r.right.entity];
    if (a === b || sibling(a, b)) continue;
    if (r.identifying) edges.push(m.entities.find((e) => e.name === b)?.weak ? [a, b] : [b, a]);
    else if (r.left.card === "N" && r.right.card === "1") edges.push([b, a]);
    else edges.push([a, b]);
  }
  for (const s of specs) for (const c of s.children) edges.push([s.parent, c]);
  const n = m.entities.length;
  const layer: Record<string, number> = Object.fromEntries(m.entities.map((e) => [e.name, 0]));
  for (let pass = 0; pass <= n; pass++) {
    let changed = false;
    for (const [a, b] of edges) {
      // Capped at n - 1, so a cycle of relationships cannot push rows down forever.
      if (layer[b] < layer[a] + 1 && layer[a] + 1 < n) {
        layer[b] = layer[a] + 1;
        changed = true;
      }
    }
    for (const s of specs) {
      const top = Math.max(...s.children.map((c) => layer[c]));
      for (const c of s.children) if (layer[c] !== top) [layer[c], changed] = [top, true];
    }
    if (!changed) break;
  }
  const used = [...new Set(Object.values(layer))].sort((a, b) => a - b);
  return Object.fromEntries(Object.entries(layer).map(([k, v]) => [k, used.indexOf(v)]));
}

/** Entities row by row, each row ordered by where its neighbors above sit. */
export function rows(m: ERModel): string[][] {
  const layer = layers(m);
  const base = entityOrder(m);
  const out: string[][] = [];
  for (const name of base) (out[layer[name]] ??= []).push(name);
  const neighbors = (n: string) => [
    ...m.relationships.flatMap((r) => (r.left.entity === n ? [r.right.entity] : r.right.entity === n ? [r.left.entity] : [])),
    ...(m.specializations ?? []).flatMap((s) => (s.children.includes(n) ? [s.parent] : s.parent === n ? s.children : [])),
  ];
  for (let i = 1; i < out.length; i++) {
    const pos = (name: string) => {
      const above = neighbors(name).filter((x) => layer[x] < i);
      if (!above.length) return 1e6 + base.indexOf(name);
      return above.reduce((sum, x) => sum + out[layer[x]].indexOf(x) / Math.max(1, out[layer[x]].length - 1), 0) / above.length;
    };
    out[i] = [...(out[i] ?? [])].sort((a, b) => pos(a) - pos(b) || base.indexOf(a) - base.indexOf(b));
  }
  // A long row wraps onto rows of its own, so a big schema grows down, not off to the side.
  return out.filter(Boolean).flatMap((r) => Array.from({ length: Math.ceil(r.length / MAX_PER_ROW) }, (_, k) => r.slice(k * MAX_PER_ROW, (k + 1) * MAX_PER_ROW)));
}

const MAX_PER_ROW = 6;

export type Mark = "zero" | "one" | "many";
export type CrowEnd = { max: Mark; min: Mark };

/**
 * Crow's-foot marks at each end. The mark at entity E's end says how many E relate to one
 * of the other side: its maximum is E's own count, and its minimum is one when every row of
 * the other side must take part (the other side's total participation).
 */
export function crowEnds(r: ERRelationship): { left: CrowEnd; right: CrowEnd } {
  const end = (own: EREnd, other: EREnd): CrowEnd => ({
    max: own.card === "1" ? "one" : "many",
    min: other.total ? "one" : "zero",
  });
  return { left: end(r.left, r.right), right: end(r.right, r.left) };
}

/** Breadth-first from the most connected entity, so related entities land near each other. */
export function entityOrder(m: ERModel): string[] {
  const degree = (n: string) =>
    m.relationships.filter((r) => r.left.entity === n || r.right.entity === n).length;
  const names = m.entities.map((e) => e.name);
  const order: string[] = [];
  const queue: string[] = [];
  for (const start of [...names].sort((a, b) => degree(b) - degree(a) || names.indexOf(a) - names.indexOf(b))) {
    if (order.includes(start)) continue;
    queue.push(start);
    order.push(start);
    while (queue.length) {
      const n = queue.shift() as string;
      for (const r of m.relationships) {
        for (const [a, b] of [[r.left.entity, r.right.entity], [r.right.entity, r.left.entity]]) {
          if (a === n && !order.includes(b)) {
            order.push(b);
            queue.push(b);
          }
        }
      }
    }
  }
  return order;
}

const PAD = 32;
const CHAR = 7.4; // average glyph width at 13px

export type Box = { x: number; y: number; w: number; h: number };
export const ROW = 22;
export const HEADER = 32;

const GAP_X = 130;
const GAP_Y = 120;

export function crowLayout(m: ERModel): { boxes: Record<string, Box>; width: number; height: number } {
  const size = (e: EREntity) => {
    const from = inheritsFrom(m, e.name);
    const lines = e.attributes.length + (from ? 1 : 0);
    const widest = Math.max(e.name.length, from ? `+ key of ${from}`.length : 0, ...e.attributes.map((a) => a.name.length + a.type.length + 5));
    return { w: Math.max(150, 40 + CHAR * widest), h: HEADER + ROW * Math.max(1, lines) + 8 };
  };
  const sizes = Object.fromEntries(m.entities.map((e) => [e.name, size(e)]));
  const grid = rows(m);
  const rowWidth = (r: string[]) => r.reduce((sum, n) => sum + sizes[n].w, 0) + GAP_X * (r.length - 1);
  const widest = Math.max(...grid.map(rowWidth));
  const boxes: Record<string, Box> = {};
  let y = PAD;
  for (const r of grid) {
    let x = PAD + (widest - rowWidth(r)) / 2;
    for (const name of r) {
      boxes[name] = { x, y, ...sizes[name] };
      x += sizes[name].w + GAP_X;
    }
    y += Math.max(...r.map((n) => sizes[n].h)) + GAP_Y;
  }
  return { boxes, width: PAD * 2 + widest, height: y - GAP_Y + PAD };
}

const LABEL_H = 16;
const labelWidth = (text: string) => text.length * 6.8 + 10;

export function relationshipText(r: ERRelationship): string {
  return r.attributes.length ? `${r.name} (${r.attributes.map((x) => x.name).join(", ")})` : r.name;
}

/**
 * A spot beside each relationship line for its name: along the line, clear of every box and
 * of the labels placed before it. Boxes are returned centered on the label.
 */
export function crowLabels(m: ERModel, l: { boxes: Record<string, Box> }): Record<string, Box> {
  const center = (b: Box): Point => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });
  const hit = (a: Box, b: Box) => !(a.x + a.w <= b.x - 3 || b.x + b.w <= a.x - 3 || a.y + a.h <= b.y - 3 || b.y + b.h <= a.y - 3);
  const placed: Record<string, Box> = {};
  for (const r of m.relationships) {
    if (r.left.entity === r.right.entity) continue;
    const a = l.boxes[r.left.entity];
    const b = l.boxes[r.right.entity];
    const pa = exitPoint(a, center(b));
    const pb = exitPoint(b, center(a));
    const len = Math.hypot(pb.x - pa.x, pb.y - pa.y) || 1;
    const n = { x: -(pb.y - pa.y) / len, y: (pb.x - pa.x) / len };
    const w = labelWidth(relationshipText(r));
    const reach = Math.abs(n.x) * (w / 2 + 6) + Math.abs(n.y) * (LABEL_H / 2 + 4);
    let best: Box | null = null;
    search: for (const t of [0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8]) {
      for (const side of [1, -1]) {
        for (const extra of [0, 18, 36]) {
          const cx = pa.x + (pb.x - pa.x) * t + n.x * side * (reach + extra);
          const cy = pa.y + (pb.y - pa.y) * t + n.y * side * (reach + extra);
          const box = { x: cx - w / 2, y: cy - LABEL_H / 2, w, h: LABEL_H };
          const clear = Object.values(l.boxes).every((x) => !hit(box, x)) && Object.values(placed).every((x) => !hit(box, x));
          if (clear) {
            best = box;
            break search;
          }
          best ??= box;
        }
      }
    }
    placed[r.name] = best as Box;
  }
  return placed;
}

export type Point = { x: number; y: number };

/** Where the segment from a box's center toward `to` leaves the box. */
export function exitPoint(b: Box, to: Point): Point {
  const cx = b.x + b.w / 2;
  const cy = b.y + b.h / 2;
  const dx = to.x - cx;
  const dy = to.y - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  const scale = Math.min(dx ? b.w / 2 / Math.abs(dx) : Infinity, dy ? b.h / 2 / Math.abs(dy) : Infinity);
  return { x: cx + dx * scale, y: cy + dy * scale };
}

export type ChenAttribute = Point & { name: string; key: boolean; rx: number };

export function chenLayout(m: ERModel): {
  entities: Record<string, Point>;
  diamonds: Record<string, Point>;
  isa: Record<string, Point>;
  attributes: Record<string, ChenAttribute[]>;
  width: number;
  height: number;
} {
  const grid = rows(m);
  const cols = Math.max(...grid.map((r) => r.length));
  const cellW = 460;
  const cellH = 360;
  const entities: Record<string, Point> = {};
  grid.forEach((r, row) => {
    const offset = ((cols - r.length) * cellW) / 2;
    r.forEach((name, i) => {
      entities[name] = { x: 230 + offset + i * cellW, y: 190 + row * cellH };
    });
  });
  // Each specialization's circle sits between the supertype and its subtypes.
  const isa: Record<string, Point> = {};
  for (const sp of m.specializations ?? []) {
    const p = entities[sp.parent];
    const kids = sp.children.map((c) => entities[c]);
    const kx = kids.reduce((s, k) => s + k.x, 0) / kids.length;
    const ky = Math.min(...kids.map((k) => k.y));
    isa[sp.parent] = { x: (p.x + kx) / 2, y: (p.y + ky) / 2 };
  }

  const diamonds: Record<string, Point> = {};
  const pairs: Record<string, number> = {};
  for (const r of m.relationships) {
    const a = entities[r.left.entity];
    const b = entities[r.right.entity];
    if (r.left.entity === r.right.entity) {
      diamonds[r.name] = { x: a.x + 150, y: a.y - 110 };
      continue;
    }
    const pair = [r.left.entity, r.right.entity].sort().join("|");
    const nth = (pairs[pair] = (pairs[pair] ?? -1) + 1);
    const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    const shift = nth === 0 ? 0 : (nth % 2 ? 1 : -1) * Math.ceil(nth / 2) * 60;
    diamonds[r.name] = {
      x: (a.x + b.x) / 2 - ((b.y - a.y) / len) * shift,
      y: (a.y + b.y) / 2 + ((b.x - a.x) / len) * shift,
    };
  }

  const ellipse = (a: ERAttribute, p: Point): ChenAttribute => ({ ...p, name: a.name, key: a.key, rx: Math.max(34, a.name.length * 4.2 + 16) });
  const attributes: Record<string, ChenAttribute[]> = {};
  for (const e of m.entities) {
    const c = entities[e.name];
    // Directions this entity's relationship lines leave in; attributes go elsewhere.
    const toward = (p: Point) => Math.atan2(p.y - c.y, p.x - c.x);
    const taken = [
      ...m.relationships.filter((r) => r.left.entity === e.name || r.right.entity === e.name).map((r) => toward(diamonds[r.name])),
      ...(m.specializations ?? []).filter((s) => s.parent === e.name || s.children.includes(e.name)).map((s) => toward(isa[s.parent])),
    ];
    attributes[e.name] = orbitSlots(e.attributes.length, taken).map((angle, i) => {
      const ring = i < 8 ? 1 : 1.55;
      return ellipse(e.attributes[i], { x: c.x + Math.cos(angle) * 150 * ring, y: c.y + Math.sin(angle) * 95 * ring });
    });
  }
  for (const r of m.relationships) {
    const d = diamonds[r.name];
    const a = entities[r.left.entity];
    const b = entities[r.right.entity];
    // Beside the diamond, across its line; above it for a self relationship.
    const len = Math.hypot(b.x - a.x, b.y - a.y);
    const v = len ? { x: -(b.y - a.y) / len, y: (b.x - a.x) / len } : { x: 0, y: -1 };
    const side = v.y > 0 || (v.y === 0 && v.x < 0) ? -1 : 1; // prefer above, then right
    attributes[r.name] = r.attributes.map((x, i) => {
      const spread = (i - (r.attributes.length - 1) / 2) * 44;
      return ellipse(x, { x: d.x + v.x * side * 92 - v.y * spread, y: d.y + v.y * side * 72 + v.x * spread });
    });
  }

  return { entities, diamonds, isa, attributes, width: cols * cellW, height: grid.length * cellH + 40 };
}

/**
 * Angles for n attributes around an entity: eight slots per ring, filled first in the
 * directions furthest from its relationship lines (upper slots win ties), then sorted so the
 * attributes read clockwise from the left.
 */
export function orbitSlots(n: number, taken: number[]): number[] {
  const gap = (a: number, b: number) => {
    const d = Math.abs(a - b) % (2 * Math.PI);
    return Math.min(d, 2 * Math.PI - d);
  };
  const out: number[] = [];
  for (let ring = 0; out.length < n; ring++) {
    const slots = Array.from({ length: 8 }, (_, k) => (k * Math.PI) / 4 + (ring % 2 ? Math.PI / 8 : 0));
    const score = (a: number) => (taken.length ? Math.min(...taken.map((t) => gap(a, t))) : Math.PI) - (Math.sin(a) > 0 ? 0.01 : 0);
    const best = [...slots].sort((a, b) => score(b) - score(a)).slice(0, n - out.length);
    const fromLeft = (a: number) => (a - Math.PI + 4 * Math.PI) % (2 * Math.PI);
    out.push(...best.sort((a, b) => fromLeft(a) - fromLeft(b)));
  }
  return out;
}

/** The design box takes files up to 1 MB, matching the API's limit on the design text. */
export const MAX_DESIGN_FILE = 1_000_000;

export function designFileProblem(size: number): string | null {
  if (size === 0) return "That file is empty.";
  if (size > MAX_DESIGN_FILE) return "Files can be at most 1 MB here; for bigger dumps, upload them on Your data.";
  return null;
}
