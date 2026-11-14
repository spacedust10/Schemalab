/** Opens the scratchpad's DuckDB-WASM in a worker, from this site's own files, with the
 * sample tables loaded and the engine locked (see sandbox.ts). Browser only. */
import { AsyncDuckDB, VoidLogger, selectBundle } from "@duckdb/duckdb-wasm";
import { DUCKDB_FILES } from "./duckdb-files";
import { type ArrowResult, LOCKDOWN, type Table, plainResult, tableStatements } from "./sandbox";

export type Scratchpad = {
  run: (sql: string) => Promise<{ columns: string[]; rows: unknown[][] }>;
  close: () => Promise<void>;
};

export async function openScratchpad(tables: Record<string, Table>): Promise<Scratchpad> {
  const bundle = await selectBundle({
    mvp: { mainModule: DUCKDB_FILES.mvpModule, mainWorker: DUCKDB_FILES.mvpWorker },
    eh: { mainModule: DUCKDB_FILES.ehModule, mainWorker: DUCKDB_FILES.ehWorker },
  });
  const worker = new Worker(bundle.mainWorker as string);
  const db = new AsyncDuckDB(new VoidLogger(), worker);
  await db.instantiate(bundle.mainModule);
  const conn = await db.connect();
  for (const sql of [...tableStatements(tables), ...LOCKDOWN]) await conn.query(sql);
  return {
    run: async (sql) => plainResult((await conn.query(sql)) as unknown as ArrowResult),
    close: async () => {
      await conn.close();
      await db.terminate();
      worker.terminate();
    },
  };
}
