"""One DuckLake per user: bronze from dlt, silver and gold from the user's SELECT models.

Each lake is a schema in the Postgres catalog and a prefix in object storage, both named
after the user (`u<id>`). Silver and gold are copied to a DuckDB file after every change, and
Ask reads that file through the API's locked engine, so the API never holds lake credentials.
"""

import json
import os
import re
import tempfile
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import dlt
import duckdb

from ingest.config import Settings
from ingest.sources import USGS_START, quake_rows, usgs_url

NAME = re.compile(r"u[0-9]{1,12}")
TABLE = re.compile(r"[a-z_][a-z0-9_]{0,62}")
LAYERS = ("bronze", "silver", "gold")
PREVIEW_ROWS = 50
# Scalar functions that read the host: the environment holds the catalog password and S3 key.
_FORBIDDEN = frozenset({"getenv", "current_setting", "read_text", "read_blob"})


# Attaching a new lake creates its catalog schema; two requests doing that at once collide in
# Postgres. ponytail: per-process locks, enough for one ingest container; use a Postgres
# advisory lock if ingest ever runs as several replicas.
_ATTACH: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


class LakeError(ValueError):
    """A request this lake refuses, or a model that failed; the message is safe to show."""


def _check_name(name: str) -> None:
    if not NAME.fullmatch(name):
        raise LakeError("No such lake.")


def _data_path(s: Settings, name: str) -> str:
    return f"{s.data_root.rstrip('/')}/{name}/"


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@contextmanager
def connect(s: Settings, name: str) -> Iterator[duckdb.DuckDBPyConnection]:
    """The user's lake attached as `lake`, its three layers created if new."""
    _check_name(name)
    con = duckdb.connect()
    try:
        if s.s3:
            con.execute(
                f"CREATE SECRET lake_s3 (TYPE s3, KEY_ID {_quote(s.s3.key)}, "
                f"SECRET {_quote(s.s3.secret)}, ENDPOINT {_quote(s.s3.endpoint)}, "
                f"REGION {_quote(s.s3.region)}, URL_STYLE 'path', USE_SSL false, "
                f"SCOPE {_quote(s.data_root)})"
            )
        with _ATTACH[name]:
            con.execute(
                f"ATTACH {_quote('ducklake:postgres:' + s.catalog)} AS lake "
                f"(DATA_PATH {_quote(_data_path(s, name))}, METADATA_SCHEMA {_quote(name)})"
            )
            for layer in LAYERS:
                con.execute(f"CREATE SCHEMA IF NOT EXISTS lake.{layer}")
        yield con
    finally:
        con.close()


def _tables(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    rows = con.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = 'lake' "
        "AND schema_name IN ('bronze', 'silver', 'gold') AND NOT starts_with(table_name, '_dlt')"
    ).fetchall()
    return sorted(rows, key=lambda r: (LAYERS.index(r[0]), r[1]))


def tables(s: Settings, name: str) -> list[dict[str, Any]]:
    with connect(s, name) as con:
        out = []
        for layer, table in _tables(con):
            columns = con.execute(
                "SELECT column_name, data_type FROM duckdb_columns() WHERE database_name = 'lake' "
                "AND schema_name = ? AND table_name = ? ORDER BY column_index",
                [layer, table],
            ).fetchall()
            out.append(
                {
                    "layer": layer,
                    "name": table,
                    "rows": _count(con, f"lake.{layer}.{table}"),
                    "columns": [
                        {"name": c, "type": t} for c, t in columns if not c.startswith("_dlt")
                    ],
                }
            )
        return out


def _count(con: duckdb.DuckDBPyConnection, relation: str) -> int:
    row = con.execute(f"SELECT count(*) FROM {relation}").fetchone()  # noqa: S608 (checked names)
    return int(row[0]) if row else 0


def preview(s: Settings, name: str, layer: str, table: str, version: int | None) -> dict[str, Any]:
    with connect(s, name) as con:
        if (layer, table) not in _tables(con):
            raise LakeError("No such table.")
        at = f" AT (VERSION => {int(version)})" if version is not None else ""
        relation = f"lake.{layer}.{table}{at}"
        try:
            rel = con.sql(f"SELECT * FROM {relation} LIMIT {PREVIEW_ROWS}")  # noqa: S608
            columns = [c for c in rel.columns if not c.startswith("_dlt")]
            keep = [i for i, c in enumerate(rel.columns) if not c.startswith("_dlt")]
            rows = [[_plain(r[i]) for i in keep] for r in rel.fetchall()]
            return {"columns": columns, "rows": rows, "total": _count(con, relation)}
        except duckdb.Error as e:
            if version is None:
                raise LakeError("That table could not be read.") from e
            raise LakeError(f"That table did not exist at version {version}.") from e


