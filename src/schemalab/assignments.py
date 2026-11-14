"""SQL assignments: a teacher's setup, a reference query and hidden test databases, graded
with partial credit.

Setup and hidden data are untrusted text, so they load through the same .sql reader as
uploads: CREATE TABLE and INSERT ... VALUES with constants only, nothing else runs. Student and
reference queries run through the SQL guard on the locked, read-only engine. Each database is
built once per distinct content and cached.

Credit: 10 for a query that runs, 20 for the right columns (10 for the right number), and 70
shared by the databases (the visible sample and each hidden one) whose rows match. Tutor mode
explains each failed test with a hint and up to three rows that differ; exam mode only names
the failed tests, so hidden data never shows.
"""

import datetime
import hashlib
import tempfile
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from schemalab import uploads
from schemalab.guards.engine import open_readonly
from schemalab.guards.run import QueryResult, QueryTimeoutError, run_query
from schemalab.guards.sql import UnsafeSQLError
from schemalab.sqldump import ident
from schemalab.sqlerrors import explain

Mode = Literal["tutor", "exam"]
SAMPLE = "Sample data"
SHOWN_ROWS = 3


@dataclass(frozen=True)
class Assignment:
    title: str
    prompt: str
    schema: str  # CREATE TABLE statements
    data: str  # INSERTs for the sample students can see
    reference: str  # the teacher's SELECT
    hidden: list[tuple[str, str]] = field(default_factory=list)  # (what it tests, INSERTs)
    ordered: bool = False  # whether row order is part of the answer

    def with_order(self, reference: str) -> "Assignment":
        return replace(self, reference=reference, ordered=True)


def database(cache: Path, schema: str, data: str) -> Path:
    """A DuckDB file holding schema + data, built once through the safe .sql reader."""
    key = hashlib.sha256(f"{schema}\n{data}".encode()).hexdigest()[:20]
    path = cache / f"{key}.duckdb"
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as work:
            src = Path(work) / "setup.sql"
            src.write_text(f"{schema}\n{data}")
            building = cache / f"{key}.building.duckdb"
            building.unlink(missing_ok=True)
            uploads.import_file(building, src, "setup.sql")
            building.replace(path)
    return path


def schema_of(path: Path) -> dict[str, list[str]]:
    with open_readonly(path) as con:
        rows = con.execute(
            "SELECT table_name, column_name FROM duckdb_columns() WHERE schema_name = 'main' "
            "ORDER BY table_name, column_index"
        ).fetchall()
    out: dict[str, list[str]] = {}
    for table, column in rows:
        out.setdefault(table, []).append(column)
    return out


def _norm(value: Any) -> Any:
    if isinstance(value, float | Decimal):
        return round(float(value), 6)
    if isinstance(value, datetime.date | datetime.datetime):
        return value.isoformat()
    return value


def _rows(result: QueryResult) -> list[tuple[Any, ...]]:
    return [tuple(_norm(v) for v in r) for r in result.rows]


def _plain(row: tuple[Any, ...]) -> list[Any]:
    return [v if isinstance(v, int | float | str | bool) or v is None else str(v) for v in row]


_FAILURES = (UnsafeSQLError, QueryTimeoutError, duckdb.Error)


def grade(a: Assignment, sql: str, mode: Mode, cache: Path) -> dict[str, Any]:
    if mode not in ("tutor", "exam"):
        raise ValueError(f"Unknown grading mode {mode!r}.")
    tests = [(SAMPLE, a.data), *a.hidden]
    paths = [(name, database(cache, a.schema, data)) for name, data in tests]
    sample = paths[0][1]
    try:
        first = run_query(sample, sql)
    except _FAILURES as e:
        return {
            "score": 0,
            "parts": {"runs": 0, "columns": 0, "rows": 0},
            "error": explain(sql, str(e), schema_of(sample)),
            "tests": [{"name": name, "passed": False} for name, _ in tests],
        }
    expected = run_query(sample, a.reference)
    if [c.lower() for c in first.columns] == [c.lower() for c in expected.columns]:
        columns = 20
    elif len(first.columns) == len(expected.columns):
        columns = 10
    else:
        columns = 0

    results = []
    for name, path in paths:
        want = run_query(path, a.reference)
        try:
            got = first if path == sample else run_query(path, sql)
        except _FAILURES as e:
            entry: dict[str, Any] = {"name": name, "passed": False}
            if mode == "tutor":
                entry["hint"] = (
                    "Your query failed on this data: "
                    + explain(sql, str(e), schema_of(path))["title"]
                )
            results.append(entry)
            continue
        results.append(
            _compare(
                name,
                _rows(got),
                _rows(want),
                len(got.columns) == len(want.columns),
                a.ordered,
                mode,
            )
        )

    passed = sum(1 for r in results if r["passed"])
    rows = 70 * passed / len(results)
    return {
        "score": round(10 + columns + rows),
        "parts": {"runs": 10, "columns": columns, "rows": round(rows)},
        "error": None,
        "tests": results,
    }


