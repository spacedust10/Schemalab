"""Run model-written SQL: parser guard, then the read-only engine, with row and time limits."""

import threading
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from schemalab.guards.engine import open_readonly
from schemalab.guards.sql import check_sql


class QueryTimeoutError(TimeoutError):
    """The query ran past its time budget and was interrupted."""


@dataclass(frozen=True)
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool


def run_query(
    db_path: Path,
    sql: str,
    allowed_tables: Collection[str] | None = None,
    max_rows: int = 1000,
    timeout_s: float = 10.0,
) -> QueryResult:
    with open_readonly(db_path) as con:
        if allowed_tables is None:
            allowed_tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        safe_sql = check_sql(sql, allowed_tables)

        timer = threading.Timer(timeout_s, con.interrupt)
        timer.start()
        try:
            cursor = con.execute(safe_sql)
            rows = cursor.fetchmany(max_rows + 1)
        except duckdb.InterruptException as e:
            raise QueryTimeoutError(f"query ran longer than {timeout_s}s") from e
        finally:
            timer.cancel()

        columns = [d[0] for d in cursor.description or []]
        return QueryResult(safe_sql, columns, rows[:max_rows], len(rows) > max_rows)
