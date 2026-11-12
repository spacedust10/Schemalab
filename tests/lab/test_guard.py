"""The lab's statement guard: one statement, from an allow-list."""

import pytest

from schemalab.lab.guard import LabSQLError, check_lab_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM accounts WHERE id = 1 FOR UPDATE",
        "UPDATE accounts SET balance = balance - 10 WHERE id = 1;",
        "insert into accounts values (3, 50)",
        "DELETE FROM accounts",
        "CREATE TABLE t (id int primary key, n int)",
        "CREATE INDEX t_n ON t (n)",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN m int",
        "BEGIN",
        "BEGIN ISOLATION LEVEL REPEATABLE READ",
        "START TRANSACTION ISOLATION LEVEL SERIALIZABLE",
        "COMMIT",
        "rollback",
        "SAVEPOINT s1",
        "ROLLBACK TO SAVEPOINT s1",
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL SERIALIZABLE",
        "SHOW transaction_isolation",
        "LOCK TABLE accounts IN SHARE MODE",
        "EXPLAIN SELECT 1",
        "ANALYZE orders",
        "VACUUM ANALYZE orders",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "-- note\nSELECT 'a; b'",
    ],
)
def test_allowed(sql: str) -> None:
    assert check_lab_sql(sql) == sql.strip().rstrip(";").strip()


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SET statement_timeout = 0", "Only SET TRANSACTION"),
        ("set lock_timeout to 0", "Only SET TRANSACTION"),
        ("COPY accounts TO '/tmp/x'", "COPY is not allowed in the lab"),
        ("DO $$ BEGIN PERFORM 1; END $$", "not allowed in the lab"),
        (
            "CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql",
            "not allowed in the lab",
        ),
        ("CREATE EXTENSION dblink", "not allowed in the lab"),
        ("SET ROLE postgres", "Only SET TRANSACTION"),
        ("GRANT ALL ON accounts TO public", "GRANT is not allowed in the lab"),
        ("ALTER SYSTEM SET fsync = off", "not allowed in the lab"),
        ("SELECT 1; SELECT 2", "one statement at a time"),
        ("SELECT set_config('statement_timeout', '0', false)", "set_config is not allowed"),
        ("SELECT SET_CONFIG ('lock_timeout', '0', true)", "set_config is not allowed"),
        ("RESET statement_timeout", "not allowed in the lab"),
        ("SELECT \"set_config\"('statement_timeout', '0', false)", "set_config is not allowed"),
        ("UPDATE pg_settings SET setting = '0' WHERE name = 'statement_timeout'", "pg_settings"),
        ("UPDATE \"pg_settings\" SET setting = '0'", "pg_settings"),
        ("   ", "Write a statement"),
        ("SELECT '" + "x" * 6000 + "'", "at most 5000 characters"),
    ],
)
def test_refused(sql: str, message: str) -> None:
    with pytest.raises(LabSQLError, match=message):
        check_lab_sql(sql)
