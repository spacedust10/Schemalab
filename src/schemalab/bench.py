"""TPC-H and TPC-DS on DuckDB and PostgreSQL: the same data, the same queries, both timed.

DuckDB's tpch and tpcds extensions generate the data (dbgen, dsdgen) and supply the standard
queries (22 for TPC-H, 99 for TPC-DS). The data goes into a DuckDB file and into a PostgreSQL
schema in the lab's server, which lab users cannot read. Queries are translated to PostgreSQL by
sqlglot; PostgreSQL has round(numeric, int) but no round(double precision, int), so round's
argument is cast to NUMERIC. At scale 0.01 all 121 queries return identical results on both
engines, so every run also checks that the two agree.

Only the fixed TPC query text ever runs here, never user SQL. Results are derived from the TPC
kits and are not comparable to published TPC results.
"""

import datetime
import math
import statistics
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import duckdb
import psycopg
import sqlglot
from psycopg import sql
from sqlglot import exp

READY = "schemalab benchmark data"
_PREPARE_LOCK = threading.Lock()

Progress = Callable[[str], None]


@dataclass(frozen=True)
class Benchmark:
    name: str
    generate: str  # takes {sf}
    queries: str
    scales: tuple[float, ...]


BENCHMARKS = {
    "tpch": Benchmark(
        "TPC-H",
        "CALL dbgen(sf={sf})",
        "SELECT query_nr, query FROM tpch_queries() ORDER BY 1",
        (0.01, 0.1, 1.0),
    ),
    "tpcds": Benchmark(
        "TPC-DS",
        "CALL dsdgen(sf={sf})",
        "SELECT query_nr, query FROM tpcds_queries() ORDER BY 1",
        (0.01, 0.1, 1.0),
    ),
}


def _scale(sf: float) -> str:
    return f"{sf:g}".replace(".", "_")


def schema_name(bench: str, sf: float) -> str:
    return f"bench_{bench}_sf{_scale(sf)}"


def duckdb_path(root: Path, bench: str, sf: float) -> Path:
    return root / f"{bench}_sf{_scale(sf)}.duckdb"


def _check(bench: str, sf: float) -> Benchmark:
    if bench not in BENCHMARKS or sf not in BENCHMARKS[bench].scales:
        raise ValueError(f"Unknown benchmark {bench} at scale {sf}.")
    return BENCHMARKS[bench]


def to_postgres(query: str) -> str:
    tree = sqlglot.parse_one(query, read="duckdb")
    for r in tree.find_all(exp.Round):
        if r.args.get("decimals") is not None:
            r.set("this", exp.cast(r.this, "DECIMAL"))
    return tree.sql(dialect="postgres")


def _pg_type(duck: str) -> str:
    return {"DOUBLE": "DOUBLE PRECISION", "FLOAT": "REAL"}.get(duck, duck)


def prepare(root: Path, dsn: str, bench: str, sf: float, progress: Progress) -> None:
    """Generate the data into DuckDB and load it into PostgreSQL, unless both are there."""
    spec = _check(bench, sf)
    schema = schema_name(bench, sf)
    path = duckdb_path(root, bench, sf)
    with _PREPARE_LOCK, psycopg.connect(dsn, autocommit=True) as pg:
        ready = pg.execute(
            "SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname = %s",
            [schema],
        ).fetchone()
        if path.exists() and ready and ready[0] == READY:
            progress("Data already prepared.")
            return
        root.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            progress(f"Generating {spec.name} at scale {sf:g} in DuckDB…")
            tmp = path.with_suffix(".building")
            tmp.unlink(missing_ok=True)
            with duckdb.connect(str(tmp)) as con:
                con.execute(f"INSTALL {bench}")  # a no-op once installed, as in the image
                con.execute(f"LOAD {bench}")
                con.execute(spec.generate.format(sf=float(sf)))  # sf is one of the fixed scales
            tmp.replace(path)
        progress(f"Loading {spec.name} into PostgreSQL…")
        ident = sql.Identifier(schema)
        pg.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        pg.execute(sql.SQL("CREATE SCHEMA {}").format(ident))
        with (
            duckdb.connect(str(path), read_only=True) as con,
            tempfile.TemporaryDirectory() as work,
        ):
            tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
            for n, table in enumerate(tables, start=1):
                columns = con.execute(f'DESCRIBE "{table}"').fetchall()
                pg.execute(
                    sql.SQL("CREATE TABLE {}.{} ({})").format(
                        ident,
                        sql.Identifier(table),
                        sql.SQL(", ").join(
                            sql.SQL("{} {}").format(sql.Identifier(c[0]), sql.SQL(_pg_type(c[1])))
                            for c in columns
                        ),
                    )
                )
                csv_path = Path(work) / f"{table}.csv"
                con.execute(f"COPY \"{table}\" TO '{csv_path}' (HEADER false)")  # our temp path
                copy = sql.SQL("COPY {}.{} FROM STDIN (FORMAT csv)").format(
                    ident, sql.Identifier(table)
                )
                with pg.cursor().copy(copy) as cp, csv_path.open("rb") as f:
                    while chunk := f.read(1 << 20):
                        cp.write(chunk)
                progress(f"Loaded {table} ({n} of {len(tables)}).")
        pg.execute(sql.SQL("ANALYZE"))
        pg.execute(sql.SQL("COMMENT ON SCHEMA {} IS {}").format(ident, sql.Literal(READY)))


