"""What the model is told about the data: table and column structure, never row values."""

from collections.abc import Collection
from pathlib import Path

from schemalab.guards.engine import open_readonly


def describe_schema(db_path: Path, tables: Collection[str] | None = None) -> str:
    """One block per table: name, row count, then one `column TYPE` line per column."""
    with open_readonly(db_path) as con:
        columns = con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position"
        ).fetchall()
        blocks: list[str] = []
        current = None
        for table, column, dtype in columns:
            if tables is not None and table not in tables:
                continue
            if table != current:
                # Trusted code: the name comes from the catalog, not from the model.
                (count,) = con.execute(f'SELECT count(*) FROM "{table}"').fetchone() or (0,)  # noqa: S608
                blocks.append(f"{table} ({count} rows)")
                current = table
            blocks[-1] += f"\n  {column} {dtype}"
    return "\n\n".join(blocks)
