"""ER designer: design text or SQL -> diagram and DDL, English -> design, uploads -> design."""

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
    # The design text, or CREATE TABLE statements, or a whole .sql dump loaded from a file.
    design: Annotated[str, StringConstraints(max_length=1_000_000)]
    dialect: Dialect = "postgres"


class EnglishRequest(BaseModel):
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]


def _model_json(model: er.Model) -> dict[str, Any]:
    return {
        "entities": [asdict(e) for e in model.entities],
        "relationships": [{**asdict(r), "kind": r.kind} for r in model.relationships],
        "specializations": [asdict(sp) for sp in model.specializations],
    }


def _design(model: er.Model) -> dict[str, Any]:
    return {"design": er.to_text(model), "model": _model_json(model)}


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/ddl")
def design_to_ddl(user: UserDep, body: DesignRequest) -> dict[str, Any]:
    try:
        model, source = er.read_any(body.design)
    except er.ERError as e:
        raise _bad(e) from None
    return {**_design(model), "ddl": er.to_ddl(model, body.dialect), "source": source}


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
    """CREATE TABLE statements from uploaded tables and the keys stored with them (or, for
    older uploads, their column notes). Names are [a-z0-9_] identifiers made on upload; they
    are quoted in case one is a reserved word."""

    def q(names: list[str]) -> str:
        return ", ".join(f'"{n}"' for n in names)

    statements = []
    for t in tables:
        keys = t.get("keys")
        required = set(keys.get("not_null", [])) if keys else set()
        lines = [
            f'"{c["name"]}" {c["type"]}' + (" NOT NULL" if c["name"] in required else "")
            for c in t["columns"]
        ]
        if keys:
            if keys["primary"]:
                lines.append(f"PRIMARY KEY ({q(keys['primary'])})")
            lines += [f"UNIQUE ({q(u)})" for u in keys["unique"] if u]
            for cols, target, refs in keys["foreign"]:
                if cols:
                    to = f" ({q(refs)})" if refs else ""
                    lines.append(f'FOREIGN KEY ({q(cols)}) REFERENCES "{target}"{to}')
            for col, values in keys["checks"].items():
                quoted = ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)
                lines.append(f'CHECK ("{col}" IN ({quoted}))')
        else:
            parts = {c["name"]: (c["note"] or "").split("; ") for c in t["columns"]}
            key = [n for n, p in parts.items() if any(x.endswith("primary key") for x in p)]
            if key:
                lines.append(f"PRIMARY KEY ({q(key)})")
            for n, p in parts.items():
                for ref in filter(None, (_REFERENCE.fullmatch(x) for x in p)):
                    target = f'"{ref[1]}"' + (f' ("{ref[2]}")' if ref[2] else "")
                    lines.append(f'FOREIGN KEY ("{n}") REFERENCES {target}')
        statements.append(f'CREATE TABLE "{t["name"]}" ({", ".join(lines)})')
    return ";\n".join(statements) + ";"