def _compare(
    name: str,
    got: list[tuple[Any, ...]],
    want: list[tuple[Any, ...]],
    same_width: bool,
    ordered: bool,
    mode: Mode,
) -> dict[str, Any]:
    same = same_width and (
        got == want if ordered else sorted(got, key=repr) == sorted(want, key=repr)
    )
    entry: dict[str, Any] = {"name": name, "passed": same}
    if same or mode != "tutor":  # hidden rows show in tutor mode only, never by default
        return entry
    if not same_width:
        hint = "Your query returns a different number of columns than the answer."
    elif len(got) < len(want):
        hint = f"Your query returns fewer rows ({len(got)}) than the answer ({len(want)})."
    elif len(got) > len(want):
        hint = f"Your query returns more rows ({len(got)}) than the answer ({len(want)})."
    elif sorted(got, key=repr) == sorted(want, key=repr):
        hint = "The rows are right but in the wrong order."
    else:
        hint = "Some values differ from the answer."
    remaining = list(got)
    missing = []
    for row in want:
        if row in remaining:
            remaining.remove(row)
        else:
            missing.append(row)
    entry.update(
        hint=hint,
        missing=[_plain(r) for r in missing[:SHOWN_ROWS]],
        extra=[_plain(r) for r in remaining[:SHOWN_ROWS]],
    )
    return entry


# The builder's checks


def _setup_problems(a: Assignment) -> list[str]:
    """Setup may only create tables and insert constants, with names the loader keeps."""
    problems: list[str] = []
    texts = [("the schema", a.schema), (SAMPLE.lower(), a.data)]
    texts += [(f"hidden test {name!r}", data) for name, data in a.hidden]
    for where, text in texts:
        try:
            statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
        except SqlglotError as e:
            problems.append(f"Could not read {where}: {str(e).splitlines()[0]}")
            continue
        for s in statements:
            table_create = isinstance(s, exp.Create) and s.args.get("kind") == "TABLE"
            if not (table_create or isinstance(s, exp.Insert)):
                kind = s.sql(dialect="postgres").split()[0].upper() if s.sql() else "?"
                problems.append(
                    f"Only CREATE TABLE and INSERT statements may set up data; {where} has {kind}."
                )
            if table_create and isinstance(s.this, exp.Schema):
                names = [s.this.this.name] + [c.name for c in s.this.find_all(exp.ColumnDef)]
                for name in names:
                    if ident(name) != name:
                        problems.append(
                            f"Use lowercase names of letters, digits and _ only: {name}."
                        )
    return problems


def check(a: Assignment, cache: Path) -> dict[str, list[str]]:
    """What stops the assignment from being published (errors) and what weakens it."""
    errors = _setup_problems(a)
    warnings: list[str] = []
    try:
        tree = sqlglot.parse_one(a.reference, read="duckdb")
        sorts = tree.args.get("order") is not None
    except SqlglotError:
        sorts = False
    if a.ordered and not sorts:
        errors.append("Order is graded, but the reference query has no ORDER BY.")
    if errors:
        return {"errors": errors, "warnings": warnings}
    for name, data in [(SAMPLE, a.data), *a.hidden]:
        try:
            path = database(cache, a.schema, data)
            answer = run_query(path, a.reference)
        except uploads.UploadError as e:
            errors.append(f"{name}: {e}")
            continue
        except _FAILURES as e:
            reason = explain(a.reference, str(e), schema_of(path))["title"]
            errors.append(f"The reference query fails on {name}: {reason}.")
            continue
        if name != SAMPLE and not answer.rows:
            warnings.append(
                f"{name}: the answer has no rows, so a query that returns nothing passes it."
            )
    if not a.hidden:
        warnings.append(
            "There are no hidden tests: a query fitted to the sample data gets full marks."
        )
    if sorts and not a.ordered:
        warnings.append(
            "The reference sorts its rows, but order is not graded; mark the assignment as "
            "ordered if it matters."
        )
    return {"errors": errors, "warnings": warnings}


