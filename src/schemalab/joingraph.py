"""The joins a database allows: the graph the join guard checks the analyst's SQL against.

Two sources, both declared rather than guessed:
- the sample-dataset registry, for the built-in data (never across datasets);
- foreign keys from .sql uploads, kept in each table's comment by `uploads`.
CSV uploads and the lakehouse declare no keys, so their graph is empty and joins go unchecked.
"""

import json
from typing import Any

import duckdb

from schemalab.datasets import REGISTRY
from schemalab.guards.joins import Edge, edge, text
from schemalab.uploads import KEYS_COMMENT


def schema_of(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, str]]:
    """Each table's columns and types, lowercase, for resolving unqualified columns."""
    out: dict[str, dict[str, str]] = {}
    for table, column, dtype in con.execute(
        "SELECT table_name, column_name, data_type FROM duckdb_columns() "
        "WHERE schema_name = 'main' AND database_name = current_database() "
        "ORDER BY table_name, column_index"
    ).fetchall():
        out.setdefault(table.lower(), {})[column.lower()] = dtype
    return out


def join_graph(con: duckdb.DuckDBPyConnection) -> set[Edge]:
    columns = {t: set(cols) for t, cols in schema_of(con).items()}

    def exists(table: str, column: str) -> bool:
        return column.lower() in columns.get(table.lower(), set())

    graph: set[Edge] = set()
    for ds in REGISTRY.values():
        for ta, ca, tb, cb in ds.joins:
            if exists(ta, ca) and exists(tb, cb):
                graph.add(edge(ta, ca, tb, cb))
    for table, comment in con.execute(
        "SELECT table_name, comment FROM duckdb_tables() "
        "WHERE schema_name = 'main' AND database_name = current_database()"
    ).fetchall():
        for own, parent, theirs in _foreign(comment):
            for ca, cb in zip(own, theirs, strict=False):
                if exists(table, ca) and exists(parent, cb):
                    graph.add(edge(table, ca, parent, cb))
    return graph


def _foreign(comment: str | None) -> list[tuple[list[str], str, list[str]]]:
    if not (comment or "").startswith(KEYS_COMMENT):
        return []
    try:
        keys: Any = json.loads((comment or "")[len(KEYS_COMMENT) :])
        return [(list(own), str(parent), list(theirs)) for own, parent, theirs in keys["foreign"]]
    except (ValueError, KeyError, TypeError):
        return []  # a hand-edited or older comment: no joins from it


def describe_joins(graph: set[Edge]) -> str:
    """The prompt's list of allowed joins; empty when the database declares none."""
    if not graph:
        return ""
    return "Joins you may use (no others will run):\n" + "\n".join(
        f"  {text(e)}" for e in sorted(graph)
    )
