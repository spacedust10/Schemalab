"""User uploads: each .csv file, Excel sheet or table in a .sql dump becomes a table in a DuckDB
file of the user's own.

Imports are trusted code with a normal connection, like `datasets`. They build into a copy and
swap it in, so a failed upload leaves the user's tables as they were and queries running against
the old file are not disturbed. Model-written SQL only ever reads the result through
`guards.run.run_query`.
"""

import csv
import json
import re
import shutil
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import duckdb
import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from schemalab.guards.engine import open_readonly
from schemalab.sqldump import NULL, DumpError, ident, read_dump

MAX_BYTES = 50 * 1024 * 1024
# A zipped .xlsx can expand far past MAX_BYTES; this caps the CSV it unpacks to.
MAX_UNPACKED_BYTES = 4 * MAX_BYTES
MAX_TABLES = 100  # a whole sample database, like AdventureWorks (68), fits
MAX_COLUMNS = 500
SUFFIXES = (".csv", ".xlsx", ".sql")
# Key notes are built from [a-z0-9_] identifiers, so they are safe inside a SQL string.
_NOTE_PART = r"(primary key|part of the primary key|references [a-z0-9_]+(\([a-z0-9_]+\))?)"
_NOTE = re.compile(rf"{_NOTE_PART}(; {_NOTE_PART})*")
_KEYS = "schemalab keys "  # a table comment that starts so holds its keys as JSON

# Trusted connection, but still no extension downloads.
_CONFIG: dict[str, str | bool | int | float | list[str]] = {
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
}
# ponytail: one lock for every user's imports; per-user locks if uploads ever queue up.
_LOCK = threading.Lock()


class UploadError(ValueError):
    """The file cannot be imported. The message is safe to show the user."""


@dataclass(frozen=True)
class Imported:
    tables: list[str]
    skipped: dict[str, int]  # statement kind -> count, for .sql dumps


def table_name(filename: str, taken: set[str]) -> str:
    """A lowercase [a-z0-9_] identifier from the file name, not starting with a digit."""
    base = Path(filename).name
    base = base.rsplit(".", 1)[0] if "." in base else base
    return ident(base if re.search(r"[A-Za-z0-9]", base) else "upload", taken)


