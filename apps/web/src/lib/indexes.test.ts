import { describe, expect, it } from "vitest";
import { type BTree, bplusInsertSteps, bplusSearchSteps, ehInsertSteps, type Hashing } from "./indexes";

function rng(seed: number) {
  let s = seed;
  return () => (s = (s * 1103515245 + 12345) % 2 ** 31) / 2 ** 31;
}

function leaves(t: BTree): number[][] {
  let id = t.root;
  while (!t.nodes[id].leaf) id = t.nodes[id].children[0];
  const out: number[][] = [];
  for (let at: number | null = id; at !== null; at = t.nodes[at].next) out.push(t.nodes[at].keys);
  return out;
}

function depths(t: BTree, id = t.root, d = 0, out: number[] = []): number[] {
  const n = t.nodes[id];
  if (n.leaf) out.push(d);
  else n.children.forEach((c) => depths(t, c, d + 1, out));
  return out;
}

describe("B+ tree", () => {
  it("narrates a split that copies the middle key up", () => {
    const steps = bplusInsertSteps([10, 20, 30, 40], 3);
    const notes = steps.map((s) => s.note).join(" | ");
    expect(notes).toContain("Leaf is full");
    expect(notes).toContain("copy 30 up");
    const last = steps[steps.length - 1].tree;
    expect(last.nodes[last.root].keys).toEqual([30]);
    expect(leaves(last)).toEqual([[10, 20], [30, 40]]);
  });

  it("stays a valid B+ tree for 200 random inserts", () => {
    const next = rng(13);
    for (let round = 0; round < 20; round++) {
      const keys = Array.from({ length: 10 }, () => Math.floor(next() * 100));
      const unique = [...new Set(keys)];
      const steps = bplusInsertSteps(keys, 3);
      const t = steps[steps.length - 1].tree;
      expect(new Set(depths(t)).size).toBe(1); // balanced
      const flat = leaves(t).flat();
      expect(flat).toEqual([...unique].sort((a, b) => a - b)); // sorted, linked, no duplicates
      for (const n of Object.values(t.nodes)) {
        expect(n.keys.length).toBeLessThanOrEqual(3);
        if (n.leaf && n.id !== t.root) expect(n.keys.length).toBeGreaterThanOrEqual(2);
      }
      for (const k of unique) expect(bplusSearchSteps(t, k).at(-1)!.found).toBe(true);
      expect(bplusSearchSteps(t, 1000).at(-1)!.found).toBe(false);
    }
  });

  it("says duplicates are already there", () => {
    const steps = bplusInsertSteps([5, 5], 3);
    expect(steps.at(-1)!.note).toContain("5 is already in the tree");
  });
});

function check(h: Hashing) {
  expect(h.directory).toHaveLength(2 ** h.globalDepth);
  for (const [id, b] of Object.entries(h.buckets)) {
    const pointing = h.directory.filter((d) => d === Number(id)).length;
    expect(pointing).toBe(2 ** (h.globalDepth - b.localDepth));
    for (const k of b.keys) expect(h.directory[k & (2 ** h.globalDepth - 1)]).toBe(Number(id));
  }
}

describe("extendible hashing", () => {
  it("doubles the directory when a full bucket's depth equals the global depth", () => {
    const steps = ehInsertSteps([4, 8, 12], 2); // binary 100, 1000, 1100: same last bits
    const notes = steps.map((s) => s.note).join(" | ");
    expect(notes).toContain("double the directory");
    const h = steps.at(-1)!.hashing;
    check(h);
    expect(h.globalDepth).toBeGreaterThanOrEqual(3);
  });

  it("splits without doubling when the bucket's local depth is smaller", () => {
    const steps = ehInsertSteps([0, 1, 2, 4, 6], 2);
    expect(steps.map((s) => s.note).join(" | ")).toContain("split the bucket");
    check(steps.at(-1)!.hashing);
  });

  it("keeps every invariant for random inserts", () => {
    const next = rng(7);
    for (let round = 0; round < 30; round++) {
      const keys = [...new Set(Array.from({ length: 12 }, () => Math.floor(next() * 64)))];
      const steps = ehInsertSteps(keys, 3);
      const h = steps.at(-1)!.hashing;
      check(h);
      expect(Object.values(h.buckets).flatMap((b) => b.keys).sort((a, b) => a - b)).toEqual([...keys].sort((a, b) => a - b));
      for (const b of Object.values(h.buckets)) expect(b.keys.length).toBeLessThanOrEqual(3);
    }
  });
});

describe("btreeLayout", async () => {
  const { btreeLayout } = await import("./indexes");
  it("puts each level lower and keeps a level's nodes apart", () => {
    const steps = bplusInsertSteps([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], 3);
    const l = btreeLayout(steps.at(-1)!.tree);
    const byLevel = new Map<number, typeof l.nodes>();
    for (const n of l.nodes) byLevel.set(n.y, [...(byLevel.get(n.y) ?? []), n]);
    expect(byLevel.size).toBeGreaterThanOrEqual(3);
    for (const row of byLevel.values()) {
      const sorted = [...row].sort((a, b) => a.x - b.x);
      for (let i = 1; i < sorted.length; i++) expect(sorted[i].x).toBeGreaterThanOrEqual(sorted[i - 1].x + sorted[i - 1].w);
    }
    for (const n of l.nodes) expect(n.x + n.w).toBeLessThanOrEqual(l.width);
  });
});

describe("speedup", async () => {
  const { speedup } = await import("./plans");
  it("says how much faster or slower, in words", () => {
    expect(speedup(7.48, 0.073)).toBe("102× faster");
    expect(speedup(2, 1)).toBe("2.0× faster");
    expect(speedup(1, 1.1)).toBe("about the same");
    expect(speedup(1, 3)).toBe("3.0× slower");
  });
});

describe("every animation frame can be drawn", async () => {
  const { btreeLayout } = await import("./indexes");
  it("places every node an edge or a leaf link points to", () => {
    const steps = bplusInsertSteps([10, 20, 5, 6, 12, 30, 7, 17, 3, 25, 28, 1], 3);
    for (const [i, step] of steps.entries()) {
      const placed = new Set(btreeLayout(step.tree).nodes.map((n) => n.id));
      for (const n of Object.values(step.tree.nodes)) {
        if (!placed.has(n.id)) throw new Error(`step ${i}: node ${n.id} not placed (${step.note})`);
        if (n.next !== null && !placed.has(n.next)) throw new Error(`step ${i}: next ${n.next} missing`);
      }
    }
  });
});

describe("planError", async () => {
  const { planError } = await import("./plans");
  it("turns a closed lab into what to do on this page", () => {
    expect(planError("Start the lab first.")).toBe(
      "Your lab is not running: it closes after 20 minutes idle or when the server restarts. Load the example data to start it again.",
    );
    expect(planError('relation "nope" does not exist')).toBe('relation "nope" does not exist');
  });
});
