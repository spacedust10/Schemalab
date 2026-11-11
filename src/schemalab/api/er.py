"""ER designer: design text -> DDL, DDL -> design, English -> design, uploads -> design."""

import re
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, StringConstraints
from starlette.concurrency import run_in_threadpool

from schemalab import er, uploads
from schemalab.agent.design import design_from_english
from schemalab.api.auth import UserDep
from schemalab.api.datasets import UploadsDir, user_db
from schemalab.api.gateway import GatewayDep
from schemalab.llm import AllModelsFailedError

router = APIRouter(prefix="/er", tags=["er"])

Dialect = Literal["postgres", "mysql", "sqlite", "tsql", "oracle"]


class DesignRequest(BaseModel):
    design: Annotated[str, StringConstraints(max_length=20_000)]
    dialect: Dialect = "postgres"


class DdlRequest(BaseModel):
    ddl: Annotated[str, StringConstraints(max_length=200_000)]


class EnglishRequest(BaseModel):
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]


def _model_json(model: er.Model) -> dict[str, Any]:
    return {
        "entities": [asdict(e) for e in model.entities],
        "relationships": [{**asdict(r), "kind": r.kind} for r in model.relationships],
    }


def _design(model: er.Model) -> dict[str, Any]:
    return {"design": er.to_text(model), "model": _model_json(model)}


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/ddl")
def design_to_ddl(user: UserDep, body: DesignRequest) -> dict[str, Any]:
    try:
        model = er.parse(body.design)
    except er.ERError as e:
        raise _bad(e) from None
    return {**_design(model), "ddl": er.to_ddl(model, body.dialect)}


@router.post("/from-ddl")
def ddl_to_design(user: UserDep, body: DdlRequest) -> dict[str, Any]:
    try:
        return _design(er.from_ddl(body.ddl))
    except er.ERError as e:
        raise _bad(e) from None


@router.post("/from-english")
def english_to_design(user: UserDep, body: EnglishRequest, gateway: GatewayDep) -> dict[str, Any]:
    try:
        _, model = design_from_english(body.description, gateway)
    except er.ERError as e:
        raise _bad(e) from None
    except AllModelsFailedError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No language model answered. Try again."
        ) from None
    return _design(model)


@router.get("/mine")
async def design_from_uploads(user: UserDep, root: UploadsDir) -> dict[str, Any]:
    tables = await run_in_threadpool(uploads.list_tables, user_db(root, user))
    if not tables:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a .sql dump or a file first.")
    try:
        return _design(er.from_ddl(_ddl_from_profiles(tables)))
    except er.ERError as e:
        raise _bad(e) from None


_REFERENCE = re.compile(r"references (\w+)(?:\((\w+)\))?")


def _ddl_from_profiles(tables: list[dict[str, Any]]) -> str:
    """CREATE TABLE statements from uploaded tables and their key notes. Names are [a-z0-9_]
    identifiers made on upload; they are quoted in case one is a reserved word."""
    statements = []
    for t in tables:
        lines = [f'"{c["name"]}" {c["type"]}' for c in t["columns"]]
        key = [c["name"] for c in t["columns"] if (c["note"] or "").endswith("primary key")]
        if key:
            lines.append("PRIMARY KEY (" + ", ".join(f'"{k}"' for k in key) + ")")
        for c in t["columns"]:
            ref = _REFERENCE.fullmatch(c["note"] or "")
            if ref:
                target = f'"{ref[1]}"' + (f' ("{ref[2]}")' if ref[2] else "")
                lines.append(f'FOREIGN KEY ("{c["name"]}") REFERENCES {target}')
        statements.append(f'CREATE TABLE "{t["name"]}" ({", ".join(lines)})')
    return ";\n".join(statements) + ";"
