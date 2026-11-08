"""The read-only engine must hold even when SQL skips the parser guard."""

from pathlib import Path

import duckdb
import pytest

from schemalab.guards.engine import open_readonly


def test_reads_tables(sample_db: Path) -> None:
    con = open_readonly(sample_db)
    assert con.execute("SELECT count(*) FROM orders").fetchone() == (100,)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders VALUES (1, 1, 1.0)",
        "CREATE TABLE x (a INT)",
        "DROP TABLE orders",
        "UPDATE orders SET amount = 0",
    ],
)
def test_rejects_writes(sample_db: Path, sql: str) -> None:
    with pytest.raises(duckdb.Error):
        open_readonly(sample_db).execute(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('{f}')",
        "SELECT * FROM '{f}'",
        "SELECT content FROM read_text('{f}')",
        "COPY orders TO '{f}.out'",
        "ATTACH '{f}.db' AS other",
    ],
)
def test_blocks_file_access(sample_db: Path, tmp_path: Path, sql: str) -> None:
    secret = tmp_path / "secret.csv"
    secret.write_text("password\nhunter2\n")
    with pytest.raises(duckdb.Error):
        open_readonly(sample_db).execute(sql.format(f=secret))


@pytest.mark.parametrize(
    "sql",
    [
        "SET enable_external_access = true",
        "RESET enable_external_access",
        "INSTALL httpfs",
        "LOAD httpfs",
        # Only lock_configuration stops a query from raising its own resource limits.
        "SET memory_limit = '64GB'",
        "SET threads = 64",
    ],
)
def test_settings_and_extensions_are_locked(sample_db: Path, sql: str) -> None:
    with pytest.raises(duckdb.Error):
        open_readonly(sample_db).execute(sql)
