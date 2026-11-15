/** Where the self-hosted DuckDB-WASM files are served from. scripts/copy-duckdb.mjs puts them
 * there before dev and build; the version is in the path so a new release never mixes with an
 * old cached file. Only the single-threaded bundles: they need no cross-origin isolation. */
export const DUCKDB_VERSION = "1.32.0";
/** The DuckDB core that package embeds (a test asks the engine), shown to students. */
export const DUCKDB_CORE = "1.4.3";

const base = `/duckdb/${DUCKDB_VERSION}`;

export const DUCKDB_FILES = {
  mvpModule: `${base}/duckdb-mvp.wasm`,
  mvpWorker: `${base}/duckdb-browser-mvp.worker.js`,
  ehModule: `${base}/duckdb-eh.wasm`,
  ehWorker: `${base}/duckdb-browser-eh.worker.js`,
};
