"""Constraint coach: constraints a table's data already satisfies, with evidence and DDL.

Every suggestion is checked against the data before it is offered. Foreign keys also need a
hint in the names (customer_id -> customers.id, course_code -> courses.code), since small
integers fit inside almost any id range by chance. The statements are standard ALTER TABLE
for the user to run on their own database; nothing here changes the uploaded tables.
"""

import re
import threading
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import duckdb

from schemalab.er import RESERVED
from schemalab.guards.engine import open_readonly

TIMEOUT_S = 5.0  # per table scan
MAX_CATEGORIES = 10
PAIR_SEARCH = 8  # look for two-column keys among this many leading columns

_NUMERIC = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "DOUBLE",
    "FLOAT",
    "REAL",
    "DECIMAL",
)
_INTEGER = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT")
_KEYLESS = ("DOUBLE", "FLOAT", "REAL", "BOOLEAN")


@dataclass(frozen=True)
class Suggestion:
    kind: str  # "primary key", "foreign key", "not null", "unique", "check"
    columns: list[str]
    sql: str
    evidence: str


@dataclass
class _Column:
    name: str
    type: str
    note: str | None
    non_null: int = 0
    distinct: int = 0
    low: object = None

    def unique(self, rows: int) -> bool:
        return rows > 0 and self.non_null == rows == self.distinct

    @property
    def numeric(self) -> bool:
        return self.type.startswith(_NUMERIC)

    @property
    def family(self) -> str:
        return "number" if self.numeric else "text" if self.type == "VARCHAR" else self.type


@dataclass
class _Table:
    name: str
    rows: int
    columns: list[_Column] = field(default_factory=list)

    @property
    def recorded_key(self) -> list[str]:
        return [c.name for c in self.columns if (c.note or "").endswith("primary key")]


def q(name: str) -> str:
    """An identifier, quoted when it is reserved or not plain lowercase."""
    if re.fullmatch(r"[a-z_][a-z0-9_]*", name) and name not in RESERVED:
        return name
    return '"' + name.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _scan(con: duckdb.DuckDBPyConnection, name: str) -> _Table:
    meta = con.execute(
        "SELECT column_name, data_type, comment FROM duckdb_columns() "
        "WHERE schema_name = 'main' AND table_name = ? ORDER BY column_index",
        [name],
    ).fetchall()
    columns = [_Column(c, t, n) for c, t, n in meta]
    parts = ["count(*)"]
    for c in columns:
        parts += [f"count({q(c.name)})", f"count(DISTINCT {q(c.name)})"]
        parts.append(f"min({q(c.name)})" if c.numeric else "NULL")
    row = con.execute(f"SELECT {', '.join(parts)} FROM {q(name)}").fetchone() or ()  # noqa: S608
    table = _Table(name, int(row[0]) if row else 0, columns)
    for i, c in enumerate(columns):
        c.non_null, c.distinct, c.low = row[1 + 3 * i], row[2 + 3 * i], row[3 + 3 * i]
    return table


def suggest(db: Path, table: str) -> list[Suggestion]:
    """Raises KeyError for an unknown table and TimeoutError if the scan runs too long."""
    with open_readonly(db) as con:
        names = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'"
            ).fetchall()
        ]
        if table not in names:
            raise KeyError(table)
        timer = threading.Timer(TIMEOUT_S, con.interrupt)
        timer.start()
        try:
            return _suggest(con, table, names)
        except duckdb.InterruptException:
            raise TimeoutError(
                f"Checking {table} took longer than {TIMEOUT_S:.0f} seconds."
            ) from None
        finally:
            timer.cancel()


def _suggest(con: duckdb.DuckDBPyConnection, name: str, names: list[str]) -> list[Suggestion]:
    t = _scan(con, name)
    out: list[Suggestion] = []
    key = t.recorded_key or _primary_key(con, t, out)
    references = _foreign_keys(con, t, names, key, out)

    for c in t.columns:
        if c.name not in key and t.rows and c.non_null == t.rows:
            out.append(
                Suggestion(
                    "not null",
                    [c.name],
                    f"ALTER TABLE {q(t.name)} ALTER COLUMN {q(c.name)} SET NOT NULL;",
                    f"None of the {t.rows:,} rows is missing a value.",
                )
            )
    for c in t.columns:
        if (
            c.name not in key
            and t.rows >= 2
            and c.unique(t.rows)
            and not c.type.startswith(_KEYLESS)
        ):
            out.append(
                Suggestion(
                    "unique",
                    [c.name],
                    f"ALTER TABLE {q(t.name)} ADD UNIQUE ({q(c.name)});",
                    f"All {t.rows:,} values are different.",
                )
            )
    for c in t.columns:
        if c.name in key or c.name in references:
            continue
        if c.numeric and c.low is not None and c.low >= 0:  # type: ignore[operator]
            out.append(
                Suggestion(
                    "check",
                    [c.name],
                    f"ALTER TABLE {q(t.name)} ADD CHECK ({q(c.name)} >= 0);",
                    f"The smallest value is {c.low}.",
                )
            )
        elif c.type == "VARCHAR" and 2 <= c.distinct <= MAX_CATEGORIES and t.rows >= 3 * c.distinct:
            values = [
                r[0]
                for r in con.execute(
                    f"SELECT DISTINCT {q(c.name)} FROM {q(t.name)} "  # noqa: S608
                    f"WHERE {q(c.name)} IS NOT NULL ORDER BY 1"
                ).fetchall()
            ]
            listed = ", ".join(_literal(v) for v in values)
            out.append(
                Suggestion(
                    "check",
                    [c.name],
                    f"ALTER TABLE {q(t.name)} ADD CHECK ({q(c.name)} IN ({listed}));",
                    f"Only {len(values)} different values appear in {t.rows:,} rows.",
                )
            )
    return out