# Ready-made assignments

SAMPLES: dict[str, Assignment] = {
    "courses-per-student": Assignment(
        title="Courses per student",
        prompt="For every student, show their name and how many courses they have taken "
        "(0 if none). Columns: name, courses.",
        schema="CREATE TABLE students (id INT PRIMARY KEY, name TEXT);\n"
        "CREATE TABLE grades (student_id INT, course TEXT, grade INT);",
        data="INSERT INTO students VALUES (1, 'Ada'), (2, 'Alan');\n"
        "INSERT INTO grades VALUES (1, 'DB', 90), (1, 'AI', 85), (2, 'DB', 70);",
        reference="SELECT s.name, count(g.course) AS courses FROM students s "
        "LEFT JOIN grades g ON g.student_id = s.id GROUP BY s.id, s.name",
        hidden=[
            (
                "A student with no courses",
                "INSERT INTO students VALUES (1, 'Ada'), (3, 'Grace');\n"
                "INSERT INTO grades VALUES (1, 'DB', 90);",
            ),
            (
                "Two students with the same name",
                "INSERT INTO students VALUES (1, 'Sam'), (2, 'Sam');\n"
                "INSERT INTO grades VALUES (1, 'DB', 90), (2, 'AI', 80), (2, 'DB', 75);",
            ),
        ],
    ),
    "top-paid": Assignment(
        title="Top earner per department",
        prompt="For each department, show the highest-paid employee: department, name and "
        "salary. If several share the top salary, show all of them.",
        schema="CREATE TABLE employees (id INT PRIMARY KEY, name TEXT, dept TEXT, salary INT);",
        data="INSERT INTO employees VALUES (1, 'Ada', 'Eng', 120), (2, 'Alan', 'Eng', 110), "
        "(3, 'Grace', 'Ops', 90), (4, 'Edsger', 'Ops', 80);",
        reference="SELECT e.dept, e.name, e.salary FROM employees e WHERE e.salary = "
        "(SELECT max(x.salary) FROM employees x WHERE x.dept = e.dept)",
        hidden=[
            (
                "A tie for the top salary",
                "INSERT INTO employees VALUES (1, 'Ada', 'Eng', 120), (2, 'Alan', 'Eng', 120), "
                "(3, 'Grace', 'Ops', 90);",
            ),
            (
                "A department of one",
                "INSERT INTO employees VALUES (1, 'Ada', 'Eng', 120), (2, 'Barbara', 'Law', 70), "
                "(3, 'Alan', 'Eng', 100);",
            ),
        ],
    ),
    "missing-emails": Assignment(
        title="Customers without an email",
        prompt="How many customers have no email on file? One column: no_email.",
        schema="CREATE TABLE customers (id INT PRIMARY KEY, name TEXT, email TEXT);",
        data="INSERT INTO customers VALUES (1, 'Ada', 'ada@example.com'), (2, 'Alan', NULL), "
        "(3, 'Grace', NULL);",
        reference="SELECT count(*) AS no_email FROM customers WHERE email IS NULL",
        hidden=[
            (
                "Everyone has an email",
                "INSERT INTO customers VALUES (1, 'Ada', 'ada@example.com'), "
                "(2, 'Alan', 'alan@example.com');",
            ),
            (
                "Nobody has an email",
                "INSERT INTO customers VALUES (1, 'Ada', NULL), (2, 'Alan', NULL), "
                "(3, 'Grace', NULL), (4, 'Edsger', NULL);",
            ),
        ],
    ),
}
