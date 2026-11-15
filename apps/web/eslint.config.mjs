import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  // public/duckdb holds the DuckDB-WASM bundle, copied in from node_modules at build time.
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "public/duckdb/**"]),
]);
