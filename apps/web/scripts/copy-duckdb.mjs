// Copies the DuckDB-WASM files the scratchpad loads into public/duckdb/<version>/, so the
// browser fetches them from this site, never from a CDN.
import { copyFileSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(web, "node_modules/@duckdb/duckdb-wasm/dist");
const { version } = JSON.parse(readFileSync(join(web, "node_modules/@duckdb/duckdb-wasm/package.json"), "utf8"));
const target = join(web, "public/duckdb", version);
mkdirSync(target, { recursive: true });
for (const file of ["duckdb-mvp.wasm", "duckdb-eh.wasm", "duckdb-browser-mvp.worker.js", "duckdb-browser-eh.worker.js"]) {
  copyFileSync(join(dist, file), join(target, file));
}
console.log(`DuckDB-WASM ${version} copied to public/duckdb/${version}`);
