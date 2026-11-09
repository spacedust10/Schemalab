"""What the model is told about the data: table and column structure, never row values."""

from collections.abc import Collection
from pathlib import Path

from schemalab.guards.engine import open_readonly


def describe_schema(db_path: Path, tables: Collection[str] | None = None) -> str:
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
            blocks[-1] += f"\n  {column} {dtype}" + (f" -- {comment}" if comment else "")
    return "\n\n".join(blocks)
