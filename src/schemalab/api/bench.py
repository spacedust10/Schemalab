"""The DuckDB vs PostgreSQL benchmark: one run at a time on the server, results shared."""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from schemalab import bench
from schemalab.api.auth import UserDep
from schemalab.api.lab import LabDsn
from schemalab.config import get_settings

router = APIRouter(prefix="/bench", tags=["bench"])
logger = logging.getLogger(__name__)
OPEN_SCALE = 0.01  # seconds of work; anything larger needs an operator


def get_bench_dir() -> Path:
    return Path(get_settings().bench_dir)


BenchDir = Annotated[Path, Depends(get_bench_dir)]


def get_bench_operators() -> list[str]:
    return [e.lower() for e in get_settings().bench_operators]


Operators = Annotated[list[str], Depends(get_bench_operators)]

_job: dict[str, Any] = {
    "running": False,
    "benchmark": None,
    "scale": None,
    "message": None,
    "error": None,
}
_job_lock = threading.Lock()
_thread: threading.Thread | None = None


class RunRequest(BaseModel):
    benchmark: Literal["tpch", "tpcds"]
    scale: float


def _results_path(root: Path, name: str, sf: float) -> Path:
    return root / "results" / f"{name}_sf{bench.schema_name(name, sf).rsplit('sf', 1)[1]}.json"


def _work(root: Path, dsn: str, name: str, sf: float) -> None:
    def say(message: str) -> None:
        _job["message"] = message

    try:
        bench.prepare(root, dsn, name, sf, say)
        result = bench.run(root, dsn, name, sf, progress=say)
        path = _results_path(root, name, sf)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        partial.write_text(json.dumps(result))
        partial.replace(path)
        _job["error"] = None
    except Exception:
        # The text can carry the DSN or server internals; it goes to the log, not to users.
        logger.exception("benchmark %s at scale %s failed", name, sf)
        _job["error"] = "The benchmark failed; the server log has the details."
    finally:
        _job["running"] = False


def wait_for_job(timeout_s: float) -> None:
    """For tests: let a background run finish before the next test starts."""
    end = time.monotonic() + timeout_s
    while _job["running"] and time.monotonic() < end:
        time.sleep(0.1)


@router.get("")
def overview(user: UserDep, root: BenchDir) -> dict[str, Any]:
    results = []
    for p in sorted((root / "results").glob("*.json")):
        try:
            results.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            logger.warning("skipping unreadable benchmark result %s", p)
    return {
        "benchmarks": [
            {"id": k, "name": b.name, "scales": list(b.scales)} for k, b in bench.BENCHMARKS.items()
        ],
        "job": dict(_job),
        "results": results,
    }


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
def start(
    user: UserDep, body: RunRequest, root: BenchDir, dsn: LabDsn, operators: Operators
) -> dict[str, Any]:
    global _thread
    if not dsn:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The benchmark database is not set up on this server.",
        )
    if body.scale not in bench.BENCHMARKS[body.benchmark].scales:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Choose a scale from {list(bench.BENCHMARKS[body.benchmark].scales)}.",
        )
    if body.scale != OPEN_SCALE and user["email"].lower() not in operators:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Scale {OPEN_SCALE:g} is open to everyone; larger scales need an operator account.",
        )
    with _job_lock:
        if _job["running"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A benchmark is already running; wait for it to finish."
            )
        _job.update(
            running=True,
            benchmark=body.benchmark,
            scale=body.scale,
            message="Starting…",
            error=None,
        )
        _thread = threading.Thread(
            target=_work, args=(root, dsn, body.benchmark, body.scale), daemon=True
        )
        _thread.start()
    return dict(_job)
