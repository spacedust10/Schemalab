/** ER models from /er/* and the geometry for drawing them in Chen and crow's-foot notation. */

export type ERAttribute = { name: string; type: string; key: boolean };
export type EREntity = { name: string; attributes: ERAttribute[] };
export type EREnd = { entity: string; card: "1" | "N"; total: boolean };
export type ERRelationship = {
  name: string;
  kind: string;
  left: EREnd;
  right: EREnd;
  attributes: ERAttribute[];
};
export type ERModel = { entities: EREntity[]; relationships: ERRelationship[] };

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

export function crowLayout(m: ERModel): { boxes: Record<string, Box>; width: number; height: number } {
  const order = entityOrder(m);
  const size = (e: EREntity) => ({
    w: Math.max(140, 40 + CHAR * Math.max(e.name.length, ...e.attributes.map((a) => a.name.length + a.type.length + 2))),
    h: HEADER + ROW * e.attributes.length + 8,
  });
  const sizes = Object.fromEntries(m.entities.map((e) => [e.name, size(e)]));
  const cols = Math.max(1, Math.ceil(Math.sqrt(order.length)));
  const cellW = Math.max(...Object.values(sizes).map((s) => s.w)) + 150;
  const cellH = Math.max(...Object.values(sizes).map((s) => s.h)) + 110;
  const boxes: Record<string, Box> = {};
  order.forEach((name, i) => {
    const { w, h } = sizes[name];
    const col = i % cols;
    const row = Math.floor(i / cols);
    boxes[name] = { x: PAD + col * cellW + (cellW - 150 - w) / 2, y: PAD + row * cellH, w, h };
  });
  const rows = Math.ceil(order.length / cols);
  return { boxes, width: PAD * 2 + cols * cellW - 150, height: PAD * 2 + rows * cellH - 110 };
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
  attributes: Record<string, ChenAttribute[]>;
  width: number;
  height: number;
} {
  const order = entityOrder(m);
  const cols = Math.max(1, Math.ceil(Math.sqrt(order.length)));
  const cellW = 460;
  const cellH = 360;
  const entities: Record<string, Point> = {};
  order.forEach((name, i) => {
    entities[name] = { x: 230 + (i % cols) * cellW, y: 190 + Math.floor(i / cols) * cellH };
  });

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
    const taken = m.relationships
      .filter((r) => r.left.entity === e.name || r.right.entity === e.name)
      .map((r) => Math.atan2(diamonds[r.name].y - c.y, diamonds[r.name].x - c.x));
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

  const rows = Math.ceil(order.length / cols);
  return { entities, diamonds, attributes, width: cols * cellW, height: rows * cellH + 40 };
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
