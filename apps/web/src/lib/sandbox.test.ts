// @vitest-environment node
import path from "node:path";
import { createRequire } from "node:module";
import { beforeAll, describe, expect, it } from "vitest";
import { type ArrowResult, LOCKDOWN, plainResult, tableStatements } from "./sandbox";

// The real DuckDB-WASM, through its Node build: the same engine and settings the page uses.
const require = createRequire(import.meta.url);
const duck = require("@duckdb/duckdb-wasm/dist/duckdb-node-blocking.cjs");
type Conn = { query: (sql: string) => ArrowResult };
let conn: Conn;

const rows = (sql: string) =>
  conn.query(sql).toArray().map((r) => (r as unknown as { toJSON: () => Record<string, unknown> }).toJSON());

beforeAll(async () => {
  const dist = path.dirname(require.resolve("@duckdb/duckdb-wasm/dist/duckdb-node-blocking.cjs"));
  const db = await duck.createDuckDB(
    { mvp: { mainModule: path.join(dist, "duckdb-mvp.wasm"), mainWorker: "" }, eh: { mainModule: path.join(dist, "duckdb-eh.wasm"), mainWorker: "" } },
    new duck.VoidLogger(),
    duck.NODE_RUNTIME,
  );
  await db.instantiate();
  conn = db.connect();
  const tables = {
    students: { columns: ["id", "name", "gpa"], rows: [[1, "O'Brien", 3.5], [2, null, 4]] },
    "odd \"name\"": { columns: ["x"], rows: [] },
  };
  for (const sql of [...tableStatements(tables), ...LOCKDOWN]) conn.query(sql);
});

describe("the in-browser scratchpad", () => {
  it("loads the sample rows as tables, quotes and NULLs intact", () => {
    expect(rows("SELECT id, name, gpa FROM students ORDER BY id")).toEqual([
      { id: 1, name: "O'Brien", gpa: 3.5 },
      { id: 2, name: null, gpa: 4 },
    ]);
    expect(rows('SELECT count(*)::INT AS n FROM "odd ""name"""')).toEqual([{ n: 0 }]);
  });

  it("cannot reach the network or the file system", () => {
    expect(() => rows("SELECT * FROM read_csv('https://example.com/x.csv')")).toThrow(/external|disabled/i);
    expect(() => rows("SELECT * FROM read_text('/etc/hosts')")).toThrow(/external|disabled|permission/i);
  });

  it("cannot load extensions", () => {
    rows("INSTALL httpfs"); // a no-op in WASM: what matters is that nothing can be loaded
    expect(() => rows("LOAD httpfs")).toThrow(/disabled/i);
    expect(rows("SELECT count(*)::INT AS n FROM duckdb_extensions() WHERE loaded AND extension_name = 'httpfs'")).toEqual([{ n: 0 }]);
  });

  it("cannot be unlocked", () => {
    expect(() => rows("SET enable_external_access = true")).toThrow(/lock/i);
    expect(() => rows("SET autoload_known_extensions = true")).toThrow(/lock/i);
  });
});

describe("showing results", () => {
  it("turns big integers, decimals and dates into plain values", () => {
    const got = plainResult(
      conn.query("SELECT 9007199254740993::BIGINT AS big, 42::BIGINT AS n, 1.5::DECIMAL(4, 1) AS d, DATE '2024-01-02' AS day, 'x' AS s"),
    );
    expect(got.columns).toEqual(["big", "n", "d", "day", "s"]);
    expect(got.rows).toEqual([["9007199254740993", 42, 1.5, "2024-01-02", "x"]]);
  });
});
