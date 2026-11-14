"""SQL assignments: build (with checks), share by link, take in tutor or exam mode.

Handlers are plain functions: FastAPI runs them in its thread pool, so database calls and
grading never block the event loop.

The reference query and hidden tests are shown to the assignment's owner only. Students see
the prompt, the schema, the sample rows and the names of the tests; in exam mode they get a
fixed number of attempts and never see hidden rows.
"""

import secrets
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.exc import IntegrityError, OperationalError

from schemalab import assignments as asg
from schemalab.agent.graph import json_value
from schemalab.api.auth import UserDep, get_engine
from schemalab.config import get_settings
from schemalab.guards.engine import open_readonly
from schemalab.store import assignments, attempt_slots, submissions

router = APIRouter(prefix="/assignments", tags=["assignments"])
SAMPLE_ROWS = 50


def get_assignments_dir() -> Path:
    return Path(get_settings().assignments_dir)


CacheDir = Annotated[Path, Depends(get_assignments_dir)]
EngineDep = Annotated[Engine, Depends(get_engine)]
Text = Annotated[str, StringConstraints(min_length=1, max_length=200_000)]


class Hidden(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    data: Text


class AssignmentBody(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    schema_: Annotated[str, StringConstraints(min_length=1, max_length=100_000)] = Field(
        alias="schema"
    )
    data: Text
    reference: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    hidden: Annotated[list[Hidden], Field(max_length=10)] = []
    ordered: bool = False
    mode: Literal["tutor", "exam"] = "tutor"
    max_attempts: Annotated[int, Field(ge=1, le=20)] = 3

    def assignment(self) -> asg.Assignment:
        return asg.Assignment(
            title=self.title,
            prompt=self.prompt,
            schema=self.schema_,
            data=self.data,
            reference=self.reference,
            hidden=[(h.name, h.data) for h in self.hidden],
            ordered=self.ordered,
        )


class Submission(BaseModel):
    sql: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]


def _found(engine: Engine, aid: str) -> tuple[asg.Assignment, str, int | None, int | None]:
    """(assignment, mode, attempt limit, owner id) or 404."""
    if aid.startswith("sample:") and aid[7:] in asg.SAMPLES:
        return asg.SAMPLES[aid[7:]], "tutor", None, None
    with engine.connect() as conn:
        row = conn.execute(select(assignments).where(assignments.c.id == aid)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such assignment.")
    spec = dict(row.spec)
    spec["hidden"] = [tuple(h) for h in spec["hidden"]]
    return asg.Assignment(**spec), row.mode, row.max_attempts, row.owner_id


def _attempts(engine: Engine, aid: str, user_id: int) -> tuple[int, int | None]:
    with engine.connect() as conn:
        count, best = conn.execute(
            select(func.count(), func.max(submissions.c.score)).where(
                submissions.c.assignment_id == aid, submissions.c.user_id == user_id
            )
        ).one()
    return int(count), best


def _sample_tables(a: asg.Assignment, cache: Path) -> dict[str, Any]:
    return _table_rows(asg.database(cache, a.schema, a.data))


def _table_rows(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with open_readonly(path) as con:
        for table, columns in asg.schema_of(path).items():
            # Names come from the catalog; quoted and escaped all the same.
            quoted = '"' + table.replace('"', '""') + '"'
            rows = con.execute(f"SELECT * FROM {quoted} LIMIT {SAMPLE_ROWS}").fetchall()  # noqa: S608
            out[table] = {"columns": columns, "rows": [[json_value(v) for v in r] for r in rows]}
    return out


def reserve_attempt(engine: Engine, aid: str, user_id: int, limit: int) -> int | None:
    """Claim the next attempt slot and return its number, or None when all `limit` are used.
    Slots are numbered after the highest existing one; two requests that pick the same number
    at once collide on the key, and the loser looks again."""
    for _ in range(limit + 20):
        if _slots_used(engine, aid, user_id) >= limit:
            return None
        with engine.connect() as conn:
            highest = conn.execute(
                select(func.max(attempt_slots.c.attempt_no)).where(
                    attempt_slots.c.assignment_id == aid, attempt_slots.c.user_id == user_id
                )
            ).scalar_one()
        number = (highest or 0) + 1
        try:
            with engine.begin() as conn:
                conn.execute(
                    insert(attempt_slots).values(
                        assignment_id=aid, user_id=user_id, attempt_no=number
                    )
                )
            return number
        except IntegrityError:
            continue  # another request took that number first
        except OperationalError:
            time.sleep(0.02)  # SQLite busy with another writer; this runs in a worker thread
    return None


def _release(engine: Engine, aid: str, user_id: int, number: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            delete(attempt_slots).where(
                attempt_slots.c.assignment_id == aid,
                attempt_slots.c.user_id == user_id,
                attempt_slots.c.attempt_no == number,
            )
        )


def _slots_used(engine: Engine, aid: str, user_id: int) -> int:
    """Attempts used: claimed slots, or recorded submissions if more (attempts made before
    slots were claimed still count)."""
    with engine.connect() as conn:
        slots = conn.execute(
            select(func.count()).where(
                attempt_slots.c.assignment_id == aid, attempt_slots.c.user_id == user_id
            )
        ).scalar_one()
        made = conn.execute(
            select(func.count()).where(
                submissions.c.assignment_id == aid, submissions.c.user_id == user_id
            )
        ).scalar_one()
    return max(int(slots), int(made))


@router.get("")
def overview(user: UserDep, engine: EngineDep) -> list[dict[str, Any]]:
    out = [
        {"id": f"sample:{key}", "title": a.title, "mode": "tutor", "mine": False}
        for key, a in asg.SAMPLES.items()
    ]
    with engine.connect() as conn:
        rows = conn.execute(
            select(assignments.c.id, assignments.c.spec, assignments.c.mode)
            .where(assignments.c.owner_id == user["id"])
            .order_by(assignments.c.created_at.desc())
        ).all()
    out += [{"id": r.id, "title": r.spec["title"], "mode": r.mode, "mine": True} for r in rows]
    return out


@router.post("/check")
def check(user: UserDep, body: AssignmentBody, cache: CacheDir) -> dict[str, list[str]]:
    return asg.check(body.assignment(), cache)


@router.post("", status_code=status.HTTP_201_CREATED)
def create(
    user: UserDep, body: AssignmentBody, cache: CacheDir, engine: EngineDep
) -> dict[str, Any]:
    a = body.assignment()
    found = asg.check(a, cache)
    if found["errors"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, found)
    aid = secrets.token_urlsafe(12)
    spec = asdict(a)
    with engine.begin() as conn:
        conn.execute(
            insert(assignments).values(
                id=aid,
                owner_id=user["id"],
                spec=spec,
                mode=body.mode,
                max_attempts=body.max_attempts,
                created_at=datetime.now(UTC),
            )
        )
    return {"id": aid, "warnings": found["warnings"]}


@router.get("/{aid}")
def view(aid: str, user: UserDep, engine: EngineDep, cache: CacheDir) -> dict[str, Any]:
    a, mode, limit, owner = _found(engine, aid)
    _, best = _attempts(engine, aid, user["id"])
    used = _slots_used(engine, aid, user["id"])
    out: dict[str, Any] = {
        "id": aid,
        "title": a.title,
        "prompt": a.prompt,
        "schema": a.schema,
        "tables": _sample_tables(a, cache),
        "tests": [asg.SAMPLE, *[name for name, _ in a.hidden]],
        "ordered": a.ordered,
        "mode": mode,
        "attempts_left": None if limit is None else max(0, limit - used),
        "best": best,
    }
    if owner == user["id"]:  # the owner may see and edit everything
        out.update(
            reference=a.reference,
            hidden=[{"name": n, "data": d} for n, d in a.hidden],
            max_attempts=limit,
        )
    return out


@router.post("/{aid}/submit")
def submit(
    aid: str, body: Submission, user: UserDep, engine: EngineDep, cache: CacheDir
) -> dict[str, Any]:
    a, mode, limit, _ = _found(engine, aid)
    slot = None if limit is None else reserve_attempt(engine, aid, user["id"], limit)
    if limit is not None and slot is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"No attempts left (the limit is {limit}).")
    try:
        result = asg.grade(a, body.sql, mode, cache)  # type: ignore[arg-type]
    except Exception:
        if slot is not None:  # our failure, not the student's: the attempt is given back
            _release(engine, aid, user["id"], slot)
        raise
    with engine.begin() as conn:
        conn.execute(
            insert(submissions).values(
                assignment_id=aid,
                user_id=user["id"],
                sql=body.sql,
                score=result["score"],
                created_at=datetime.now(UTC),
            )
        )
    _, best = _attempts(engine, aid, user["id"])
    used = _slots_used(engine, aid, user["id"])
    result["attempts_left"] = None if limit is None else max(0, limit - used)
    result["best"] = best
    return result
