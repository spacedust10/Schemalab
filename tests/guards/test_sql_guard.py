"""The parser guard: the first line of defense for model-written SQL."""

from pathlib import Path

import pytest

from schemalab.guards.engine import open_readonly
from schemalab.guards.sql import UnsafeSQLError, check_sql

TABLES = frozenset({"orders", "customers"})

ALLOWED = [
    "SELECT * FROM orders",
    "SELECT id, amount FROM orders WHERE amount > 10 ORDER BY amount DESC LIMIT 5",
    "SELECT c.name, sum(o.amount) AS total FROM orders o JOIN customers c ON o.customer_id = c.id"
    " GROUP BY c.name HAVING sum(o.amount) > 100",
    "WITH big AS (SELECT * FROM orders WHERE amount > 50) SELECT count(*) FROM big",
    "WITH a AS (SELECT id FROM orders), b AS (SELECT id FROM a) SELECT * FROM b",
    "SELECT * FROM orders WHERE customer_id IN (SELECT id FROM customers)",
    "SELECT id FROM orders UNION ALL SELECT id FROM customers",
    "SELECT id, rank() OVER (PARTITION BY customer_id ORDER BY amount) FROM orders",
    "SELECT CASE WHEN amount > 10 THEN 'big' ELSE 'small' END FROM orders",
    "SELECT date_trunc('month', DATE '2026-03-14'), upper(name) FROM customers",
    'SELECT * FROM "Orders"',
    "SELECT * FROM ORDERS;",
    "-- top orders\nSELECT * FROM orders /* inline */ LIMIT 3",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allows_read_queries(sql: str) -> None:
    assert check_sql(sql, TABLES)


def test_returns_normalized_sql_to_run() -> None:
    # The caller runs exactly what was checked, not the raw model text.
    assert check_sql("select * from orders;  -- done", TABLES) == "SELECT * FROM orders"


BLOCKED = {
    # Statement shape
    "empty": "",
    "only a comment": "-- nothing here",
    "syntax error": "SELEC * FRM orders",
    "two statements": "SELECT 1 FROM orders; DROP TABLE orders",
    "stacked select": "SELECT * FROM orders; SELECT * FROM customers",
    # Writes and DDL
    "insert": "INSERT INTO orders VALUES (1, 1, 1)",
    "update": "UPDATE orders SET amount = 0",
    "delete": "DELETE FROM orders",
    "drop": "DROP TABLE orders",
    "create": "CREATE TABLE x (a INT)",
    "create as select": "CREATE TABLE x AS SELECT * FROM orders",
    "alter": "ALTER TABLE orders ADD COLUMN x INT",
    "truncate": "TRUNCATE orders",
    "write in cte": "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
    # Engine and session commands
    "copy out": "COPY orders TO '/tmp/out.csv'",
    "copy in": "COPY orders FROM '/etc/passwd'",
    "attach": "ATTACH '/tmp/other.db' AS other",
    "detach": "DETACH other",
    "pragma": "PRAGMA database_list",
    "set": "SET enable_external_access = true",
    "install": "INSTALL httpfs",
    "load": "LOAD httpfs",
    "export": "EXPORT DATABASE '/tmp/dump'",
    "call": "CALL pragma_version()",
    "begin": "BEGIN TRANSACTION",
    "describe": "DESCRIBE orders",
    # Files and table functions
    "read_csv": "SELECT * FROM read_csv('/etc/passwd')",
    "read_csv mixed case": "sElEcT * FrOm ReAd_CsV('/etc/passwd')",
    "read_parquet": "SELECT * FROM read_parquet('s3://bucket/x.parquet')",
    "read_json": "SELECT * FROM read_json_auto('/tmp/x.json')",
    "read_text": "SELECT content FROM read_text('/etc/hosts')",
    "glob": "SELECT * FROM glob('/home/*')",
    "file path as table": "SELECT * FROM '/etc/passwd'",
    "parquet path as table": "SELECT * FROM 'data/*.parquet'",
    "url as table": "SELECT * FROM 'https://example.com/x.csv'",
    "file in subquery": "SELECT * FROM orders WHERE id IN (SELECT 1 FROM read_csv('/etc/passwd'))",
    "file in cte": "WITH x AS (SELECT * FROM read_csv('/etc/passwd')) SELECT * FROM x",
    "file in lateral join": "SELECT * FROM orders, read_csv('/etc/passwd')",
    "query table function": "SELECT * FROM query_table('orders')",
    "range": "SELECT * FROM range(1000000000)",
    "function named like a table": "SELECT * FROM orders()",
    # Scalar functions that leak the host
    "getenv": "SELECT getenv('HOME') FROM orders",
    "current_setting": "SELECT current_setting('home_directory')",
    # Tables outside the allow-list
    "unknown table": "SELECT * FROM users",
    "system catalog": "SELECT * FROM information_schema.tables",
    "duckdb meta function": "SELECT * FROM duckdb_settings()",
    "sqlite_master": "SELECT * FROM sqlite_master",
    "other catalog": "SELECT * FROM other.main.orders",
    "other schema": "SELECT * FROM secret.orders",
}


@pytest.mark.parametrize("sql", BLOCKED.values(), ids=BLOCKED.keys())
def test_blocks_unsafe_sql(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        check_sql(sql, TABLES)


def test_error_says_why() -> None:
    with pytest.raises(UnsafeSQLError, match="users"):
        check_sql("SELECT * FROM users", TABLES)


@pytest.mark.parametrize("sql", ALLOWED)
def test_normalized_sql_runs_on_readonly_engine(sql: str, sample_db: Path) -> None:
    open_readonly(sample_db).execute(check_sql(sql, TABLES)).fetchall()


def test_table_function_error_names_the_function() -> None:
    # The model retries from this message, so it must say what was wrong.
    with pytest.raises(UnsafeSQLError, match="table functions are not allowed: READ_CSV"):
        check_sql("SELECT * FROM read_csv('/etc/passwd')", TABLES)


def test_cte_name_does_not_leak_out_of_its_subquery() -> None:
    # The inner CTE named "customers" must not make the outer, real customers table allowed.
    sql = (
        "SELECT * FROM customers WHERE id IN "
        "(WITH customers AS (SELECT 1 AS id) SELECT id FROM customers)"
    )
    with pytest.raises(UnsafeSQLError, match="customers"):
        check_sql(sql, {"orders"})


def test_outer_cte_is_visible_in_nested_subqueries() -> None:
    sql = (
        "WITH big AS (SELECT * FROM orders) "
        "SELECT * FROM (SELECT * FROM big) WHERE id IN (SELECT id FROM big)"
    )
    assert check_sql(sql, {"orders"})
