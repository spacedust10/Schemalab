"""/lakehouse: load data into the caller's lake, write silver and gold models, see runs.

Loads and transforms run in a background thread: one per user at a time, MAX_RUNS in all.
"""

import logging
import secrets
import tempfile
import threading
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from schemalab import store, uploads
from schemalab.api.auth import EngineDep, UserDep
from schemalab.config import get_settings
from schemalab.lakehouse import (
    LAYERS,
    MAX_CHECKS,
    MAX_MODELS,
    MAX_SQL,
    TABLE,
    Ingest,
    LakehouseError,
    Unavailable,
    check_models,
    lake_of,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/lakehouse", tags=["lakehouse"])

MAX_RUNS = 2  # loads at once across all users; dlt and DuckDB each want a core
SUFFIXES = (".csv", ".xlsx", ".parquet")
TOO_LARGE = f"Files can be at most {uploads.MAX_BYTES // (1024 * 1024)} MB."
UNAVAILABLE = "The lakehouse service is not answering. Try again in a minute."

# ponytail: in-process state, right for the one uvicorn worker Dockerfile.api starts. With
# several workers the caps would be per worker; move them to Postgres (advisory locks) then.
_LOCK = threading.Lock()
_ACTIVE: set[int] = set()  # users with a run going in this process
_RUNNING: set[int] = set()  # run ids this process is running; any other "running" row is stale
_SLOTS = threading.BoundedSemaphore(MAX_RUNS)
INTERRUPTED = "The run was interrupted. Start it again."


def _spawn(job: Callable[[], None]) -> None:
    threading.Thread(target=job, daemon=True).start()


@lru_cache
def _client(url: str, token: str) -> Ingest:
    return Ingest(url, token)


def get_ingest() -> Ingest:
    s = get_settings()
    if not s.ingest_url:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The lakehouse is not set up here."
        )
    return _client(s.ingest_url, s.ingest_token)


def get_lake_dir() -> Path:
    return Path(get_settings().lake_dir)


IngestDep = Annotated[Ingest, Depends(get_ingest)]
LakeDir = Annotated[Path, Depends(get_lake_dir)]


def _reserve(user_id: int) -> None:
    with _LOCK:
        if user_id in _ACTIVE:
            raise HTTPException(status.HTTP_409_CONFLICT, "A run is already going on your lake.")
        if not _SLOTS.acquire(blocking=False):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "The lakehouse is busy. Try again in a minute."
            )
        _ACTIVE.add(user_id)


def _release(user_id: int) -> None:
    with _LOCK:
        _ACTIVE.discard(user_id)
        _SLOTS.release()


