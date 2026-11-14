"""Plain-English explanations of real DuckDB and guard errors, with a fix to try."""

import duckdb
import pytest

from schemalab.guards.sql import UnsafeSQLError, check_sql
from schemalab.sqlerrors import explain

SCHEMA = {
    "students": ["id", "name", "major", "gpa"],
    "enrollments": ["student_id", "course", "grade"],
}


def error_of(sql: str) -> str:
    """The real message DuckDB gives for this query on the schema above."""
    con = duckdb.connect()
    con.execute("CREATE TABLE students (id INT, name TEXT, major TEXT, gpa DOUBLE)")
    con.execute("CREATE TABLE enrollments (student_id INT, course TEXT, grade TEXT)")
    con.execute("INSERT INTO students VALUES (1, 'Ada', 'Math', 3.9)")  # values get converted
    try:
        check_sql(sql, SCHEMA)
        con.execute(sql)
    except (duckdb.Error, UnsafeSQLError) as e:
        return str(e)
    raise AssertionError(f"no error for {sql}")


@pytest.mark.parametrize(
    ("sql", "title", "fix"),
    [
        ("SELECT nme FROM students", "No such column", "name"),
        ("SELECT * FROM student", "No such table", "students"),
        ("SELECT major, name, count(*) FROM students GROUP BY major", "GROUP BY", "name"),
        ("SELECT name FROM students WHERE", "Syntax", "WHERE"),
        ("SELECT name FROM students WHERE count(*) > 1", "Aggregate in WHERE", "HAVING"),
        ("SELECT lenght(name) FROM students", "No such function", "length"),
        ("DELETE FROM students", "Only queries", "SELECT"),
        ("SELECT 1; SELECT 2", "One query", "one"),
        ("SELECT name FROM students WHERE gpa = 'high'", "Types do not match", "quote"),
    ],
)
def test_each_error_gets_a_title_an_explanation_and_a_fix(sql: str, title: str, fix: str) -> None:
    got = explain(sql, error_of(sql), SCHEMA)
    assert title.lower() in got["title"].lower()
    assert got["explanation"]
    assert fix.lower() in got["fix"].lower(), got


def test_an_ambiguous_column_says_how_to_qualify_it() -> None:
    con = duckdb.connect()
    con.execute("CREATE TABLE a (id INT)")
    con.execute("CREATE TABLE b (id INT)")
    with pytest.raises(duckdb.Error) as e:
        con.execute("SELECT id FROM a JOIN b ON a.id = b.id")
    got = explain(
        "SELECT id FROM a JOIN b ON a.id = b.id", str(e.value), {"a": ["id"], "b": ["id"]}
    )
    assert "ambiguous" in got["title"].lower() and "a.id" in got["fix"]


def test_an_unknown_error_still_gets_a_readable_first_line() -> None:
    got = explain("SELECT 1", "Some Error: something odd happened\nmore detail", SCHEMA)
    assert got["title"] == "The query failed" and "something odd happened" in got["explanation"]
