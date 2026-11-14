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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.concurrency import run_in_threadpool

from schemalab import assignments as asg
from schemalab import canvas
from schemalab.agent.graph import json_value
from schemalab.api.auth import UserDep, get_engine
from schemalab.config import get_settings
from schemalab.guards.engine import open_readonly
from schemalab.store import assignments, attempt_slots, submission_tests, submissions, users

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
    """Claim the next attempt slot and return its number, or None when none is left.

    The number is one past both the highest slot and the submissions recorded (attempts made
    before slots existed), and it must not exceed the limit. Each number can be claimed once,
    so however many requests race, at most `limit` attempts get through; a request that loses
    a number looks again."""
    for _ in range(limit + 20):
        with engine.connect() as conn:
            highest = conn.execute(
                select(func.max(attempt_slots.c.attempt_no)).where(
                    attempt_slots.c.assignment_id == aid, attempt_slots.c.user_id == user_id
                )
            ).scalar_one()
            made = conn.execute(
                select(func.count()).where(
                    submissions.c.assignment_id == aid, submissions.c.user_id == user_id
                )
            ).scalar_one()
        number = max(highest or 0, int(made)) + 1
        if number > limit:
            return None
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
    except asg.GraderUnavailable:
        # Our failure (building the databases, running the reference), never the student's:
        # the attempt is given back. Anything else keeps it, so a submission crafted to break
        # the grader cannot buy more attempts.
        if slot is not None:
            _release(engine, aid, user["id"], slot)
        raise
    with engine.begin() as conn:
        submission_id = conn.execute(
            insert(submissions)
            .values(
                assignment_id=aid,
                user_id=user["id"],
                sql=body.sql,
                score=result["score"],
                created_at=datetime.now(UTC),
            )
            .returning(submissions.c.id)
        ).scalar_one()
        conn.execute(
            insert(submission_tests).values(
                submission_id=submission_id,
                tests=[{"name": t["name"], "passed": t["passed"]} for t in result["tests"]],
            )
        )
    _, best = _attempts(engine, aid, user["id"])
    used = _slots_used(engine, aid, user["id"])
    result["attempts_left"] = None if limit is None else max(0, limit - used)
    result["best"] = best
    return result


# The owner's view


def _owned(engine: Engine, aid: str, user: dict[str, Any]) -> asg.Assignment:
    """The assignment if the caller created it; 404 otherwise, so others learn nothing."""
    if aid.startswith("sample:"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such assignment.")
    a, _, _, owner = _found(engine, aid)
    if owner != user["id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such assignment.")
    return a


def _answers(engine: Engine, aid: str) -> list[Any]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                select(
                    users.c.email,
                    submissions.c.id,
                    submissions.c.score,
                    submissions.c.sql,
                    submissions.c.created_at,
                    submission_tests.c.tests,
                )
                .join(users, users.c.id == submissions.c.user_id)
                .outerjoin(submission_tests, submission_tests.c.submission_id == submissions.c.id)
                .where(submissions.c.assignment_id == aid)
                .order_by(submissions.c.created_at, submissions.c.id)
            ).all()
        )


@router.get("/{aid}/dashboard")
def dashboard(aid: str, user: UserDep, engine: EngineDep) -> dict[str, Any]:
    a = _owned(engine, aid, user)
    rows = _answers(engine, aid)
    by_student: dict[str, list[Any]] = {}
    for row in rows:
        by_student.setdefault(row.email, []).append(row)
    students = []
    for email, mine in sorted(by_student.items()):
        latest = mine[-1]
        students.append(
            {
                "email": email,
                "attempts": len(mine),
                "best": max(r.score for r in mine),
                "latest": latest.score,
                "last_at": latest.created_at.isoformat(),
            }
        )
    # Pass rates over each student's latest answer that recorded its tests.
    latest_tests = [mine[-1].tests for mine in by_student.values() if mine[-1].tests]
    tests = [
        {
            "name": name,
            "passed": sum(
                1 for t in latest_tests if any(x["name"] == name and x["passed"] for x in t)
            ),
            "of": len(latest_tests),
        }
        for name in [asg.SAMPLE, *[n for n, _ in a.hidden]]
    ]
    best = sorted(s["best"] for s in students)
    middle = len(best) // 2
    summary: dict[str, Any] = {"students": len(students), "submissions": len(rows)}
    if best:
        median = best[middle] if len(best) % 2 else (best[middle - 1] + best[middle]) / 2
        summary.update(
            mean=round(sum(best) / len(best), 1), median=median, min=best[0], max=best[-1]
        )
    return {"title": a.title, "students": students, "tests": tests, "summary": summary}


@router.get("/{aid}/submissions")
def student_answers(
    aid: str, user: UserDep, engine: EngineDep, email: Annotated[str, Query(max_length=254)]
) -> list[dict[str, Any]]:
    _owned(engine, aid, user)
    return [
        {"sql": r.sql, "score": r.score, "at": r.created_at.isoformat()}
        for r in _answers(engine, aid)
        if r.email == email.strip().lower()
    ]


MAX_EXPORT = 5 * 1024 * 1024  # a Canvas gradebook export for a large course fits easily


def _best_scores(engine: Engine, aid: str) -> list[tuple[str, int]]:
    best: dict[str, int] = {}
    for r in _answers(engine, aid):
        best[r.email] = max(best.get(r.email, 0), r.score)
    return sorted(best.items())


@router.get("/{aid}/canvas.csv")
def canvas_csv(aid: str, user: UserDep, engine: EngineDep) -> Response:
    a = _owned(engine, aid, user)
    text, _ = canvas.gradebook(a.title, _best_scores(engine, aid))
    return Response(
        text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="canvas-grades.csv"'},
    )


@router.post("/{aid}/canvas.csv")
async def canvas_csv_matched(
    aid: str, request: Request, user: UserDep, engine: EngineDep
) -> dict[str, Any]:
    """The same CSV, with Canvas IDs filled in from the teacher's own gradebook export (the
    request body). The export is read here and not kept."""
    a = await run_in_threadpool(_owned, engine, aid, user)
    body = await request.body()
    if len(body) > MAX_EXPORT:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The export is larger than 5 MB.")
    export = body.decode("utf-8-sig", errors="replace")
    scores = await run_in_threadpool(_best_scores, engine, aid)
    text, unmatched = canvas.gradebook(a.title, scores, export)
    return {"csv": text, "unmatched": unmatched}
