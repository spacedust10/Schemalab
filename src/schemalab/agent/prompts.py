"""Prompts for each model step. None of them contain row values."""

PLAN = """You plan how to answer a question with one SQL query over these DuckDB tables.

Write 1 to 4 short lines: which tables and columns to use, how to join them, what to filter,
group and aggregate. Group only by what the question asks about. Do not write SQL.

Tables (row counts and column types; no data values are shown):

{schema}
"""

WRITE_SQL = """You write one DuckDB SQL query that answers the user's question.

Rules:
- Return only the SQL, in a ```sql fenced block.
- One SELECT statement. No writes, no DDL, no PRAGMA, no file or table functions.
- Use only the tables and columns listed below.
- Add LIMIT 1000 unless the result is an aggregate.

Plan:
{plan}

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
GROUP BY columns, missing filters, or the wrong aggregate are problems.

Reply with JSON only: {{"ok": true}} or {{"ok": false, "issue": "<one sentence>"}}
"""

ANSWER = """Answer the question in one or two plain sentences using only this query result.
Do not add facts that are not in the result.

Question:
{question}

Result (columns: {columns}; first {shown} of {total} rows):
{rows}
"""
