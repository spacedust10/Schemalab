"""Canvas gradebook CSV: a file a teacher imports into Canvas to set one assignment's grades.

Canvas reads the columns Student, ID, SIS User ID, SIS Login ID and Section, then one column
per assignment, with an optional "Points Possible" row. It matches students by Canvas ID, then
SIS User ID, then SIS Login ID, never by email. Schemalab knows students only by email, so by
default the email goes in Student and SIS Login ID, which matches where a school's logins are
emails. A teacher can also pass their own Canvas gradebook export: students whose SIS Login ID
is their email then get Canvas's own ID, SIS User ID, name and section, which always match.
The export is read, not kept.
"""

import csv
import io

COLUMNS = ["Student", "ID", "SIS User ID", "SIS Login ID", "Section"]
POINTS = 100


def defuse(cell: str) -> str:
    """Spreadsheets run a cell starting with = + - @ as a formula; a leading ' stops that."""
    return f"'{cell}" if cell[:1] in ("=", "+", "-", "@", "\t", "\r") else cell


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


def gradebook(
    title: str, scores: list[tuple[str, int]], export: str | None = None
) -> tuple[str, list[str]]:
    """(CSV text, emails not found in the export) for (email, score) pairs."""
    roster = _roster(export) if export else {}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([*COLUMNS, defuse(title)])
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
