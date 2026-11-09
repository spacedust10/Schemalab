"""User uploads: each .csv file or Excel sheet becomes a table in a DuckDB file of the user's own.

Imports are trusted code with a normal connection, like `datasets`. They build into a copy and
swap it in, so a failed upload leaves the user's tables as they were and queries running against
the old file are not disturbed. Model-written SQL only ever reads the result through
`guards.run.run_query`.
"""

import csv
import re
import shutil
import tempfile
import threading
import zipfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import duckdb
import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from schemalab.guards.engine import open_readonly

MAX_BYTES = 50 * 1024 * 1024
MAX_TABLES = 20
MAX_COLUMNS = 500
SUFFIXES = (".csv", ".xlsx")

# Trusted connection, but still no extension downloads.
_CONFIG: dict[str, str | bool | int | float | list[str]] = {
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
}
# ponytail: one lock for every user's imports; per-user locks if uploads ever queue up.
_LOCK = threading.Lock()


class UploadError(ValueError):
    """The file cannot be imported. The message is safe to show the user."""


def table_name(filename: str, taken: set[str]) -> str:
    """A lowercase [a-z0-9_] identifier from the file name, not starting with a digit."""
    base = Path(filename).name
    base = base.rsplit(".", 1)[0] if "." in base else base
    name = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")[:50] or "upload"
    if name[0].isdigit():
        name = f"t_{name}"
    unique, n = name, 2
    while unique in taken:
        unique, n = f"{name}_{n}", n + 1
    return unique


def import_file(db: Path, src: Path, filename: str) -> list[str]:
    """Add the file's tables to `db` and return their names."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUFFIXES:
        raise UploadError("Upload a .csv or .xlsx file.")
    with _LOCK, tempfile.TemporaryDirectory() as work:
        db.parent.mkdir(parents=True, exist_ok=True)
        tmp = db.with_name(db.name + ".importing")
        tmp.unlink(missing_ok=True)
        if db.exists():
            shutil.copyfile(db, tmp)
        try:
            with duckdb.connect(str(tmp), config=_CONFIG) as con:
                taken = set(_tables(con))
                if suffix == ".csv":
                    sources = [(filename, src, False)]
                else:
                    sources = _excel_to_csv(src, filename, Path(work))
                if len(taken) + len(sources) > MAX_TABLES:
                    raise UploadError(
                        f"You can keep at most {MAX_TABLES} tables. Delete one first."
                    )
                created = [
                    _load(con, table_name(n, taken), path, header, taken)
                    for n, path, header in sources
                ]
            tmp.replace(db)
            return created
        except duckdb.Error as e:
            detail = str(e).splitlines()[0]
            raise UploadError(f"Could not read {Path(filename).name}: {detail}") from None
        finally:
            tmp.unlink(missing_ok=True)


def _load(
    con: duckdb.DuckDBPyConnection, name: str, path: Path, header: bool, taken: set[str]
) -> str:
    # The name passed table_name's [a-z0-9_] filter; the path is bound. Excel sheets always have
    # a header row; for CSV files DuckDB detects one.
    options = "normalize_names = true" + (", header = true" if header else "")
    con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM read_csv(?, {options})', [str(path)])  # noqa: S608
    columns = len(con.execute(f'DESCRIBE "{name}"').fetchall())
    if columns > MAX_COLUMNS:
        raise UploadError(f"A table can have at most {MAX_COLUMNS} columns, not {columns}.")
    (rows,) = con.execute(f'SELECT count(*) FROM "{name}"').fetchone() or (0,)  # noqa: S608
    if rows == 0:
        raise UploadError("The file has no data rows.")
    taken.add(name)
    return name


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
                for row in ws.iter_rows(values_only=True):
                    if all(v is None for v in row):
                        continue
                    if len(row) > MAX_COLUMNS:
                        raise UploadError(f"A table can have at most {MAX_COLUMNS} columns.")
                    writer.writerow([_cell(v) for v in row])
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
                    }
                )
            out.append({"name": name, "rows": rows, "columns": columns})
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
