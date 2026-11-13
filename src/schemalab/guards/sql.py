"""Parser guard for model-written SQL: the first line of defense.

A query passes only if it is one read-only SELECT (or set operation) over tables on an
allow-list. Everything else is rejected before it reaches DuckDB. The read-only engine in
`engine.py` backs this up in case the parser and DuckDB ever disagree.
"""

import re
from collections.abc import Collection
from typing import Any

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
    except sqlglot.errors.SqlglotError as e:  # ParseError, and TokenError for an unclosed quote
        # sqlglot underlines the bad token with terminal escape codes; keep the plain text.
        plain = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
        raise UnsafeSQLError(f"could not parse SQL: {plain}") from e
    # A trailing comment after the last ';' parses as a bare Semicolon node.
    statements = [s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)]

    if len(statements) != 1:
        raise UnsafeSQLError(f"expected exactly one statement, got {len(statements)}")
    tree = statements[0]

    if not isinstance(tree, _READ_ROOTS):
        raise UnsafeSQLError(f"only SELECT queries are allowed, got {tree.key.upper()}")

    for node in tree.walk():
        # LATERAL may wrap a subquery; around anything else it is a table function call
        # (LATERAL read_csv(...)) that the table check below would never see.
        if isinstance(node, exp.Lateral) and not isinstance(node.this, exp.Subquery):
            raise UnsafeSQLError("LATERAL is only allowed around a subquery")
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
    """CTE names in scope at `node`, following SQL's scoping rules.

    - A CTE is visible inside the query that owns its WITH, including nested subqueries,
      but not outside it.
    - Inside a CTE body, only CTEs defined earlier in the same WITH are visible, plus the
      CTE itself when the WITH is RECURSIVE. Otherwise a name like `customers` inside
      `WITH customers AS (SELECT * FROM customers)` means the real table.
    """
    names: set[str] = set()
    child: Any = node  # sqlglot types parents as Expr, nodes as Expression
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.With):
            for cte in parent.expressions:
                if cte is child:
                    if parent.args.get("recursive"):
                        names.add(cte.alias_or_name.lower())
                    break
                names.add(cte.alias_or_name.lower())
        else:
            with_ = parent.args.get("with_")
            if isinstance(with_, exp.With) and child is not with_:
                names.update(cte.alias_or_name.lower() for cte in with_.expressions)
        child, parent = parent, parent.parent
    return names
