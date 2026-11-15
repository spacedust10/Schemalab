"""Canvas gradebook CSV: a file a teacher imports into Canvas to set one assignment's grades.

Canvas reads the columns Student, ID, SIS User ID, SIS Login ID and Section, then one column
per assignment, with an optional "Points Possible" row. It matches students by Canvas ID, then
SIS User ID, then SIS Login ID, never by email. Schemalab knows students only by email, so by
default the email goes in Student and SIS Login ID, which matches where a school's logins are
emails. A teacher can also pass their own Canvas gradebook export: students whose SIS Login ID
is their email then get Canvas's own ID, SIS User ID, name and section, which always match.
Its column for the same assignment (headed "Title (12345)") is reused, so the import updates
that assignment instead of creating a second one. The export is read, not kept.
"""

import csv
import io
import re

COLUMNS = ["Student", "ID", "SIS User ID", "SIS Login ID", "Section"]
POINTS = 100


# Spreadsheets skip these before deciding whether a cell is a formula.
_SKIPPED = " \t\r\n\u00a0\u200b\u200c\u200d\u2060\ufeff"
_FORMULA = set("=+-@|%")


def defuse(cell: str) -> str:
    """A cell a spreadsheet would run as a formula (after leading spaces and invisible
    characters, = + - @ | %) gets a leading ' so it stays text."""
    if cell[:1] in ("\t", "\r") or cell.lstrip(_SKIPPED)[:1] in _FORMULA:
        return f"'{cell}"
    return cell


def _roster(export: str) -> dict[str, list[str]]:
    """Each student's five identifying cells from a Canvas gradebook export, by login."""
    out: dict[str, list[str]] = {}
    for row in csv.DictReader(io.StringIO(export.lstrip("﻿"))):
        name = (row.get("Student") or "").strip()
        login = (row.get("SIS Login ID") or "").strip()
        if not login or not (row.get("ID") or "").strip() or name.startswith("Points Possible"):
            continue
        out[login.lower()] = [(row.get(c) or "").strip() for c in COLUMNS]
    return out


_HEADER = re.compile(r"^(?P<title>.*) \((?P<id>\d+)\)$")


def _column(title: str, export: str) -> str:
    """The export's own header for this assignment ("Title (12345)"), so importing updates
    that Canvas assignment; the bare title, which makes Canvas create one, if none matches."""
    header = next(csv.reader(io.StringIO(export.lstrip("\ufeff"))), [])
    for cell in header[len(COLUMNS) :]:
        found = _HEADER.match(cell.strip())
        if found and found["title"].strip() == title.strip():
            return cell.strip()
    return title


def gradebook(
    title: str, scores: list[tuple[str, int]], export: str | None = None
) -> tuple[str, list[str]]:
    """(CSV text, emails not found in the export) for (email, score) pairs."""
    roster = _roster(export) if export else {}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([*COLUMNS, defuse(_column(title, export) if export else title)])
    writer.writerow(["Points Possible", "", "", "", "", str(POINTS)])
    unmatched = []
    for email, score in scores:
        found = roster.get(email.lower())
        if found is None:
            if export:
                unmatched.append(email)
            found = [email, "", "", email, ""]
        writer.writerow([*(defuse(c) for c in found), str(score)])
    return out.getvalue(), unmatched
