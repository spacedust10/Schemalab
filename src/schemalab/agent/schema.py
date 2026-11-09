"""What the model is told about the data: table and column structure, and row values only as
category lists when the caller allows it.

Category values are the distinct values of short text columns, such as 'Male Only' or
'Under 65 years'. Without them the model guesses spellings ('Male', 'Under 65') and filters
match nothing; that was the main cause of wrong answers in the first eval run.
"""

from collections.abc import Collection
from pathlib import Path

import duckdb

from schemalab.guards.engine import open_readonly

MAX_CATEGORIES = 100  # more distinct values than this is free text or an id, not a category
MAX_VALUE_CHARS = 60


def describe_schema(
    db_path: Path, tables: Collection[str] | None = None, categories: bool = False
) -> str:
    """One block per table: name, row count, then one `column TYPE` line per column."""
    with open_readonly(db_path) as con:
        columns = con.execute(
            # comment: key notes from .sql uploads, such as "references customers(id)".
            "SELECT table_name, column_name, data_type, comment FROM duckdb_columns() "
            "WHERE schema_name = 'main' AND database_name = current_database() "
            "ORDER BY table_name, column_index"
        ).fetchall()
        blocks: list[str] = []
        current = None
        for table, column, dtype, comment in columns:
            if tables is not None and table not in tables:
                continue
            if table != current:
                # Trusted code: the name comes from the catalog, not from the model.
                (count,) = con.execute(f'SELECT count(*) FROM "{table}"').fetchone() or (0,)  # noqa: S608
                blocks.append(f"{table} ({count} rows)")
                current = table
            notes = [comment] if comment else []
            if categories and dtype == "VARCHAR" and (values := _categories(con, table, column)):
                notes.append("values: " + ", ".join(values))
            blocks[-1] += f"\n  {column} {dtype}" + (f" -- {'; '.join(notes)}" if notes else "")
    return "\n\n".join(blocks)


def _categories(con: duckdb.DuckDBPyConnection, table: str, column: str) -> list[str] | None:
    """The column's distinct values as SQL literals, or None if it is not a short category."""
    # Names come from the catalog and are quoted; one row past the limit is enough to know.
    rows = con.execute(
        f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL '  # noqa: S608
        f"ORDER BY 1 LIMIT {MAX_CATEGORIES + 1}"
    ).fetchall()
    values = [r[0] for r in rows]
    if not values or len(values) > MAX_CATEGORIES or max(map(len, values)) > MAX_VALUE_CHARS:
        return None
    return ["'" + v.replace("'", "''") + "'" for v in values]
