from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    """A small DuckDB file with two tables, built with a normal writable connection."""
    path = tmp_path / "sample.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE orders (id INTEGER, customer_id INTEGER, amount DOUBLE)")
        con.execute("INSERT INTO orders SELECT i, i % 10, i * 1.5 FROM range(1, 101) t(i)")
        con.execute("CREATE TABLE customers (id INTEGER, name VARCHAR)")
        con.execute("INSERT INTO customers SELECT i, 'c' || i FROM range(10) t(i)")
    return path