def import_file(db: Path, src: Path, filename: str) -> Imported:
    """Add the file's tables to `db` and return their names."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUFFIXES:
        raise UploadError("Upload a .csv, .xlsx or .sql file.")
    with _LOCK, tempfile.TemporaryDirectory() as work:
        db.parent.mkdir(parents=True, exist_ok=True)
        tmp = db.with_name(db.name + ".importing")
        tmp.unlink(missing_ok=True)
        if db.exists():
            shutil.copyfile(db, tmp)
        try:
            with duckdb.connect(str(tmp), config=_CONFIG) as con:
                taken = set(_tables(con))
                if suffix == ".sql":
                    result = _load_dump(con, src, Path(work), taken)
                else:
                    if suffix == ".csv":
                        sources = [(filename, _as_utf8(src, Path(work)), False)]
                    else:
                        sources = _excel_to_csv(src, filename, Path(work))
                    _check_room(taken, len(sources))
                    created = [
                        _load(con, table_name(n, taken), path, header, taken)
                        for n, path, header in sources
                    ]
                    result = Imported(created, {})
            tmp.replace(db)
            return result
        except duckdb.Error as e:
            # DuckDB names the server's temp file in some errors; the user needs only theirs.
            detail = _PATH.sub("the file", str(e).splitlines()[0])
            raise UploadError(f"Could not read {Path(filename).name}: {detail}") from None
        finally:
            tmp.unlink(missing_ok=True)


_PATH = re.compile(r"""["']?/[^\s"']+["']?""")


def _as_utf8(src: Path, work: Path) -> Path:
    """The file as UTF-8. Excel saves CSV as Windows-1252 or, as "Unicode Text", UTF-16."""
    raw = src.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        try:
            raw.decode("utf-8")
            return src
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
    out = work / "utf8.csv"
    out.write_text(text, encoding="utf-8")
    return out


def _check_room(taken: set[str], adding: int) -> None:
    has = f"This file has {adding} table{'' if adding == 1 else 's'}"
    if adding > MAX_TABLES:
        raise UploadError(f"{has}; you can keep at most {MAX_TABLES}.")
    if len(taken) + adding > MAX_TABLES:
        raise UploadError(
            f"{has} and you have {len(taken)}; you can keep at most {MAX_TABLES}. "
            "Delete some first."
        )


def _load_dump(con: duckdb.DuckDBPyConnection, src: Path, work: Path, taken: set[str]) -> Imported:
    """Create the dump's tables from its parsed definitions and load the rows it holds."""
    try:
        dump = read_dump(src.read_text(encoding="utf-8", errors="replace"), work)
    except DumpError as e:
        raise UploadError(str(e)) from None
    _check_room(taken, len(dump.tables))
    renamed: dict[str, str] = {}
    for table in dump.tables:
        if len(table.columns) > MAX_COLUMNS:
            raise UploadError(f"A table can have at most {MAX_COLUMNS} columns.")
        name = ident(table.name, taken)
        taken.add(name)
        renamed[table.name] = name
        # Names are [a-z0-9_] identifiers and types matched a strict pattern in sqldump.
        columns = [(c, _duckdb_type(con, t)) for c, t in table.columns]
        con.execute(f'CREATE TABLE "{name}" ({", ".join(f'"{c}" {t}' for c, t in columns)})')
        if table.row_count:
            # TRY_CAST: a value the column type cannot hold becomes NULL rather than failing
            # the whole file; the profile shows how much of each column is empty.
            casts = ", ".join(f'TRY_CAST("{c}" AS {t})' for c, t in columns)
            options = "header = true, all_varchar = true, delim = ',', quote = '\"', escape = '\"'"
            con.execute(
                # OFFSET 0 stops DuckDB pushing the casts into the CSV reader, where they
                # would fail strictly instead of giving NULL.
                f'INSERT INTO "{name}" SELECT {casts} '  # noqa: S608
                f"FROM (SELECT * FROM read_csv(?, {options}, nullstr = '{NULL}') OFFSET 0)",
                [str(table.csv)],
            )
    for (table_key, column), note in dump.keys.items():
        note = re.sub(r"references (\w+)", lambda m: f"references {renamed.get(m[1], m[1])}", note)
        if _NOTE.fullmatch(note):
            con.execute(f'COMMENT ON COLUMN "{renamed[table_key]}"."{column}" IS \'{note}\'')
    for table in dump.tables:
        if table.primary or table.foreign:
            keys = {
                "primary": table.primary,
                "unique": table.unique,
                "foreign": [[c, renamed.get(t, t), r] for c, t, r in table.foreign],
                "checks": {c: v[:50] for c, v in table.checks.items()},
                "not_null": table.not_null,
            }
            # Names are [a-z0-9_]; CHECK values come from the file, so quotes are doubled.
            text = (_KEYS + json.dumps(keys)).replace("'", "''")
            con.execute(f"COMMENT ON TABLE \"{renamed[table.name]}\" IS '{text}'")
    return Imported([renamed[t.name] for t in dump.tables], dict(dump.skipped))


def _duckdb_type(con: duckdb.DuckDBPyConnection, sql_type: str) -> str:
    """The type if DuckDB has it (MONEY, for one, it does not), else VARCHAR."""
    try:
        con.execute(f"SELECT CAST(NULL AS {sql_type})")
    except duckdb.Error:
        return "VARCHAR"
    return sql_type


def _load(
    con: duckdb.DuckDBPyConnection, name: str, path: Path, header: bool, taken: set[str]
) -> str:
    # The name passed table_name's [a-z0-9_] filter; the path is bound. Excel sheets always have
    # a header row; for CSV files DuckDB detects one.
    # null_padding: a short row gets NULLs and a long one an extra column, instead of the
    # sniffer reading every row as one text column.
    options = "null_padding = true" + (", header = true" if header else "")
    con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM read_csv(?, {options})', [str(path)])  # noqa: S608
    _tidy_columns(con, name)
    columns = len(con.execute(f'DESCRIBE "{name}"').fetchall())
    if columns > MAX_COLUMNS:
        raise UploadError(f"A table can have at most {MAX_COLUMNS} columns, not {columns}.")
    (rows,) = con.execute(f'SELECT count(*) FROM "{name}"').fetchone() or (0,)  # noqa: S608
    if rows == 0:
        raise UploadError("The file has no data rows.")
    taken.add(name)
    return name