def _primary_key(con: duckdb.DuckDBPyConnection, t: _Table, out: list[Suggestion]) -> list[str]:
    def rank(c: _Column) -> tuple[int, int, int]:
        lower = c.name.lower()
        return (lower != "id", not lower.endswith("id"), not c.type.startswith(_INTEGER))

    singles = [c for c in t.columns if c.unique(t.rows) and not c.type.startswith(_KEYLESS)]
    if singles:
        best = min(singles, key=rank)
        out.append(
            Suggestion(
                "primary key",
                [best.name],
                f"ALTER TABLE {q(t.name)} ADD PRIMARY KEY ({q(best.name)});",
                f"All {t.rows:,} rows have a value, and no two are the same.",
            )
        )
        return [best.name]
    full = [
        c
        for c in t.columns[:PAIR_SEARCH]
        if t.rows and c.non_null == t.rows and not c.type.startswith(_KEYLESS)
    ]
    for a, b in combinations(full, 2):
        pair = f"{q(a.name)}, {q(b.name)}"
        (distinct,) = con.execute(
            f"SELECT count(*) FROM (SELECT DISTINCT {pair} FROM {q(t.name)})"  # noqa: S608
        ).fetchone() or (0,)
        if distinct == t.rows:
            out.append(
                Suggestion(
                    "primary key",
                    [a.name, b.name],
                    f"ALTER TABLE {q(t.name)} ADD PRIMARY KEY ({pair});",
                    f"No single column is unique, but every ({a.name}, {b.name}) pair "
                    f"in the {t.rows:,} rows is.",
                )
            )
            return [a.name, b.name]
    return []


def _hint(column: str, table: str, key: str) -> bool:
    """customer_id -> customers.id, course_code -> courses.code, PULocationID -> LocationID."""
    c, k, u = column.lower(), key.lower(), table.lower()
    stem = u[:-3] + "y" if u.endswith("ies") else u[:-1] if u.endswith("s") else u
    if c == k:
        return True
    if not c.endswith(k) or len(c) == len(k):
        return False
    return k not in ("id", "key", "no", "num") or stem in c


def _foreign_keys(
    con: duckdb.DuckDBPyConnection,
    t: _Table,
    names: list[str],
    key: list[str],
    out: list[Suggestion],
) -> set[str]:
    scanned: dict[str, _Table] = {t.name: t}
    found: set[str] = set()
    for c in t.columns:
        if c.non_null == 0 or (c.note or "").startswith("references") or key == [c.name]:
            continue
        for other_name in names:
            other = scanned.get(other_name) or scanned.setdefault(
                other_name, _scan(con, other_name)
            )
            targets = (
                other.recorded_key
                if len(other.recorded_key) == 1
                else [k.name for k in other.columns if k.unique(other.rows)]
            )
            for target in targets:
                target_col = next(k for k in other.columns if k.name == target)
                if (other_name, target) == (t.name, c.name) or target_col.family != c.family:
                    continue
                if not _hint(c.name, other_name, target):
                    continue
                (missing,) = con.execute(
                    f"SELECT count(*) FROM {q(t.name)} WHERE {q(c.name)} IS NOT NULL "  # noqa: S608
                    f"AND {q(c.name)} NOT IN (SELECT {q(target)} FROM {q(other_name)})"
                ).fetchone() or (1,)
                if missing:
                    continue
                out.append(
                    Suggestion(
                        "foreign key",
                        [c.name],
                        f"ALTER TABLE {q(t.name)} ADD FOREIGN KEY ({q(c.name)}) "
                        f"REFERENCES {q(other_name)} ({q(target)});",
                        f"The names match, and every one of the {c.non_null:,} values "
                        f"appears in {other_name}.{target}.",
                    )
                )
                found.add(c.name)
                break
            if c.name in found:
                break
    return found
