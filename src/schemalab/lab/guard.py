"""The lab runs SQL the user types, so it takes one statement at a time from an allow-list.

The sandbox role already cannot touch files, roles or other users' schemas; this guard is the
second layer. It also keeps the session's timeouts in place: only SET TRANSACTION and SET
SESSION CHARACTERISTICS may change settings, so statement_timeout stays on.
"""

import re

MAX_LENGTH = 5000

_ALLOWED = re.compile(
    r"""(
        SELECT | WITH | VALUES | TABLE | INSERT | UPDATE | DELETE | MERGE | TRUNCATE
      | CREATE \s+ (TEMP(ORARY)? \s+)? (UNIQUE \s+)? (TABLE | INDEX | VIEW)
      | DROP \s+ (TABLE | INDEX | VIEW) | ALTER \s+ TABLE
      | BEGIN | START \s+ TRANSACTION | COMMIT | END | ROLLBACK | ABORT | SAVEPOINT | RELEASE
      | SET \s+ TRANSACTION | SET \s+ SESSION \s+ CHARACTERISTICS
      | SHOW | LOCK | EXPLAIN
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)


class LabSQLError(ValueError):
    """The statement is not allowed in the lab. The message says what is."""


def _code(sql: str) -> tuple[str, int]:
    """The SQL with comments and string literals blanked out and quoted names unquoted, so
    "set_config" reads as set_config, plus the number of semicolons in it."""
    out: list[str] = []
    i, semicolons = 0, 0
    while i < len(sql):
        ch, nxt = sql[i], sql[i + 1 : i + 2]
        if ch == "-" and nxt == "-":
            end = sql.find("\n", i)
            i = len(sql) if end < 0 else end
        elif ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            i = len(sql) if end < 0 else end + 2
        elif ch in "'\"":
            if "".join(out[-2:]).upper() == "U&":
                raise LabSQLError("Unicode-escaped names and strings are not allowed in the lab.")
            j = i + 1
            while j < len(sql):
                if sql[j] == ch and sql[j + 1 : j + 2] == ch:
                    j += 2
                elif sql[j] == ch:
                    break
                else:
                    j += 1
            # A string literal is data; a double-quoted name is code and stays visible.
            out.append(" " if ch == "'" else " " + sql[i + 1 : j].replace('""', '"') + " ")
            i = j + 1
        elif ch == "$":
            raise LabSQLError(
                "Dollar-quoted bodies (functions, DO blocks) are not allowed in the lab."
            )
        else:
            if ch == ";":
                semicolons += 1
            out.append(ch)
            i += 1
    return "".join(out), semicolons


def check_lab_sql(sql: str) -> str:
    """Return the statement without a trailing semicolon, or raise LabSQLError."""
    if len(sql) > MAX_LENGTH:
        raise LabSQLError(f"Write at most {MAX_LENGTH} characters.")
    statement = sql.strip().rstrip(";").strip()
    if not statement:
        raise LabSQLError("Write a statement, like SELECT * FROM accounts.")
    code, semicolons = _code(statement)
    if semicolons:
        raise LabSQLError("Run one statement at a time, so you can see what each one does.")
    # set_config(...) and UPDATE pg_settings change settings without SET; the sandbox database
    # also revokes both from PUBLIC, so this is the first of two layers.
    for name in ("set_config", "pg_settings"):
        if re.search(rf"\b{name}\b", code, re.IGNORECASE):
            raise LabSQLError(f"{name} is not allowed; the lab's timeouts stay on.")
    words = code.split()
    first = words[0].upper()
    if first == "SET" and not _ALLOWED.match(code.strip()):
        raise LabSQLError(
            "Only SET TRANSACTION and SET SESSION CHARACTERISTICS are allowed; "
            "the lab's timeouts stay on."
        )
    if not _ALLOWED.match(code.strip()):
        raise LabSQLError(
            f"{first} is not allowed in the lab. Use queries, INSERT/UPDATE/DELETE, tables and "
            "indexes, transaction control (BEGIN, COMMIT, ROLLBACK, SAVEPOINT), LOCK, SHOW and "
            "EXPLAIN."
        )
    return statement
