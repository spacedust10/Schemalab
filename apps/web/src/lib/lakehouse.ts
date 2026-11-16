/** The lakehouse page's data, and which calls the browser may pass through to the API. */

export const LAYERS = ["bronze", "silver", "gold"] as const;
export type Layer = (typeof LAYERS)[number];
export type LakeTable = { layer: Layer; name: string; rows: number; columns: { name: string; type: string }[] };
export type Run = {
  id: number;
  kind: "file" | "sheet" | "quakes" | "transform";
  label: string;
  status: "running" | "done" | "failed";
  result: Record<string, number> | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  snapshot: number | null; // the lake version the run left behind
};
export type Snapshot = { id: number; at: string; changes: Record<string, string[]> };
export type Model = { layer: "silver" | "gold"; name: string; sql: string };
export type Overview = { tables: LakeTable[]; snapshots: Snapshot[]; runs: Run[]; models: Model[]; available: boolean };
export type Preview = { columns: string[]; rows: unknown[][]; total: number };

const TABLE = /^[a-z_][a-z0-9_]{0,62}$/;
const POSTS = new Set(["quakes", "sheets", "transform"]);

/** The API path for a browser call, or null when it is not one the page makes. */
export function upstreamPath(method: string, path: string[], search: URLSearchParams): string | null {
  const [first, ...rest] = path;
  if (method === "GET" && path.length === 0) return "/lakehouse";
  if (method === "GET" && first === "tables" && rest.length === 2) {
    const [layer, table] = rest;
    if (!(LAYERS as readonly string[]).includes(layer) || !TABLE.test(table)) return null;
    const version = search.get("version");
    const query = version && /^\d{1,12}$/.test(version) ? `?${new URLSearchParams({ version })}` : "";
    return `/lakehouse/tables/${layer}/${table}${query}`;
  }
  if (path.length !== 1) return null;
  if (method === "POST" && first === "files") {
    const name = search.get("name");
    if (!name) return null;
    const table = search.get("table");
    return `/lakehouse/files?${new URLSearchParams(table ? { name, table } : { name })}`;
  }
  if (method === "POST" && POSTS.has(first)) return `/lakehouse/${first}`;
  if (method === "PUT" && first === "models") return "/lakehouse/models";
  return null;
}

const rows = (n: number) => `${n} row${n === 1 ? "" : "s"}`;

export function runLine(run: Run): string {
  if (run.status === "running") return "Running…";
  if (run.status === "failed") return run.error ?? "The run failed.";
  const counts = Object.entries(run.result ?? {});
  if (counts.length === 0) return "Nothing new to load.";
  const made = counts.map(([table, n]) => `${table}: ${rows(n)}`).join(", ");
  return run.snapshot === null ? made : `${made}, as snapshot #${run.snapshot}`;
}

/** A snapshot named after the run that made it, when there is one. */
export function snapshotLabel(id: number, runs: Run[]): string {
  const run = runs.find((r) => r.snapshot === id);
  return run ? `Snapshot #${id}, after ${run.label}` : `Snapshot #${id}`;
}

export const busy = (runs: Run[]) => runs.some((r) => r.status === "running");

/** A user's own table: in a layer, and not one of dlt's _dlt_* tables. */
const own = (name: string) => {
  const [schema, table = ""] = name.split(".");
  return (LAYERS as readonly string[]).includes(schema) && !table.startsWith("_dlt");
};

/** DuckLake's snapshot change keys in plain words. Inserts name internal ids, so no names. */
export function changeLine(changes: Record<string, string[]>): string {
  const parts = Object.entries(changes).map(([key, values]) => {
    if (key === "schemas_created") {
      if (values.includes("main")) return "made the lake";
      const layers = values.filter((v) => (LAYERS as readonly string[]).includes(v));
      return layers.length ? `created layer ${layers.join(", ")}` : "loader bookkeeping";
    }
    if (key === "tables_created" || key === "tables_dropped") {
      const mine = values.filter(own);
      if (!mine.length) return "loader bookkeeping";
      return `${key === "tables_created" ? "created" : "dropped"} ${mine.join(", ")}`;
    }
    if (key.includes("insert")) return "added rows";
    if (key.includes("delete")) return "removed rows";
    return key.replaceAll("_", " ");
  });
  return parts.length ? [...new Set(parts)].join("; ") : "no change";
}

/** Which columns hold only numbers (nulls aside), so they can be right-aligned. */
export function numericColumns(rows: unknown[][]): boolean[] {
  const width = rows[0]?.length ?? 0;
  return Array.from({ length: width }, (_, j) => {
    const values = rows.map((r) => r[j]).filter((v) => v !== null);
    return values.length > 0 && values.every((v) => typeof v === "number");
  });
}

/** The open table and the snapshot it is shown as of, as the page's query string. */
export type Open = { layer: Layer; name: string; version: string };

export function previewQuery(open: Open | null): string {
  if (!open) return "";
  const q: Record<string, string> = { table: `${open.layer}.${open.name}` };
  if (open.version) q.as_of = open.version;
  return new URLSearchParams(q).toString();
}

export function previewFromQuery(search: URLSearchParams): Open | null {
  const parts = (search.get("table") ?? "").split(".");
  if (parts.length !== 2) return null;
  const [layer, name] = parts;
  if (!(LAYERS as readonly string[]).includes(layer) || !TABLE.test(name)) return null;
  const asOf = search.get("as_of") ?? "";
  return { layer: layer as Layer, name, version: /^\d{1,12}$/.test(asOf) ? asOf : "" };
}
