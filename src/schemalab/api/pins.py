"""Pinned answers for the dashboard. Each pin keeps its question and SQL, and listing pins
re-runs every query through the SQL guard and the locked engine, so the dashboard is current.
"""

from pathlib import Path
from typing import Annotated, Any, Literal

import duckdb
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, StringConstraints

from schemalab import store
from schemalab.agent.graph import json_value
from schemalab.api.auth import EngineDep, UserDep
from schemalab.api.datasets import SampleDb, UploadsDir, user_db
from schemalab.guards.run import QueryTimeoutError, run_query
from schemalab.guards.sql import UnsafeSQLError

router = APIRouter(prefix="/pins", tags=["pins"])

MAX_PINS = 12
MAX_ROWS = 200  # a dashboard card shows a chart and a short table, not a full export


class PinRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    sql: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
    dataset: Literal["sample", "mine"] = "sample"


def _run(sql: str, db: Path) -> dict[str, Any]:
    """The query's result, or the reason it cannot run. Never raises for a bad query."""
    failed = {"columns": None, "rows": None, "truncated": False}
    if not db.exists():
        return {**failed, "error": "No tables uploaded."}
    try:
        result = run_query(db, sql, max_rows=MAX_ROWS)
    except (UnsafeSQLError, QueryTimeoutError, duckdb.Error) as e:
        return {**failed, "error": str(e).splitlines()[0]}
    rows = [[json_value(v) for v in row] for row in result.rows]
    return {"columns": result.columns, "rows": rows, "truncated": result.truncated, "error": None}


@router.post("", status_code=status.HTTP_201_CREATED)
def add(
    user: UserDep, body: PinRequest, engine: EngineDep, sample: SampleDb, uploads: UploadsDir
) -> dict[str, Any]:
    db = sample if body.dataset == "sample" else user_db(uploads, user)
    outcome = _run(body.sql, db)
    if outcome["error"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"This query cannot be pinned: {outcome['error']}"
        )
    with engine.begin() as conn:
        if len(store.list_pins(conn, user["id"])) >= MAX_PINS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"You can pin at most {MAX_PINS} answers. Remove one first.",
            )
        return {"id": store.add_pin(conn, user["id"], body.question, body.sql, body.dataset)}


@router.get("")
def list_all(
    user: UserDep, engine: EngineDep, sample: SampleDb, uploads: UploadsDir
) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        pins = store.list_pins(conn, user["id"])
    mine = user_db(uploads, user)
    return [{**p, **_run(p["sql"], sample if p["dataset"] == "sample" else mine)} for p in pins]


@router.delete("/{pin_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(pin_id: int, user: UserDep, engine: EngineDep) -> None:
    with engine.begin() as conn:
        if not store.delete_pin(conn, user["id"], pin_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such pin.")
