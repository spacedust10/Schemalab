"""A user's own tables: upload a .csv or .xlsx file, list the tables with profiles, delete one."""

import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from schemalab import uploads
from schemalab.api.auth import UserDep
from schemalab.config import get_settings

router = APIRouter(prefix="/datasets", tags=["datasets"])

TOO_LARGE = f"Files can be at most {uploads.MAX_BYTES // (1024 * 1024)} MB."


def get_uploads_dir() -> Path:
    return Path(get_settings().uploads_dir)


UploadsDir = Annotated[Path, Depends(get_uploads_dir)]


def user_db(root: Path, user: dict[str, Any]) -> Path:
    # The id is an integer from the session lookup, never user input.
    return root / f"{int(user['id'])}.duckdb"


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(
    user: UserDep,  # first, so an anonymous body is never read
    request: Request,
    root: UploadsDir,
    name: Annotated[str, Query(min_length=1, max_length=255)],
) -> dict[str, Any]:
    """The raw file is the request body; `name` is its file name."""
    limit = uploads.MAX_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LARGE)
    with tempfile.NamedTemporaryFile() as tmp:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:  # a chunked body has no Content-Length to check up front
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LARGE)
            tmp.write(chunk)
        tmp.flush()
        db = user_db(root, user)
        try:
            created = await run_in_threadpool(uploads.import_file, db, Path(tmp.name), name)
        except uploads.UploadError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    return {"created": created, "tables": await run_in_threadpool(uploads.list_tables, db)}


@router.get("")
def list_datasets(user: UserDep, root: UploadsDir) -> list[dict[str, Any]]:
    return uploads.list_tables(user_db(root, user))


@router.delete("/{table}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(user: UserDep, root: UploadsDir, table: str) -> Response:
    if not uploads.delete_table(user_db(root, user), table):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such table.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
