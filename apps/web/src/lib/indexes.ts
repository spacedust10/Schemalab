/**
 * Step-by-step index structures for the animations: a B+ tree (insert with splits, search) and
 * extendible hashing (bucket splits, directory doubling). Every step carries a full snapshot, so
 * the page can step back and forth without recomputing.
 */

export type BNode = { id: number; keys: number[]; children: number[]; leaf: boolean; next: number | null };
export type BTree = { root: number; nodes: Record<number, BNode> };
export type BStep = { tree: BTree; note: string; highlight: number[]; found?: boolean };

const snapshot = (t: BTree): BTree => structuredClone(t);

function emptyTree(): BTree {
  return { root: 0, nodes: { 0: { id: 0, keys: [], children: [], leaf: true, next: null } } };
}

/** Insert keys one at a time; a node holds at most maxKeys keys and splits beyond that. */
export function bplusInsertSteps(keys: number[], maxKeys = 3, start?: BTree): BStep[] {
  const t = start ? snapshot(start) : emptyTree();
  let nextId = Math.max(...Object.keys(t.nodes).map(Number)) + 1;
  const steps: BStep[] = [];
  const say = (note: string, highlight: number[] = []) => steps.push({ tree: snapshot(t), note, highlight });

  for (const key of keys) {
    const path: number[] = [];
    let id = t.root;
    say(`Insert ${key}: start at the root.`, [id]);
    while (!t.nodes[id].leaf) {
      const n = t.nodes[id];
      const i = n.keys.filter((k) => key >= k).length;
      path.push(id);
      id = n.children[i];
      const why = i === 0 ? `${key} < ${n.keys[0]}` : `${key} ≥ ${n.keys[i - 1]}`;
      say(`${why}: go down to child ${i + 1}.`, [id]);
    }
    const leaf = t.nodes[id];
    if (leaf.keys.includes(key)) {
      say(`${key} is already in the tree; a unique index keeps one copy.`, [id]);
      continue;
    }
    leaf.keys = [...leaf.keys, key].sort((a, b) => a - b);
    say(`Put ${key} in the leaf, keeping the keys sorted.`, [id]);
    if (leaf.keys.length <= maxKeys) continue;

    // Leaf split: the right half's first key is copied up; it stays in the leaf too.
    const half = Math.ceil(leaf.keys.length / 2);
    const right: BNode = { id: nextId++, keys: leaf.keys.slice(half), children: [], leaf: true, next: leaf.next };
    leaf.keys = leaf.keys.slice(0, half);
    leaf.next = right.id;
    t.nodes[right.id] = right;
    let up = right.keys[0];
    let child = right.id;
    // Each split is told once the new node hangs off a parent, so every frame is a whole tree.
    let pending = `Leaf is full (${maxKeys + 1} keys > ${maxKeys}): split into [${leaf.keys.join(", ")}] and [${right.keys.join(", ")}], copy ${up} up to the parent.`;
    let touched = [leaf.id, right.id];
    let left = leaf.id;
    for (;;) {
      const parentId = path.pop();
      if (parentId === undefined) {
        const root: BNode = { id: nextId++, keys: [up], children: [left, child], leaf: false, next: null };
        t.nodes[root.id] = root;
        t.root = root.id;
        say(`${pending} New root [${up}]: the tree grows one level, at the top, so it stays balanced.`, [...touched, root.id]);
        break;
      }
      const parent = t.nodes[parentId];
      const at = parent.children.indexOf(left) + 1;
      parent.keys.splice(at - 1, 0, up);
      parent.children.splice(at, 0, child);
      if (parent.keys.length <= maxKeys) {
        say(`${pending} Add ${up} to the parent.`, [...touched, parent.id]);
        break;
      }
      // Internal split: the middle key moves up and leaves this level.
      const mid = Math.floor(parent.keys.length / 2);
      const moved = parent.keys[mid];
      const sibling: BNode = {
        id: nextId++,
        keys: parent.keys.slice(mid + 1),
        children: parent.children.slice(mid + 1),
        leaf: false,
        next: null,
      };
      parent.keys = parent.keys.slice(0, mid);
      parent.children = parent.children.slice(0, mid + 1);
      t.nodes[sibling.id] = sibling;
      pending = `${pending} The parent is now full too: split it and push ${moved} up (it moves, it is not copied).`;
      touched = [parent.id, sibling.id];
      up = moved;
      left = parent.id;
      child = sibling.id;
    }
  }
  return steps;
}

