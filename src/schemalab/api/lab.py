"""The transaction lab over HTTP: start, run in session A or B, poll, inspect, scenarios.

Labs live in this process: one per user, at most MAX_LABS at a time, closed after IDLE_S
without use. Each holds two PostgreSQL connections in the sandbox database.
"""

import logging
import threading
import time
from dataclasses import asdict
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, StringConstraints
from starlette.concurrency import run_in_threadpool

from schemalab.api.auth import UserDep
from schemalab.config import get_settings
from schemalab.lab import patterns
from schemalab.lab.guard import LabSQLError
from schemalab.lab.scenarios import BY_ID, SCENARIOS
from schemalab.lab.session import Lab, ServerBusy

router = APIRouter(prefix="/lab", tags=["lab"])

MAX_LABS = 20
IDLE_S = 20 * 60
Session = Literal["A", "B"]

_labs: dict[int, Lab] = {}
# Experiments open several connections each; this many may run at once across all users.
_pattern_slots = threading.BoundedSemaphore(4)
# What an experiment's failure means, written here rather than taken from the server.
_EXPLAINED = {
    "40P01": "a deadlock was detected and retrying did not get past it.",
    "40001": "a serialization failure was not resolved by retrying.",
    "57014": "a statement ran past the lab's 30-second limit.",
    "53300": "your lab has too many connections open; close a session and try again.",
}
logger = logging.getLogger(__name__)
_labs_lock = threading.Lock()


def get_lab_dsn() -> str:
    return get_settings().lab_admin_dsn


LabDsn = Annotated[str, Depends(get_lab_dsn)]


class RunRequest(BaseModel):
    session: Session
    sql: Annotated[str, StringConstraints(max_length=5000)]


class ScenarioRequest(BaseModel):
    id: Annotated[str, StringConstraints(max_length=100)]


def close_all() -> None:
    with _labs_lock:
        labs = list(_labs.values())
        _labs.clear()
    for lab in labs:
        lab.close()


def _expire() -> None:
    """Close labs idle too long, and the least recently used one if there is no room."""
    now = time.monotonic()
    with _labs_lock:
        stale = [uid for uid, lab in _labs.items() if now - lab.last_used > IDLE_S]
        if len(_labs) - len(stale) >= MAX_LABS:
            stale.append(min(_labs, key=lambda uid: _labs[uid].last_used))
        closing = [_labs.pop(uid) for uid in dict.fromkeys(stale) if uid in _labs]
    for lab in closing:
        lab.close()


def _lab(user: dict[str, Any]) -> Lab:
    lab = _labs.get(int(user["id"]))
    if lab is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Start the lab first.")
    lab.last_used = time.monotonic()
    return lab


def _start(user: dict[str, Any], dsn: str) -> Lab:
    if not dsn:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The transaction lab is not set up on this server."
        )
    _expire()
    uid = int(user["id"])
    existing = _labs.get(uid)
    if existing is not None:
        existing.reset()
        existing.last_used = time.monotonic()
        return existing
    lab = Lab(dsn, uid)
    lab.start()
    with _labs_lock:
        _labs[uid] = lab
    return lab


@router.post("/start")
async def start(user: UserDep, dsn: LabDsn) -> dict[str, Any]:
    lab = await run_in_threadpool(_start, user, dsn)
    return await run_in_threadpool(lab.state)


@router.post("/stop", status_code=status.HTTP_204_NO_CONTENT)
def stop(user: UserDep) -> None:
    with _labs_lock:
        lab = _labs.pop(int(user["id"]), None)
    if lab is not None:
        lab.close()


@router.post("/run")
def run(user: UserDep, body: RunRequest) -> dict[str, Any]:
    lab = _lab(user)
    try:
        return lab.run(body.session, body.sql)
    except LabSQLError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None


class ExplainRequest(BaseModel):
    sql: Annotated[str, StringConstraints(max_length=5000)]


@router.post("/explain")
def explain(user: UserDep, body: ExplainRequest) -> dict[str, Any]:
    """EXPLAIN ANALYZE in a third session, always rolled back, with index advice."""
    lab = _lab(user)
    try:
        return lab.explain(body.sql)
    except LabSQLError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    except psycopg.Error as e:
        detail = (e.diag.message_primary or str(e)).strip()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail) from None


class PatternRequest(BaseModel):
    id: Annotated[str, StringConstraints(max_length=40)]
    mode: Annotated[str, StringConstraints(max_length=40)]


@router.get("/patterns")
def pattern_list(user: UserDep) -> list[dict[str, Any]]:
    return patterns.CATALOG


@router.post("/pattern")
def pattern(user: UserDep, body: PatternRequest) -> dict[str, Any]:
    """Run one transaction-pattern experiment (fixed SQL) in the user's lab."""
    modes = {p["id"]: [m["id"] for m in p["modes"]] for p in patterns.CATALOG}
    if body.mode not in modes.get(body.id, []):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Choose an experiment and a way to run it."
        )
    lab = _lab(user)
    try:
        return lab.pattern(body.id, body.mode, slots=_pattern_slots)
    except ServerBusy as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None  # our own messages
    except psycopg.Error as e:
        # Experiments run fixed SQL, so PostgreSQL's own text adds nothing a user needs, and
        # some errors (connection, authentication) can name hosts and users. The reply is a
        # message written here for the error's SQLSTATE; the log gets the type and SQLSTATE.
        logger.warning(
            "pattern %s/%s stopped: %s (%s)", body.id, body.mode, type(e).__name__, e.sqlstate
        )
        detail = _EXPLAINED.get(e.sqlstate or "", "The experiment could not finish.")
        raise HTTPException(status.HTTP_409_CONFLICT, f"The experiment stopped: {detail}") from None


@router.get("/result")
def result(user: UserDep, session: Annotated[Session, Query()]) -> dict[str, Any]:
    return _lab(user).result(session) or {"state": "idle"}


@router.get("/state")
def lab_state(user: UserDep) -> dict[str, Any]:
    return _lab(user).state()


@router.get("/scenarios")
def scenarios(user: UserDep) -> list[dict[str, str]]:
    return [{"id": s.id, "title": s.title, "summary": s.summary} for s in SCENARIOS]


@router.post("/scenario")
async def scenario(user: UserDep, body: ScenarioRequest, dsn: LabDsn) -> dict[str, Any]:
    chosen = BY_ID.get(body.id)
    if chosen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such scenario.")
    lab = await run_in_threadpool(_start, user, dsn)
    for statement in chosen.setup:
        await run_in_threadpool(lab.run, "A", statement)
    return asdict(chosen)
