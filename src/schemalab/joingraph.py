"""The joins a database allows: the graph the join guard checks the analyst's SQL against.

Two sources, both declared rather than guessed:
- the sample-dataset registry, for the built-in data (never across datasets);
- foreign keys from .sql uploads, kept in each table's comment by `uploads`.
CSV uploads and the lakehouse declare no keys, so their graph is empty and joins go unchecked.
"""

import json
from pathlib import Path
from typing import Any

import duckdb

from schemalab.datasets import REGISTRY
from schemalab.guards.joins import Edge, edge
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


def join_graph(con: duckdb.DuckDBPyConnection) -> dict[Edge, str]:
    """Each allowed join, mapped to how the prompt shows it: the schema's own spelling, and
    what the join means when the registry says. Registry joins come first, in their order."""
    spelled: dict[tuple[str, str], tuple[str, str]] = {}
    for table, column in con.execute(
        "SELECT table_name, column_name FROM duckdb_columns() "
        "WHERE schema_name = 'main' AND database_name = current_database()"
    ).fetchall():
        spelled[(table.lower(), column.lower())] = (table, column)

    def line(ta: str, ca: str, tb: str, cb: str, note: str = "") -> str | None:
        a, b = spelled.get((ta.lower(), ca.lower())), spelled.get((tb.lower(), cb.lower()))
        if a is None or b is None:
            return None
        return f"{a[0]}.{a[1]} = {b[0]}.{b[1]}" + (f" -- {note}" if note else "")

    graph: dict[Edge, str] = {}
    for ds in REGISTRY.values():
        for ta, ca, tb, cb, note in ds.joins:
            if shown := line(ta, ca, tb, cb, note):
                graph.setdefault(edge(ta, ca, tb, cb), shown)
    for table, comment in con.execute(
        "SELECT table_name, comment FROM duckdb_tables() "
        "WHERE schema_name = 'main' AND database_name = current_database()"
    ).fetchall():
        for own, parent, theirs in _foreign(comment):
            for ca, cb in zip(own, theirs, strict=False):
                if shown := line(table, ca, parent, cb):
                    graph.setdefault(edge(table, ca, parent, cb), shown)
    return graph


def _foreign(comment: str | None) -> list[tuple[list[str], str, list[str]]]:
    if not (comment or "").startswith(KEYS_COMMENT):
        return []
    try:
        keys: Any = json.loads((comment or "")[len(KEYS_COMMENT) :])
        return [(list(own), str(parent), list(theirs)) for own, parent, theirs in keys["foreign"]]
    except (ValueError, KeyError, TypeError):
        return []  # a hand-edited or older comment: no joins from it


def describe_joins(graph: dict[Edge, str]) -> str:
    """The prompt's list of allowed joins; empty when the database declares none."""
    if not graph:
        return ""
    head = (
        "How tables join. Join only when the question needs columns from both; "
        "no other join will run:"
    )
    return "\n".join([head, *(f"  {shown}" for shown in graph.values())])


def joins_block(db_path: Path) -> str:
    """The allowed-joins block for the analyst's schema prompt."""
    from schemalab.guards.engine import open_readonly

    with open_readonly(db_path) as con:
        return describe_joins(join_graph(con))
