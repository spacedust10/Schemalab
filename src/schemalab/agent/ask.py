"""One pass of the analyst, as a stream of events the UI can show step by step.

question -> schema (no rows) -> model writes SQL -> guard -> read-only run -> rows

The retry-and-critic loop replaces this single pass later; the event shapes stay the same.
"""

import re
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from schemalab.agent.schema import describe_schema
from schemalab.guards.run import QueryTimeoutError, run_query
from schemalab.guards.sql import UnsafeSQLError
from schemalab.llm import AllModelsFailedError, Gateway

Event = dict[str, Any]

_FENCE = re.compile(r"```(?:sql)?\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)

SYSTEM_PROMPT = """You write one DuckDB SQL query that answers the user's question.

Rules:
- Return only the SQL, in a ```sql fenced block.
- One SELECT statement. No writes, no DDL, no PRAGMA, no file or table functions.
- Use only the tables and columns listed below.
- Add LIMIT 1000 unless the result is an aggregate.

Tables (row counts and column types; no data values are shown):

{schema}
"""


def extract_sql(text: str) -> str:
    """Take the SQL out of a model reply: the first fenced block, else the whole reply."""
    match = _FENCE.search(text)
    return (match.group(1) if match else text).strip()


def ask(question: str, db_path: Path, gateway: Gateway) -> Iterator[Event]:
    schema = describe_schema(db_path)
    yield {"step": "schema", "tables": schema.count(" rows)")}

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(schema=schema)},
        {"role": "user", "content": question},
    ]
    try:
        reply = gateway.complete(messages)
    except AllModelsFailedError as e:
        yield {"step": "error", "stage": "model", "message": str(e)}
        return

    raw_sql = extract_sql(reply.text)
    try:
        result = run_query(db_path, raw_sql)
    except UnsafeSQLError as e:
        yield {"step": "error", "stage": "guard", "message": str(e), "sql": raw_sql}
        return
    except (QueryTimeoutError, duckdb.Error) as e:
        yield {"step": "error", "stage": "run", "message": str(e), "sql": raw_sql}
        return

    yield {
        "step": "sql",
        "sql": result.sql,
        "model": reply.model,
        "failed_models": reply.failed,
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
        "latency_ms": round(reply.latency_ms),
    }
    yield {
        "step": "result",
        "columns": result.columns,
        "rows": [[_json_value(v) for v in row] for row in result.rows],
        "truncated": result.truncated,
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
