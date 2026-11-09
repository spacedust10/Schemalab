/** Uploaded tables: types from GET /datasets and the client side of an upload. */

export const MAX_MB = 50; // matches uploads.MAX_BYTES in the API

export type ColumnProfile = {
  name: string;
  type: string;
  min: string | null;
  max: string | null;
  distinct: number;
  null_percent: number;
};

export type Dataset = { name: string; rows: number; columns: ColumnProfile[] };

/** A message saying what is wrong with the file, or null. The API checks again. */
export function checkFile(name: string, size: number): string | null {
  if (!/\.(csv|xlsx)$/i.test(name)) return "Choose a .csv or .xlsx file.";
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
