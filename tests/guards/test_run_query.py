"""Guarded execution: guard first, then the read-only engine, with limits."""

import time
from pathlib import Path

import pytest

from schemalab.guards.run import QueryTimeoutError, run_query
from schemalab.guards.sql import UnsafeSQLError


def test_returns_columns_and_rows(sample_db: Path) -> None:
    result = run_query(sample_db, "SELECT id, name FROM customers ORDER BY id LIMIT 2")
    assert result.columns == ["id", "name"]
    assert result.rows == [(0, "c0"), (1, "c1")]
    assert result.truncated is False


def test_reports_the_sql_that_ran(sample_db: Path) -> None:
    assert run_query(sample_db, "select id from orders limit 1;").sql == (
        "SELECT id FROM orders LIMIT 1"
    )


def test_allows_every_table_in_the_database_by_default(sample_db: Path) -> None:
    assert run_query(sample_db, "SELECT count(*) FROM customers").rows == [(10,)]


def test_rejects_unsafe_sql_before_running_it(sample_db: Path) -> None:
    with pytest.raises(UnsafeSQLError):
        run_query(sample_db, "DROP TABLE orders")


def test_respects_a_narrower_allow_list(sample_db: Path) -> None:
    with pytest.raises(UnsafeSQLError, match="customers"):
        run_query(sample_db, "SELECT * FROM customers", allowed_tables={"orders"})


def test_caps_rows_and_flags_truncation(sample_db: Path) -> None:
    result = run_query(sample_db, "SELECT * FROM orders", max_rows=7)
    assert len(result.rows) == 7
    assert result.truncated is True


def test_exact_row_count_is_not_truncated(sample_db: Path) -> None:
    assert run_query(sample_db, "SELECT * FROM customers", max_rows=10).truncated is False


def test_interrupts_slow_queries(sample_db: Path) -> None:
    # 100^5 = 10 billion joined rows: far longer than the budget.
    slow = "SELECT count(*) FROM orders a, orders b, orders c, orders d, orders e"
    start = time.monotonic()
    with pytest.raises(QueryTimeoutError):
        run_query(sample_db, slow, timeout_s=0.2)
    assert time.monotonic() - start < 2
