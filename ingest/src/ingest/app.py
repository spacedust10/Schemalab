"""The ingest service's HTTP API. Only the Schemalab API can reach it (internal network)."""

import hmac
import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ingest import lake as lk
from ingest import sources
from ingest.config import Settings

_FILE = re.compile(r"[A-Za-z0-9_-]{1,64}\.(csv|parquet)")
# ponytail: per-process, right for the one uvicorn worker the image starts; a Postgres advisory
# lock per lake if ingest ever runs several workers or replicas.
_LOCK = threading.Lock()
_BUSY: set[str] = set()


@contextmanager
def busy(name: str) -> Iterator[None]:
    """One run per lake at a time; a second one gets 409 rather than waiting."""
    with _LOCK:
        if name in _BUSY:
            raise HTTPException(status.HTTP_409_CONFLICT, "A run is already going on this lake.")
        _BUSY.add(name)
    try:
        yield
    finally:
        with _LOCK:
            _BUSY.discard(name)


class FileLoad(BaseModel):
    file: str
    table: str


class SheetLoad(BaseModel):
    url: str
    table: str


class Model(BaseModel):
    layer: str
    name: str
    sql: str


class Transform(BaseModel):
    models: list[Model]


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings.from_env()
    if len(s.token) < 32:
        raise ValueError("Set INGEST_TOKEN to a random value of at least 32 characters.")
    expected = f"Bearer {s.token}".encode()

    def caller(authorization: Annotated[str, Header()] = "") -> None:
        """Network placement is not enough: every call must carry the API's token."""
        if not hmac.compare_digest(authorization.encode(), expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not the API.")

    app = FastAPI(title="Schemalab ingest")
    lakes = APIRouter(prefix="/lakes/{name}", dependencies=[Depends(caller)])

    def lake_name(name: str) -> str:
        if not lk.NAME.fullmatch(name):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such lake.")
        return name

    Lake = Annotated[str, Depends(lake_name)]

    def made(name: str, tables: dict[str, int]) -> dict[str, Any]:
        """A run's answer: rows per table, and the snapshot it left the lake at."""
        return {"tables": tables, "snapshot": lk.newest_snapshot(s, name)}

    @app.exception_handler(lk.LakeError)
    @app.exception_handler(sources.SourceError)
    def refused(_: Request, e: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=status.HTTP_400_BAD_REQUEST)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @lakes.post("/load/file")
    def load_file(name: Lake, body: FileLoad) -> dict[str, Any]:
        if not _FILE.fullmatch(body.file):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No such file.")
        path = s.inbox_dir / body.file
        try:
            with busy(name):
                return made(name, lk.load_file(s, name, path, body.table))
        finally:
            path.unlink(missing_ok=True)

    @lakes.post("/load/sheet")
    def load_sheet(name: Lake, body: SheetLoad) -> dict[str, Any]:
        url = sources.sheet_export_url(body.url)
        with busy(name):
            csv = sources.download(url, sources.sheet_host)
            return made(name, lk.load_sheet(s, name, csv, body.table))

    @lakes.post("/load/quakes")
    def load_quakes(name: Lake) -> dict[str, Any]:
        def fetch(url: str) -> dict[str, Any]:
            feed: dict[str, Any] = json.loads(sources.download(url, sources.usgs_host))
            return feed

        with busy(name):
            return made(name, lk.load_quakes(s, name, fetch))

    @lakes.post("/transform")
    def transform(name: Lake, body: Transform) -> dict[str, Any]:
        with busy(name):
            return made(name, lk.transform(s, name, [m.model_dump() for m in body.models]))

    @lakes.get("/tables")
    def tables(name: Lake) -> list[dict[str, Any]]:
        return lk.tables(s, name)

    @lakes.get("/tables/{layer}/{table}")
    def preview(name: Lake, layer: str, table: str, version: int | None = None) -> dict[str, Any]:
        return lk.preview(s, name, layer, table, version)

    @lakes.get("/snapshots")
    def snapshots(name: Lake) -> list[dict[str, Any]]:
        return lk.snapshots(s, name)

    app.include_router(lakes)
    return app
