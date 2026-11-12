/** Uploaded tables: types from GET /datasets and the client side of an upload. */

export const MAX_MB = 50; // matches uploads.MAX_BYTES in the API

export type ColumnProfile = {
  name: string;
  type: string;
  min: string | null;
  max: string | null;
  distinct: number;
  null_percent: number;
  /** "primary key" or "references table(column)", from a .sql upload. */
  note: string | null;
};

export type Dataset = { name: string; rows: number; columns: ColumnProfile[] };

/** A message saying what is wrong with the file, or null. The API checks again. */
export function checkFile(name: string, size: number): string | null {
  if (!/\.(csv|xlsx|sql)$/i.test(name)) return "Choose a .csv, .xlsx or .sql file.";
  if (size === 0) return "That file is empty.";
  if (size > MAX_MB * 1024 * 1024) return `Files can be at most ${MAX_MB} MB.`;
  return null;
}

/** Send the file as the raw body; the API reads the name from the query string. */
export function uploadFile(file: File): Promise<Response> {
  return fetch(`/api/datasets?${new URLSearchParams({ name: file.name })}`, {
    method: "POST",
    headers: { "content-type": "application/octet-stream" },
    body: file,
  });
}

/** One line on what a .sql upload left out, or null if it left nothing out. */
export function skippedText(skipped: Record<string, number>): string | null {
  const { [COPY_FROM_FILE]: files = 0, ...rest } = skipped;
  const kinds = Object.entries(rest).sort((a, b) => b[1] - a[1]);
  const total = kinds.reduce((sum, [, n]) => sum + n, 0);
  const what = total === 1 ? "statement that holds" : "statements that hold";
  const parts = [
    files &&
      `The tables are empty: this file loads its rows with \\copy from ${files} CSV file${files === 1 ? "" : "s"} that ${files === 1 ? "is" : "are"} not part of it. You can still draw, normalize and ask about the schema.`,
    total && `Skipped ${total} ${what} no tables or rows: ${kinds.map(([k, n]) => `${n} ${k}`).join(", ")}.`,
  ].filter(Boolean);
  return parts.length ? parts.join(" ") : null;
}

/** The API's name for psql \\copy lines, whose rows live in files outside the upload. */
export const COPY_FROM_FILE = "\\copy from a CSV file not in this upload";

export function addedText(names: string[]): string {
  if (names.length <= 6) return `Added ${names.join(", ")}.`;
  return `Added ${names.length} tables: ${names.slice(0, 5).join(", ")} and ${names.length - 5} more.`;
}

export type Link = { from: string; to: string };

/** Foreign keys across all tables, from the column notes a .sql upload leaves. */
export function relationships(tables: Dataset[]): Link[] {
  return tables.flatMap((t) =>
    t.columns.flatMap((c) => {
      const m = c.note?.match(/^references (\w+)(?:\((\w+)\))?$/);
      return m ? [{ from: `${t.name}.${c.name}`, to: m[2] ? `${m[1]}.${m[2]}` : m[1] }] : [];
    }),
  );
}