def snapshots(s: Settings, name: str) -> list[dict[str, Any]]:
    with connect(s, name) as con:
        rows = con.execute(
            "SELECT snapshot_id, snapshot_time, changes FROM lake.snapshots() "
            "ORDER BY snapshot_id DESC LIMIT 50"
        ).fetchall()
        return [{"id": i, "at": _plain(at), "changes": _plain(ch)} for i, at, ch in rows]


def newest_snapshot(s: Settings, name: str) -> int:
    """The lake's latest snapshot: what a run just made, so the page can link the two."""
    with connect(s, name) as con:
        row = con.execute("SELECT max(snapshot_id) FROM lake.snapshots()").fetchone()
        return int(row[0]) if row and row[0] is not None else 0


def _plain(value: Any) -> Any:
    """A value JSON can carry."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, int | float | str | bool) or value is None:
        return value
    return str(value)


# Loading: dlt writes bronze.


def _destination(s: Settings, name: str) -> Any:
    storage: dict[str, Any] = {"bucket_url": _data_path(s, name)}
    if s.s3:
        storage["credentials"] = {
            "aws_access_key_id": s.s3.key,
            "aws_secret_access_key": s.s3.secret,
            "endpoint_url": f"http://{s.s3.endpoint}",
            "region_name": s.s3.region,
        }
    # dlt resolves this dict into its DuckLakeCredentials; its stubs want the class itself.
    credentials: Any = {
        "ducklake_name": "lake",
        "catalog": s.catalog,
        "metadata_schema": name,
        "storage": storage,
    }
    return dlt.destinations.ducklake(credentials=credentials)


def _run(s: Settings, name: str, source: str, resource: Any) -> dict[str, int]:
    with connect(s, name):  # makes the lake, with our data path, before dlt sees it
        pass
    pipeline = dlt.pipeline(
        pipeline_name=f"{name}_{source}",
        destination=_destination(s, name),
        dataset_name="bronze",
        pipelines_dir=str(s.work_dir / name),
    )
    try:
        pipeline.run(resource)
    except Exception as e:
        raise LakeError(f"The load failed: {_cause(e)}") from e
    trace = pipeline.last_trace
    info = trace.last_normalize_info if trace else None
    counts = info.row_counts if info else {}
    publish(s, name)
    return {t: n for t, n in counts.items() if not t.startswith("_dlt")}


def _cause(e: BaseException) -> str:
    while e.__cause__ is not None:
        e = e.__cause__
    return str(e).splitlines()[0][:300] if str(e) else type(e).__name__


def load_file(s: Settings, name: str, path: Path, table: str) -> dict[str, int]:
    """A CSV or Parquet file, read by DuckDB so types match the rest of Schemalab."""
    _check_name(name)
    if not TABLE.fullmatch(table):
        raise LakeError("Table names use lowercase letters, digits and underscores.")
    reader = duckdb.read_parquet if path.suffix == ".parquet" else duckdb.read_csv
    try:
        data = reader(str(path)).to_arrow_table()
    except duckdb.Error as e:
        raise LakeError(f"The file could not be read: {str(e).splitlines()[0]}") from e
    return _run(s, name, table, dlt.resource(data, name=table, write_disposition="replace"))


def load_sheet(s: Settings, name: str, csv: bytes, table: str) -> dict[str, int]:
    """A Google Sheet's CSV export, already downloaded by the caller."""
    s.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=s.work_dir, suffix=".csv") as f:
        f.write(csv)
        f.flush()
        return load_file(s, name, Path(f.name), table)


def load_quakes(s: Settings, name: str, fetch: Callable[[str], dict[str, Any]]) -> dict[str, int]:
    """USGS earthquakes, merged by id; each run asks only for events after the newest one."""

    @dlt.resource(name="quakes", write_disposition="merge", primary_key="id")
    def quakes(
        time: dlt.sources.incremental[datetime] = dlt.sources.incremental("time"),  # noqa: B008
    ) -> Iterator[list[dict[str, Any]]]:
        since = time.start_value or datetime.now(UTC) - USGS_START
        yield quake_rows(fetch(usgs_url(since)))

    return _run(s, name, "quakes", quakes())


# Transforming: the user's SELECTs build silver and gold.


