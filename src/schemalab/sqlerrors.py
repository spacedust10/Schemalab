"""SQL errors in plain English, with a fix to try.

Students see DuckDB's and the guard's messages, which are written for people who already know
what went wrong. explain() recognizes the common ones and says what happened and what to try,
naming the closest table, column or function when a name is misspelled. Anything it does not
recognize keeps the first line of the original message.
"""

import difflib
import re
from collections.abc import Callable, Mapping, Sequence

Explanation = dict[str, str]
Schema = Mapping[str, Sequence[str]]  # table -> its columns

_END_WORDS = ("WHERE", "AND", "OR", "ON", "BY", "SELECT", "FROM", "JOIN", "HAVING", "NOT", ",")


def _closest(word: str, options: Sequence[str]) -> str | None:
    found = difflib.get_close_matches(word.lower(), [o.lower() for o in options], n=1, cutoff=0.6)
    if not found:
        return None
    return next(o for o in options if o.lower() == found[0])


def _first_line(message: str) -> str:
    return message.strip().splitlines()[0] if message.strip() else message


def _column(m: re.Match[str], sql: str, schema: Schema) -> Explanation:
    name = m["name"]
    guess = _closest(name, [c for cols in schema.values() for c in cols])
    return {
        "title": f"No such column: {name}",
        "explanation": f"None of the tables your query reads has a column called {name}.",
        "fix": f'Did you mean "{guess}"?' if guess else "Check the column names in the schema.",
    }


def _table(m: re.Match[str], sql: str, schema: Schema) -> Explanation:
    name = m["name"]
    guess = _closest(name, list(schema))
    return {
        "title": f"No such table: {name}",
        "explanation": f"The database has no table called {name}.",
        "fix": f'Did you mean "{guess}"?' if guess else f"Tables here: {', '.join(schema)}.",
    }


def _group_by(m: re.Match[str], sql: str, schema: Schema) -> Explanation:
    col = m["name"]
    return {
        "title": f"{col} is not in GROUP BY",
        "explanation": (
            "With GROUP BY, each result row stands for a whole group, so every selected column "
            f"must either be one of the grouping columns or be summarized. {col} is neither, so "
            "the database cannot tell which of the group's values to show."
        ),
        "fix": f"Add {col} to GROUP BY, or summarize it, for example max({col}).",
    }


def _ambiguous(m: re.Match[str], sql: str, schema: Schema) -> Explanation:
    col = m["name"]
    options = re.findall(r"'([^']+)'", m["options"] or "")
    return {
        "title": f"Ambiguous column: {col}",
        "explanation": f"More than one table in the query has a column called {col}.",
        "fix": "Say which table it comes from: " + (" or ".join(options) or f"table.{col}") + ".",
    }


def _aggregate_in_where(m: re.Match[str], sql: str, schema: Schema) -> Explanation:
    return {
        "title": "Aggregate in WHERE",
        "explanation": (
            "WHERE filters single rows before any grouping, so it cannot use count(), sum() "
            "and other aggregates, which need a whole group."
        ),
        "fix": "Filter groups with HAVING after GROUP BY instead, for example "
        "GROUP BY ... HAVING count(*) > 1.",
    }


def _function(m: re.Match[str], sql: str, schema: Schema) -> Explanation:
    guess = m["guess"]
    return {
        "title": f"No such function: {m['name']}",
        "explanation": f"The database has no function called {m['name']}.",
        "fix": f'Did you mean "{guess}"?' if guess else "Check the function's spelling.",
    }


def _not_a_query(m: re.Match[str], sql: str, schema: Schema) -> Explanation:
    return {
        "title": "Only queries are allowed",
        "explanation": f"This database is read-only here, so {m['kind']} cannot run.",
        "fix": "Write a SELECT that returns the rows you want.",
    }


def _one_statement(m: re.Match[str], sql: str, schema: Schema) -> Explanation:
    return {
        "title": "One query at a time",
        "explanation": "Only one statement runs per answer; this text has more than one.",
        "fix": "Keep one SELECT and remove the rest (and any extra semicolons).",
    }


def _types(m: re.Match[str], sql: str, schema: Schema) -> Explanation:
    value, kind = m["value"], m["type"]
    return {
        "title": "Types do not match",
        "explanation": f"'{value}' was compared with or put into a {kind} column, and it is not "
        f"a {kind.lower()} value.",
        "fix": "Compare numbers with numbers: drop the quotes around a number, or compare the "
        "column with a value of its own type.",
    }


def _syntax(m: re.Match[str], sql: str, schema: Schema) -> Explanation:
    ending = sql.strip().rstrip(";").split()
    last = ending[-1].upper() if ending else ""
    if last in _END_WORDS or last.endswith(","):
        word = "," if last.endswith(",") else last
        fix = f"The query stops right after {word}; finish what comes after {word}, or remove it."
    else:
        near = m["near"] if "near" in m.groupdict() and m["near"] else None
        where = f" around {near}" if near else ""
        fix = (
            f"Check the words{where}: a missing comma, a misspelled keyword or an unclosed "
            "bracket are the usual causes."
        )
    return {
        "title": "Syntax error",
        "explanation": "The database could not read the query as SQL.",
        "fix": fix,
    }


_RULES: list[tuple[re.Pattern[str], Callable[[re.Match[str], str, Schema], Explanation]]] = [
    (re.compile(r'Referenced column "(?P<name>[^"]+)" not found'), _column),
    (re.compile(r"unknown table: (?P<name>\S+)"), _table),
    (re.compile(r"Table with name (?P<name>\S+) does not exist"), _table),
    (re.compile(r'column "(?P<name>[^"]+)" must appear in the GROUP BY'), _group_by),
    (
        re.compile(
            r'Ambiguous reference to column name "(?P<name>[^"]+)"(?: \(use: (?P<options>[^)]*)\))?'
        ),
        _ambiguous,
    ),
    (re.compile(r"WHERE clause cannot contain aggregates"), _aggregate_in_where),
    (
        re.compile(
            r"Function with name (?P<name>\S+) does not exist!"
            r'(?:\s*Did you mean "(?P<guess>[^"]+)")?'
        ),
        _function,
    ),
    (re.compile(r"only SELECT queries are allowed, got (?P<kind>\w+)"), _not_a_query),
    (re.compile(r"expected exactly one statement"), _one_statement),
    (re.compile(r"Could not convert string '(?P<value>[^']*)' to (?P<type>\w+)"), _types),
    (
        re.compile(r'syntax error at or near "(?P<near>[^"]*)"|could not parse SQL|Parser Error'),
        _syntax,
    ),
]


def explain(sql: str, message: str, schema: Schema) -> Explanation:
    for pattern, rule in _RULES:
        if found := pattern.search(message):
            return rule(found, sql, schema)
    return {
        "title": "The query failed",
        "explanation": _first_line(message),
        "fix": "Read the message above; if it names a table or column, check its spelling.",
    }