export function bplusSearchSteps(tree: BTree, key: number): BStep[] {
  const steps: BStep[] = [];
  let id = tree.root;
  steps.push({ tree, note: `Search ${key}: start at the root.`, highlight: [id] });
  while (!tree.nodes[id].leaf) {
    const n = tree.nodes[id];
    const i = n.keys.filter((k) => key >= k).length;
    id = n.children[i];
    steps.push({ tree, note: `Go down to child ${i + 1}.`, highlight: [id] });
  }
  const found = tree.nodes[id].keys.includes(key);
  steps.push({
    tree,
    note: found ? `Found ${key} in the leaf: one node read per level.` : `${key} is not in the leaf, so it is not in the tree.`,
    highlight: [id],
    found,
  });
  return steps;
}

// Extendible hashing

export type Bucket = { localDepth: number; keys: number[] };
export type Hashing = { globalDepth: number; directory: number[]; buckets: Record<number, Bucket> };
export type HStep = { hashing: Hashing; note: string; highlight: number[] };

const MAX_GLOBAL_DEPTH = 8;
const bits = (n: number, width: number) => (width ? (n & (2 ** width - 1)).toString(2).padStart(width, "0") : "");

/** Each key hashes to itself; its last `globalDepth` bits pick the directory entry. */
export function ehInsertSteps(keys: number[], capacity = 2): HStep[] {
  const h: Hashing = { globalDepth: 1, directory: [0, 1], buckets: { 0: { localDepth: 1, keys: [] }, 1: { localDepth: 1, keys: [] } } };
  let nextId = 2;
  const steps: HStep[] = [];
  const say = (note: string, highlight: number[] = []) => steps.push({ hashing: structuredClone(h), note, highlight });

  for (const key of keys) {
    for (;;) {
      const slot = key & (2 ** h.globalDepth - 1);
      const id = h.directory[slot];
      const b = h.buckets[id];
      if (b.keys.includes(key)) {
        say(`${key} is already stored.`, [id]);
        break;
      }
      if (b.keys.length < capacity || h.globalDepth >= MAX_GLOBAL_DEPTH) {
        b.keys.push(key);
        say(`${key} ends in ${bits(key, h.globalDepth)}: directory entry ${slot} points to this bucket.`, [id]);
        break;
      }
      if (b.localDepth === h.globalDepth) {
        h.directory = [...h.directory, ...h.directory];
        h.globalDepth += 1;
        say(`The bucket is full and its local depth equals the global depth: double the directory (global depth ${h.globalDepth}).`, [id]);
      }
      // Split on the next bit: keys with that bit set move to a new bucket.
      const bit = b.localDepth;
      const fresh: Bucket = { localDepth: bit + 1, keys: b.keys.filter((k) => (k >> bit) & 1) };
      b.keys = b.keys.filter((k) => !((k >> bit) & 1));
      b.localDepth = bit + 1;
      const newId = nextId++;
      h.buckets[newId] = fresh;
      h.directory = h.directory.map((d, i) => (d === id && (i >> bit) & 1 ? newId : d));
      say(`The bucket is full: split the bucket on bit ${bit}; keys with that bit set move to a new bucket.`, [id, newId]);
    }
  }
  return steps;
}

export const KEY_W = 30;
const NODE_PAD = 8;
const LEVEL_H = 72;
const GAP = 18;

export type PlacedNode = { id: number; keys: number[]; leaf: boolean; x: number; y: number; w: number };

/** Leaves left to right in key order; each internal node centered over its children. */
export function btreeLayout(t: BTree): { nodes: PlacedNode[]; edges: [number, number][]; width: number; height: number } {
  const width = (n: BNode) => Math.max(1, n.keys.length) * KEY_W + NODE_PAD;
  const placed = new Map<number, PlacedNode>();
  const edges: [number, number][] = [];
  let cursor = 12;
  let depth = 0;
  const place = (id: number, level: number): PlacedNode => {
    depth = Math.max(depth, level);
    const n = t.nodes[id];
    let x: number;
    if (n.leaf) {
      x = cursor;
      cursor += width(n) + GAP;
    } else {
      const kids = n.children.map((c) => place(c, level + 1));
      for (const k of kids) edges.push([id, k.id]);
      const first = kids[0];
      const last = kids[kids.length - 1];
      x = (first.x + last.x + last.w) / 2 - width(n) / 2;
    }
    const p = { id, keys: n.keys, leaf: n.leaf, x, y: 12 + level * LEVEL_H, w: width(n) };
    placed.set(id, p);
    return p;
  };
  place(t.root, 0);
  // A wide root over narrow leaves can start left of 0; shift everything right if so.
  const shift = Math.max(0, 12 - Math.min(...[...placed.values()].map((p) => p.x)));
  const nodes = [...placed.values()].map((p) => ({ ...p, x: p.x + shift }));
  return { nodes, edges, width: Math.max(cursor, ...nodes.map((p) => p.x + p.w + 12)) + shift, height: 12 + depth * LEVEL_H + 44 };
}