def _check_model(sql: str, allowed: set[str]) -> None:
    """DuckDB's own parse of the model: a second check after the API's SQL guard."""
    statements = duckdb.extract_statements(sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise LakeError("A model is one SELECT statement.")
    tree = json.loads(duckdb.sql("SELECT json_serialize_sql(?)", params=[sql]).fetchone()[0])  # type: ignore[index]
    if tree.get("error"):
        raise LakeError(tree.get("error_message", "The model could not be parsed."))
    ctes: set[str] = set()

    def walk(node: Any) -> Iterator[dict[str, Any]]:
        if isinstance(node, dict):
            yield node
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    nodes = list(walk(tree))
    for n in nodes:
        if isinstance(n.get("cte_map"), dict):
            ctes |= {c["key"].lower() for c in n["cte_map"]["map"]}
    for n in nodes:
        if n.get("type") == "TABLE_FUNCTION":
            raise LakeError("Models cannot call table functions.")
        if n.get("type") == "BASE_TABLE":
            if n.get("catalog_name") or n.get("schema_name"):
                raise LakeError("Name tables as layer_table, like bronze_orders.")
            if n["table_name"].lower() not in allowed | ctes:
                raise LakeError(f"Unknown table: {n['table_name']}")
        if n.get("class") == "FUNCTION" and n.get("function_name", "").lower() in _FORBIDDEN:
            raise LakeError(f"Models cannot call {n['function_name']}.")


def _gate(con: duckdb.DuckDBPyConnection, target: str, m: dict[str, Any]) -> None:
    """The model's data quality checks on the table it just built, inside the run's transaction.

    A failure raises, the run rolls back, and no snapshot is made. ponytail: single-column
    unique and a full scan per check; composite keys or sampling when a model needs them.
    """
    table = f"lake.{target}"
    try:
        empty = con.execute(f"SELECT * FROM {table} LIMIT 0")  # noqa: S608 (checked names)
        have = {d[0].lower() for d in empty.description}
        for c in [*m.get("not_null", []), *m.get("unique", [])]:
            if c not in have:
                raise LakeError(f"{target}: there is no column {c} to check")
        for c in m.get("not_null", []):
            if n := _count(con, f'{table} WHERE "{c}" IS NULL'):
                rows = "1 row has" if n == 1 else f"{n} rows have"
                raise LakeError(f"{target}: {rows} no {c}, but {c} must never be empty")
        for c in m.get("unique", []):
            # NULL is no value, so it never repeats (as in dbt's unique test).
            dupes = f'(SELECT 1 FROM {table} WHERE "{c}" IS NOT NULL GROUP BY "{c}" HAVING count(*) > 1)'  # noqa: S608, E501 (checked names)
            if n := _count(con, dupes):
                values = f"1 value of {c} appears" if n == 1 else f"{n} values of {c} appear"
                raise LakeError(f"{target}: {values} more than once, but {c} must be unique")
    except duckdb.Error as e:
        raise LakeError(f"{target}: {_cause(e)}") from e


def transform(s: Settings, name: str, models: list[dict[str, Any]]) -> dict[str, int]:
    """Runs every model in order in one transaction: one snapshot, or nothing on failure."""
    for m in models:
        if m["layer"] not in ("silver", "gold") or not TABLE.fullmatch(m["name"]):
            raise LakeError("A model needs a silver or gold layer and a lowercase table name.")
        if not all(TABLE.fullmatch(c) for c in [*m.get("not_null", []), *m.get("unique", [])]):
            raise LakeError("Name check columns in lowercase, like order_id.")
    counts: dict[str, int] = {}
    with connect(s, name) as con:
        allowed: set[str] = set()
        for layer, table in _tables(con):
            if layer != "gold":
                con.execute(f"CREATE TEMP VIEW {layer}_{table} AS FROM lake.{layer}.{table}")
                allowed.add(f"{layer}_{table}")
        _lock(con, s, name)
        con.execute("BEGIN")
        try:
            for m in models:
                target = f"{m['layer']}.{m['name']}"
                try:
                    _check_model(m["sql"], allowed)
                    con.execute(f"CREATE OR REPLACE TABLE lake.{target} AS {m['sql']}")
                except (duckdb.Error, LakeError) as e:
                    raise LakeError(f"{target}: {str(e).splitlines()[0][:300]}") from e
                if m["layer"] == "silver":
                    con.execute(
                        f"CREATE OR REPLACE TEMP VIEW silver_{m['name']} AS FROM lake.{target}"
                    )
                    allowed.add(f"silver_{m['name']}")
                counts[target] = _count(con, f"lake.{target}")
                _gate(con, target, m)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    publish(s, name)
    return counts


def _lock(con: duckdb.DuckDBPyConnection, s: Settings, name: str) -> None:
    con.execute(f"SET allowed_directories = [{_quote(_data_path(s, name))}]")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")


def publish(s: Settings, name: str) -> None:
    """Copies silver and gold to <publish_dir>/<name>.duckdb, swapped in whole."""
    s.publish_dir.mkdir(parents=True, exist_ok=True)
    final = s.publish_dir / f"{name}.duckdb"
    fd, tmp = tempfile.mkstemp(dir=s.publish_dir, suffix=".tmp")
    os.close(fd)
    os.unlink(tmp)  # DuckDB makes the file itself
    try:
        with connect(s, name) as con:
            con.execute(f"ATTACH {_quote(tmp)} AS pub")
            for layer, table in _tables(con):
                if layer != "bronze":
                    con.execute(f"CREATE TABLE pub.{layer}_{table} AS FROM lake.{layer}.{table}")
            con.execute("DETACH pub")
        os.replace(tmp, final)
    finally:
        Path(tmp).unlink(missing_ok=True)
        Path(tmp + ".wal").unlink(missing_ok=True)
