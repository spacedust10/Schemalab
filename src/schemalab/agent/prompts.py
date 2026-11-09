"""Prompts for each model step. None of them contain row values."""

PLAN = """You plan how to answer a question with one SQL query over these DuckDB tables.

Write 1 to 4 short lines: which tables and columns to use, how to join them, what to filter,
group and aggregate. Group only by what the question asks about.
Add no filter the question does not ask for. Do not write SQL.
{notes}
Tables (row counts and column types; no data values are shown):

{schema}
"""

WRITE_SQL = """You write one DuckDB SQL query that answers the user's question.

Rules:
- Return only the SQL, in a ```sql fenced block.
- One SELECT statement. No writes, no DDL, no PRAGMA, no file or table functions.
- Use only the tables and columns listed below.
- Add no filter the question does not ask for.
- Add LIMIT 1000 unless the result is an aggregate.

Plan:
{plan}
{notes}
Tables (row counts and column types; no data values are shown):

{schema}
"""

RETRY = """Your previous query did not work.

Query:
{sql}

Problem:
{problem}

Write a corrected query."""

CRITIC = """You review whether a SQL query answers a question. You cannot see any data.

Question:
{question}

Plan:
{plan}

Query that ran:
{sql}

It returned {row_count} rows with columns: {columns}

Check: does the query group, filter and aggregate exactly as the question asks? Extra
GROUP BY columns, a filter the question does not ask for, a missing filter, or the wrong
aggregate are problems.
{notes}
Reply with JSON only: {{"ok": true}} or {{"ok": false, "issue": "<one sentence>"}}
"""

ANSWER = """Answer the question in one or two plain sentences using only this query result.
Do not add facts that are not in the result.

Question:
{question}

Result (columns: {columns}; first {shown} of {total} rows):
{rows}
"""

NOTES = """
Notes from the user about this data (treat them as facts):
{items}
"""


def notes_block(notes: list[str]) -> str:
    return NOTES.format(items="\n".join(f"- {n}" for n in notes)) if notes else ""