def _tidy_columns(con: duckdb.DuckDBPyConnection, table: str) -> None:
    """Lowercase [a-z0-9_] column names; only SQL's reserved words get a leading _."""
    reserved = {
        r[0]
        for r in con.execute(
            "SELECT keyword_name FROM duckdb_keywords() WHERE keyword_category = 'reserved'"
        ).fetchall()
    }
    taken: set[str] = set()
    for (old,) in con.execute(
        "SELECT column_name FROM duckdb_columns() WHERE table_name = ? ORDER BY column_index",
        [table],
    ).fetchall():
        base = ident(old)
        base = f"_{base}" if base in reserved else base
        new, n = base, 2
        while new in taken:
            new, n = f"{base}_{n}", n + 1
        taken.add(new)
        if new != old:
            quoted = old.replace('"', '""')
            con.execute(f'ALTER TABLE "{table}" RENAME COLUMN "{quoted}" TO "{new}"')


def _excel_to_csv(src: Path, filename: str, work: Path) -> list[tuple[str, Path, bool]]:
    """Write each non-empty sheet to a CSV file. openpyxl parses XML with defusedxml."""
    # A file object, not the path: openpyxl refuses paths without an Excel extension, and
    # uploads arrive as extensionless temp files. Read-only mode keeps reading it, so it stays
    # open until the workbook is closed.
    fh = src.open("rb")
    try:
        wb = openpyxl.load_workbook(fh, read_only=True, data_only=True)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError):
        fh.close()
        raise UploadError("This is not an Excel .xlsx file.") from None
    stem = Path(filename).name.rsplit(".", 1)[0]
    sheets: list[tuple[str, Path]] = []
    try:
        for i, ws in enumerate(wb.worksheets):
            out = work / f"sheet{i}.csv"
            with out.open("w", newline="") as f:
                writer = csv.writer(f)
                wrote = False
                size = 0
                for row in ws.iter_rows(values_only=True):
                    if all(v is None for v in row):
                        continue
                    if len(row) > MAX_COLUMNS:
                        raise UploadError(f"A table can have at most {MAX_COLUMNS} columns.")
                    size += writer.writerow([_cell(v) for v in row])
                    if size > MAX_UNPACKED_BYTES:
                        raise UploadError("This workbook is too large once unpacked.")
                    wrote = True
            if wrote:
                sheets.append((ws.title, out))
    finally:
        wb.close()
        fh.close()
    if not sheets:
        raise UploadError("The workbook has no data.")
    if len(sheets) == 1:
        return [(filename, sheets[0][1], True)]
    return [(f"{stem}_{title}.xlsx", path, True) for title, path in sheets]


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime) and value.time() == time(0):
        return value.date().isoformat()
    if isinstance(value, date | time):
        return value.isoformat()
    return value


def _tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' "
        "ORDER BY table_name"
    ).fetchall()
    return [r[0] for r in rows]


def list_tables(db: Path) -> list[dict[str, Any]]:
    """Every uploaded table with its row count and a profile of each column."""
    if not db.exists():
        return []
    out = []
    with open_readonly(db) as con:
        for name in _tables(con):
            (rows,) = con.execute(f'SELECT count(*) FROM "{name}"').fetchone() or (0,)  # noqa: S608
            summary = con.execute(f'SUMMARIZE "{name}"').fetchall()
            cols = [d[0] for d in con.description or []]
            notes = dict(
                con.execute(
                    "SELECT column_name, comment FROM duckdb_columns() "
                    "WHERE schema_name = 'main' AND table_name = ?",
                    [name],
                ).fetchall()
            )
            columns = []
            for values in summary:
                s = dict(zip(cols, values, strict=True))
                columns.append(
                    {
                        "name": s["column_name"],
                        "type": s["column_type"],
                        "min": s["min"],
                        "max": s["max"],
                        "distinct": s["approx_unique"],
                        "null_percent": float(s["null_percentage"] or 0),
                        "note": notes.get(s["column_name"]) or None,
                    }
                )
            (comment,) = con.execute(
                "SELECT comment FROM duckdb_tables() WHERE schema_name = 'main' AND table_name = ?",
                [name],
            ).fetchone() or (None,)
            keys = json.loads(comment[len(_KEYS) :]) if (comment or "").startswith(_KEYS) else None
            out.append({"name": name, "rows": rows, "columns": columns, "keys": keys})
    return out


def delete_table(db: Path, name: str) -> bool:
    """Drop one uploaded table. False if there is no such table."""
    with _LOCK:
        if not db.exists():
            return False
        with open_readonly(db) as con:
            if name not in _tables(con):
                return False
        tmp = db.with_name(db.name + ".importing")
        shutil.copyfile(db, tmp)
        try:
            with duckdb.connect(str(tmp), config=_CONFIG) as con:
                con.execute(f'DROP TABLE "{name}"')  # name matched an existing table above
            tmp.replace(db)
        finally:
            tmp.unlink(missing_ok=True)
        return True
