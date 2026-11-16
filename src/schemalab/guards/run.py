"""Run model-written SQL: parser guard, then the read-only engine, with row and time limits."""

import threading
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from schemalab.guards.engine import open_readonly
from schemalab.guards.joins import UnplacedColumnError
from schemalab.guards.joins import check_joins as joins_allowed
from schemalab.guards.sql import check_sql


class QueryTimeoutError(TimeoutError):
    """The query ran past its time budget and was interrupted."""


@dataclass(frozen=True)
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool
    joins: list[str] = field(default_factory=list)  # as "a.key = b.key", when checked
    joins_checked: bool = False  # False: not asked, or the database declares no joins


def run_query(
    db_path: Path,
    sql: str,
    allowed_tables: Collection[str] | None = None,
    max_rows: int = 1000,
    timeout_s: float = 10.0,
    check_joins: bool = False,
) -> QueryResult:
    """`check_joins`: also refuse joins the database does not declare (the analyst's queries)."""
    from schemalab.joingraph import join_graph, schema_of  # joingraph reads the registry

    with open_readonly(db_path) as con:
        if allowed_tables is None:
            allowed_tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        safe_sql = check_sql(sql, allowed_tables)
        joins: list[str] = []
        graph = join_graph(con) if check_joins else set()
        if graph:  # a database that declares no joins leaves them unchecked, and says so
            try:
                joins = joins_allowed(safe_sql, schema_of(con), graph)
            except UnplacedColumnError:
                # A made-up column: DuckDB's binder says which. EXPLAIN only plans, and it
                # gets the same time limit as the query.
                timer = threading.Timer(timeout_s, con.interrupt)
                timer.start()
                try:
                    con.execute(f"EXPLAIN {safe_sql}")
                finally:
                    timer.cancel()
                raise

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
        return QueryResult(
            safe_sql, columns, rows[:max_rows], len(rows) > max_rows, joins, bool(graph)
        )
