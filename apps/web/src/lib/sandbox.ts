/**
 * The in-browser scratchpad's engine setup: DuckDB-WASM gets the assignment's sample tables,
 * built from the rows the page already has, and is then locked the way the server's engine
 * is. After LOCKDOWN it cannot install or load extensions, read files or URLs, or change
 * those settings back. Grading never happens here; hidden data never reaches the browser.
 */

export type Table = { columns: string[]; rows: unknown[][] };

/** In order, after the sample tables are created. lock_configuration must come last. */
export const LOCKDOWN = [
  "SET autoinstall_known_extensions = false",
  "SET autoload_known_extensions = false",
  "SET allow_community_extensions = false",
  "SET enable_external_access = false",
  "SET lock_configuration = true",
];

const ident = (name: string) => `"${name.replaceAll('"', '""')}"`;

function literal(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "NULL";
    // A non-whole number would make the column DECIMAL, which the browser reads back garbled.
    return Number.isInteger(value) ? String(value) : `${value}::DOUBLE`;
  }
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  return `'${String(value).replaceAll("'", "''")}'`;
}

/** CREATE TABLE statements that rebuild each sample table from its rows. */
export function tableStatements(tables: Record<string, Table>): string[] {
  return Object.entries(tables).map(([name, t]) => {
    const columns = t.columns.map(ident).join(", ");
    if (t.rows.length === 0) {
      return `CREATE TABLE ${ident(name)} (${t.columns.map((c) => `${ident(c)} VARCHAR`).join(", ")})`;
    }
    const values = t.rows.map((r) => `(${r.map(literal).join(", ")})`).join(", ");
    return `CREATE TABLE ${ident(name)} AS SELECT * FROM (VALUES ${values}) AS t(${columns})`;
  });
}

// Arrow type ids (apache-arrow's Type enum) for the values that need converting.
const DECIMAL = 7;
const DATE = 8;
const TIMESTAMP = 10;

type ArrowField = { name: string; type: { typeId: number; scale?: number } };
export type ArrowResult = { schema: { fields: ArrowField[] }; toArray: () => Record<string, unknown>[] };

function plainValue(value: unknown, field: ArrowField): unknown {
  if (value === null || value === undefined) return null;
  const id = field.type.typeId;
  if (id === DECIMAL) return Number(String(value)) / 10 ** (field.type.scale ?? 0);
  if (id === DATE && typeof value === "number") return new Date(value).toISOString().slice(0, 10);
  if (id === TIMESTAMP && typeof value === "number") return new Date(value).toISOString().replace(".000Z", "Z");
  if (typeof value === "bigint") return Number.isSafeInteger(Number(value)) ? Number(value) : value.toString();
  if (typeof value === "object") return JSON.stringify(value, (_, v) => (typeof v === "bigint" ? v.toString() : v));
  return value;
}

/** Column names and rows of plain values (no BigInt, decimals as numbers, dates as text). */
export function plainResult(result: ArrowResult): { columns: string[]; rows: unknown[][] } {
  const fields = result.schema.fields;
  return {
    columns: fields.map((f) => f.name),
    rows: result.toArray().map((row) => fields.map((f) => plainValue(row[f.name], f))),
  };
}
