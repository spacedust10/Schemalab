"""Correction notes: facts a user teaches the analyst, used in their own prompts."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, StringConstraints

from schemalab import store
from schemalab.api.auth import EngineDep, UserDep

router = APIRouter()


class CorrectionRequest(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


@router.post("/corrections", status_code=status.HTTP_201_CREATED)
def add(body: CorrectionRequest, user: UserDep, engine: EngineDep) -> dict[str, Any]:
    with engine.begin() as conn:
        return store.add_correction(conn, user["id"], body.text)


@router.get("/corrections")
def list_all(user: UserDep, engine: EngineDep) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return store.list_corrections(conn, user["id"])


@router.delete("/corrections/{correction_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(correction_id: int, user: UserDep, engine: EngineDep) -> None:
    with engine.begin() as conn:
        # Someone else's note gets the same 404 as a missing one, so ids reveal nothing.
        if not store.delete_correction(conn, user["id"], correction_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such note.")
