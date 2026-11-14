// @vitest-environment node
import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { DUCKDB_FILES, DUCKDB_VERSION } from "./duckdb-files";

describe("self-hosted DuckDB-WASM files", () => {
  it("names the installed package version, so the files and the loader never drift", () => {
    const installed = JSON.parse(readFileSync("node_modules/@duckdb/duckdb-wasm/package.json", "utf8")).version;
    expect(DUCKDB_VERSION).toBe(installed);
    const pinned = JSON.parse(readFileSync("package.json", "utf8")).dependencies["@duckdb/duckdb-wasm"];
    expect(pinned).toBe(installed); // an exact pin, not a range
  });

  it("is copied into public before dev and build, and kept out of git", () => {
    const scripts = JSON.parse(readFileSync("package.json", "utf8")).scripts;
    expect(scripts.predev).toContain("copy-duckdb");
    expect(scripts.prebuild).toContain("copy-duckdb");
    expect(readFileSync(".gitignore", "utf8")).toMatch(/^\/public\/duckdb\/$/m);
    for (const file of Object.values(DUCKDB_FILES)) {
      expect(existsSync(`public${file}`), file).toBe(true);
    }
  });
});