def _norm(value: Any) -> Any:
    if isinstance(value, float | Decimal):
        return round(float(value), 2)
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    return value


def _rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return sorted((tuple(_norm(v) for v in r) for r in rows), key=repr)


def _time(
    fn: Callable[[], list[tuple[Any, ...]]], reps: int
) -> tuple[float, list[tuple[Any, ...]]]:
    times = []
    rows: list[tuple[Any, ...]] = []
    for _ in range(reps):
        start = time.perf_counter()
        rows = fn()
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times), rows


def _fetch_duck(con: duckdb.DuckDBPyConnection, query: str) -> list[tuple[Any, ...]]:
    return con.execute(query).fetchall()


def _fetch_pg(con: psycopg.Connection[Any], query: str) -> list[tuple[Any, ...]]:
    return con.execute(query.encode()).fetchall()  # fixed TPC text; bytes skip % placeholders


def _geomean(values: list[float]) -> float | None:
    return round(math.exp(sum(math.log(v) for v in values) / len(values)), 3) if values else None


def run(
    root: Path,
    dsn: str,
    bench: str,
    sf: float,
    reps: int = 3,
    timeout_s: float = 30.0,
    only: list[int] | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    spec = _check(bench, sf)
    with duckdb.connect() as catalog:
        catalog.execute(f"INSTALL {bench}")
        catalog.execute(f"LOAD {bench}")
        queries = [
            (int(n), q)
            for n, q in catalog.execute(spec.queries).fetchall()
            if not only or n in only
        ]

    results = []
    with (
        duckdb.connect(str(duckdb_path(root, bench, sf)), read_only=True) as duck,
        psycopg.connect(dsn, autocommit=True) as pg,
    ):
        pg.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name(bench, sf))))
        pg.execute(
            sql.SQL("SET statement_timeout = {}").format(sql.Literal(f"{int(timeout_s * 1000)}ms"))
        )
        versions = {"duckdb": duckdb.__version__, "postgres": pg.info.server_version}
        for i, (nr, query) in enumerate(queries, start=1):
            if progress:
                progress(f"Query {nr} ({i} of {len(queries)})")
            entry: dict[str, Any] = {
                "nr": nr,
                "duckdb_ms": None,
                "postgres_ms": None,
                "rows": None,
                "agree": None,
                "error": None,
            }
            errors = []
            duck_rows = pg_rows = None
            timer = threading.Timer(timeout_s * reps, duck.interrupt)
            timer.start()
            try:
                ms, duck_rows = _time(partial(_fetch_duck, duck, query), reps)
                entry["duckdb_ms"], entry["rows"] = round(ms, 3), len(duck_rows)
            except duckdb.Error as e:
                errors.append(f"DuckDB: {str(e).splitlines()[0]}")
            finally:
                timer.cancel()
            try:
                translated = to_postgres(query)
                ms, pg_rows = _time(partial(_fetch_pg, pg, translated), reps)
                entry["postgres_ms"] = round(ms, 3)
            except (psycopg.Error, sqlglot.errors.SqlglotError) as e:
                message = getattr(getattr(e, "diag", None), "message_primary", None) or str(e)
                errors.append(f"PostgreSQL: {message.splitlines()[0]}")
            if duck_rows is not None and pg_rows is not None:
                entry["agree"] = _rows(duck_rows) == _rows(pg_rows)
            entry["error"] = "; ".join(errors) or None
            results.append(entry)

    both = [q for q in results if q["duckdb_ms"] and q["postgres_ms"]]
    duck_mean = _geomean([q["duckdb_ms"] for q in both])
    pg_mean = _geomean([q["postgres_ms"] for q in both])
    return {
        "benchmark": bench,
        "name": spec.name,
        "scale": sf,
        "reps": reps,
        "ran_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "versions": versions,
        "queries": results,
        "summary": {
            "queries": len(results),
            "agreed": sum(1 for q in results if q["agree"]),
            "failed": sum(1 for q in results if q["error"]),
            "geomean_duckdb_ms": duck_mean,
            "geomean_postgres_ms": pg_mean,
            "duckdb_faster": sum(1 for q in both if q["duckdb_ms"] < q["postgres_ms"]),
            "compared": len(both),
        },
    }
