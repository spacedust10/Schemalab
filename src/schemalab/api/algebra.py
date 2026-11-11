"""POST /algebra: relational algebra <-> SQL with a query tree and a guarded run.
GET /constraints: the constraint coach for one table."""

from pathlib import Path
from typing import Annotated, Any, Literal

import duckdb
import sqlglot
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, StringConstraints, model_validator
from starlette.concurrency import run_in_threadpool

from schemalab import constraints, ra
from schemalab.agent.graph import json_value
from schemalab.api.auth import UserDep
from schemalab.api.datasets import SampleDb, UploadsDir, user_db
from schemalab.guards.engine import open_readonly
from schemalab.guards.run import QueryTimeoutError, run_query
from schemalab.guards.sql import UnsafeSQLError

router = APIRouter(tags=["algebra"])

MAX_ROWS = 200
Dataset = Literal["sample", "mine"]


class AlgebraRequest(BaseModel):
    expression: Annotated[str, StringConstraints(max_length=5000)] | None = None
    sql: Annotated[str, StringConstraints(max_length=20_000)] | None = None
    dataset: Dataset = "sample"

    @model_validator(mode="after")
    def one_input(self) -> "AlgebraRequest":
        if (self.expression is None) == (self.sql is None):
            raise ValueError("Send either an expression or sql, not both.")
        return self


def _database(dataset: Dataset, sample: Path, uploads: Path, user: dict[str, Any]) -> Path:
    db = sample if dataset == "sample" else user_db(uploads, user)
    if not db.exists():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a file first.")
    return db


def _tables(db: Path) -> list[str]:
    with open_readonly(db) as con:
        rows = con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main' ORDER BY 1"
        ).fetchall()
    return [r[0] for r in rows]


def _relations(node: ra.Node) -> set[str]:
    own = {str(node.arg)} if node.op == "relation" else set()
    return own.union(*(_relations(c) for c in node.children))


@router.post("/algebra")
def algebra(
    user: UserDep, body: AlgebraRequest, sample: SampleDb, uploads: UploadsDir
) -> dict[str, Any]:
    db = _database(body.dataset, sample, uploads, user)
    notes: list[str] = []
    try:
        if body.expression is not None:
            node = ra.parse_ra(body.expression)
        else:
            translation = ra.from_sql(body.sql or "")
            node, notes = translation.node, translation.notes
    except ra.RAError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None

    tables = _tables(db)
    lowered = {t.lower() for t in tables}
    for name in sorted(_relations(node)):
        if name.lower() not in lowered:
            detail = f"{name} is not a table here. Tables: {', '.join(tables)}."
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail)

    sql = ra.to_sql(node)
    pretty = sqlglot.transpile(sql, read="duckdb", pretty=True)[0]
    result: dict[str, Any] | None = None
    run_error: str | None = None
    try:
        # The same guard and locked engine as the analyst: one read-only SELECT, row cap, timeout.
        rows = run_query(db, sql, max_rows=MAX_ROWS)
        result = {
            "columns": rows.columns,
            "rows": [[json_value(v) for v in r] for r in rows.rows],
            "truncated": rows.truncated,
        }
    except (UnsafeSQLError, QueryTimeoutError, duckdb.Error) as e:
        run_error = str(e).splitlines()[0]
    return {
        "ra": ra.to_ra(node),
        "sql": pretty,
        "tree": ra.tree(node),
        "notes": notes,
        "result": result,
        "run_error": run_error,
    }


@router.get("/constraints")
async def coach(
    user: UserDep,
    sample: SampleDb,
    uploads: UploadsDir,
    table: Annotated[str, Query(min_length=1, max_length=200)],
    dataset: Dataset = "sample",
) -> list[dict[str, Any]]:
    db = _database(dataset, sample, uploads, user)
    try:
        found = await run_in_threadpool(constraints.suggest, db, table)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No table named {table}.") from None
    except TimeoutError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    return [vars(s) for s in found]
