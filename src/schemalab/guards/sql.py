"""Parser guard for model-written SQL: the first line of defense.

A query passes only if it is one read-only SELECT (or set operation) over tables on an
allow-list. Everything else is rejected before it reaches DuckDB. The read-only engine in
`engine.py` backs this up in case the parser and DuckDB ever disagree.
"""

from collections.abc import Collection

import sqlglot
from sqlglot import exp

_READ_ROOTS = (exp.Select, exp.SetOperation)

# Node types that change data or the session, wherever they appear in the tree.
_FORBIDDEN_NODES = (
    exp.DML,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Command,
    exp.Pragma,
    exp.Set,
    exp.Attach,
    exp.Detach,
    exp.Describe,
    exp.Transaction,
)

# Scalar functions that read host state. File-reading table functions are caught by the
# table check instead, since any function in FROM is rejected.
_FORBIDDEN_FUNCTIONS = frozenset({"getenv", "current_setting", "read_text", "read_blob"})


class UnsafeSQLError(ValueError):
    """The SQL is not a single read-only query over allowed tables."""


def check_sql(sql: str, allowed_tables: Collection[str]) -> str:
    """Return the normalized SQL to run, or raise UnsafeSQLError saying why not."""
    try:
        parsed = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQLError(f"could not parse SQL: {e}") from e
    # A trailing comment after the last ';' parses as a bare Semicolon node.
    statements = [s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)]

    if len(statements) != 1:
        raise UnsafeSQLError(f"expected exactly one statement, got {len(statements)}")
    tree = statements[0]

    if not isinstance(tree, _READ_ROOTS):
        raise UnsafeSQLError(f"only SELECT queries are allowed, got {tree.key.upper()}")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise UnsafeSQLError(f"{node.key.upper()} is not allowed")
        if isinstance(node, exp.Func) and node.name.lower() in _FORBIDDEN_FUNCTIONS:
            raise UnsafeSQLError(f"function {node.name} is not allowed")

    _check_tables(tree, allowed_tables)
    return tree.sql(dialect="duckdb")


def _check_tables(tree: exp.Expression, allowed_tables: Collection[str]) -> None:
    allowed = {t.lower() for t in allowed_tables}

    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise UnsafeSQLError(f"table functions are not allowed: {table.this.sql('duckdb')}")
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeSQLError(f"qualified table names are not allowed: {table.sql('duckdb')}")
        name = table.name.lower()
        if name not in allowed and name not in _visible_ctes(table):
            raise UnsafeSQLError(f"unknown table: {table.name}")


def _visible_ctes(node: exp.Expression) -> set[str]:
    """CTE names in scope at `node`: those on the WITH of any enclosing query.

    A CTE defined inside a subquery is not visible outside it, so collecting CTE names from
    the whole tree would let an inner `WITH customers AS (...)` unlock a real outer table.
    """
    names: set[str] = set()
    parent = node.parent
    while parent is not None:
        with_ = parent.args.get("with_")
        if isinstance(with_, exp.With):
            names.update(cte.alias_or_name.lower() for cte in with_.expressions)
        parent = parent.parent
    return names
