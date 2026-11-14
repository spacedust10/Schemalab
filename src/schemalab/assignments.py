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

from schemalab import uploads
from schemalab.guards.engine import open_readonly
from schemalab.guards.run import QueryResult, QueryTimeoutError, run_query
from schemalab.guards.sql import UnsafeSQLError
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
    if same or mode == "exam":
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