def _start(
    engine: Engine, user_id: int, kind: str, label: str, job: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """Records the run and starts it; the caller has already reserved a slot."""
    try:
        with engine.begin() as conn:
            run_id = store.add_lake_run(conn, user_id, kind, label)
    except BaseException:
        _release(user_id)
        raise
    with _LOCK:
        _RUNNING.add(run_id)

    def work() -> None:
        result, error = None, None
        try:
            result = job()
        except LakehouseError as e:
            error = str(e)
        except Unavailable:
            error = UNAVAILABLE
        except Exception:
            log.exception("lake run %s failed", run_id)
            error = "The run failed. Try again, or try a smaller file."
        finally:
            try:
                with engine.begin() as conn:
                    store.finish_lake_run(conn, run_id, result, error)
            finally:
                with _LOCK:
                    _RUNNING.discard(run_id)
                _release(user_id)

    _spawn(work)
    return {"id": run_id, "status": "running"}


def _call(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except LakehouseError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    except Unavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None


@router.get("")
def overview(user: UserDep, ingest: IngestDep, engine: EngineDep) -> dict[str, Any]:
    lake = lake_of(user)
    with engine.begin() as conn:
        runs = store.list_lake_runs(conn, user["id"])
        for run in runs:
            # A run no thread here is running was cut off by a restart: record that for good.
            if run["status"] == "running" and run["id"] not in _RUNNING:
                store.finish_lake_run(conn, run["id"], None, INTERRUPTED)
                run.update(status="failed", error=INTERRUPTED)
        models = store.get_lake_models(conn, user["id"])
    # Runs and models live here, so they still show when ingest is down.
    try:
        tables, snapshots, available = ingest.tables(lake), ingest.snapshots(lake), True
    except Unavailable:
        tables, snapshots, available = [], [], False
    except LakehouseError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    return {
        "tables": tables,
        "snapshots": snapshots,
        "runs": runs,
        "models": models,
        "available": available,
    }


@router.get("/tables/{layer}/{table}")
def preview(
    user: UserDep, ingest: IngestDep, layer: str, table: str, version: int | None = None
) -> dict[str, Any]:
    if layer not in LAYERS or not TABLE.fullmatch(table):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such table.")
    return dict(_call(lambda: ingest.preview(lake_of(user), layer, table, version)))


def _table(name: str, given: str | None) -> str:
    return uploads.table_name(given or name, set())


@router.post("/files", status_code=status.HTTP_202_ACCEPTED)
async def load_file(
    user: UserDep,  # first, so an anonymous body is never read
    request: Request,
    ingest: IngestDep,
    lake_dir: LakeDir,
    engine: EngineDep,
    name: Annotated[str, Query(min_length=1, max_length=255)],
    table: Annotated[str | None, Query(max_length=63)] = None,
) -> dict[str, Any]:
    """The raw file is the request body; `name` is its file name."""
    suffix = Path(name).suffix.lower()
    if suffix not in SUFFIXES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Load a .csv, .xlsx or .parquet file.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > uploads.MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LARGE)
    _reserve(user["id"])
    inbox = lake_dir / "inbox"
    handed: list[tuple[str, str]] = []  # (inbox file, table)
    try:
        inbox.mkdir(parents=True, exist_ok=True)
        # On the inbox's own volume, so the hand-over below is a rename, not a copy across
        # filesystems (which os.replace cannot do). Ingest never accepts a dot-name.
        with tempfile.TemporaryDirectory(dir=inbox, prefix=".work-") as work:
            body = Path(work) / "body"
            size = 0
            with body.open("wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > uploads.MAX_BYTES:
                        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LARGE)
                    f.write(chunk)
            if suffix == ".xlsx":
                try:
                    sheets = await run_in_threadpool(uploads.excel_to_csv, body, name, Path(work))
                except uploads.UploadError as e:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
                one = len(sheets) == 1  # one sheet takes the file's name, several their own
                parts = [
                    (path, _table(name if one else sheet, table if one else None))
                    for sheet, path, _ in sheets
                ]
            else:
                parts = [(body, _table(name, table))]
            for src, target in parts:
                ext = ".parquet" if suffix == ".parquet" else ".csv"
                file = f"{secrets.token_urlsafe(16)}{ext}"
                src.replace(inbox / file)
                handed.append((file, target))
    except BaseException:
        for file, _ in handed:
            (inbox / file).unlink(missing_ok=True)
        _release(user["id"])
        raise

    lake = lake_of(user)

    def job() -> dict[str, Any]:
        made: dict[str, Any] = {"tables": {}, "snapshot": None}
        try:
            for file, target in handed:  # one load per sheet; the run ends at the last snapshot
                one = ingest.load_file(lake, file, target)
                made = {"tables": made["tables"] | one["tables"], "snapshot": one["snapshot"]}
        finally:
            for file, _ in handed:  # ingest deletes what it loaded; this covers a failure
                (inbox / file).unlink(missing_ok=True)
        return made

    try:
        return _start(engine, user["id"], "file", name, job)
    except BaseException:
        for file, _ in handed:
            (inbox / file).unlink(missing_ok=True)
        raise


@router.post("/quakes", status_code=status.HTTP_202_ACCEPTED)
def load_quakes(user: UserDep, ingest: IngestDep, engine: EngineDep) -> dict[str, Any]:
    _reserve(user["id"])
    lake = lake_of(user)
    return _start(
        engine, user["id"], "quakes", "USGS earthquakes", lambda: ingest.load_quakes(lake)
    )


class SheetBody(BaseModel):
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    table: Annotated[str, StringConstraints(min_length=1, max_length=63)]


@router.post("/sheets", status_code=status.HTTP_202_ACCEPTED)
def load_sheet(
    user: UserDep, ingest: IngestDep, engine: EngineDep, body: SheetBody
) -> dict[str, Any]:
    target = _table(body.table, None)
    _reserve(user["id"])
    lake = lake_of(user)
    return _start(
        engine, user["id"], "sheet", body.url, lambda: ingest.load_sheet(lake, body.url, target)
    )


Identifier = Annotated[str, StringConstraints(pattern=f"^{TABLE.pattern}$")]


class ModelBody(BaseModel):
    layer: Literal["silver", "gold"]
    name: Identifier
    sql: Annotated[str, StringConstraints(min_length=1, max_length=MAX_SQL)]
    # Data quality checks: ingest refuses the whole run if one fails.
    not_null: Annotated[list[Identifier], Field(max_length=MAX_CHECKS)] = []
    unique: Annotated[list[Identifier], Field(max_length=MAX_CHECKS)] = []


class ModelsBody(BaseModel):
    models: Annotated[list[ModelBody], Field(max_length=MAX_MODELS)]


@router.put("/models")
def save_models(user: UserDep, engine: EngineDep, body: ModelsBody) -> dict[str, Any]:
    models = [m.model_dump() for m in body.models]
    if len({(m["layer"], m["name"]) for m in models}) != len(models):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Two models build the same table.")
    with engine.begin() as conn:
        store.set_lake_models(conn, user["id"], models)
    return {"models": models}


@router.post("/transform", status_code=status.HTTP_202_ACCEPTED)
def transform(user: UserDep, ingest: IngestDep, engine: EngineDep) -> dict[str, Any]:
    with engine.connect() as conn:
        models = store.get_lake_models(conn, user["id"])
    if not models:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Write a silver or gold model first.")
    lake = lake_of(user)
    checked = _call(lambda: check_models(models, ingest.tables(lake)))
    _reserve(user["id"])
    label = f"{len(checked)} model{'s' if len(checked) != 1 else ''}"
    return _start(engine, user["id"], "transform", label, lambda: ingest.transform(lake, checked))


def published(lake_dir: Path, user: dict[str, Any]) -> Path:
    """The user's silver and gold as a DuckDB file, which Ask opens read-only."""
    return lake_dir / "publish" / f"{lake_of(user)}.duckdb"
