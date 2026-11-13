"""POST /normalize: closure, keys, normal form and decompositions for one relation."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints

from schemalab import normalize as nf
from schemalab.api.auth import UserDep

router = APIRouter(tags=["normalize"])


class NormalizeRequest(BaseModel):
    # "schema" would shadow a BaseModel attribute, so the field takes it as an alias.
    schema_: Annotated[str, StringConstraints(max_length=2000), Field(alias="schema")]
    dependencies: Annotated[str, StringConstraints(max_length=10_000)] = ""
    closure_of: Annotated[str, StringConstraints(max_length=500)] = ""


@router.post("/normalize")
def analyze(user: UserDep, body: NormalizeRequest) -> dict[str, Any]:
    try:
        attrs, fds = nf.parse(body.schema_, body.dependencies)
        name = body.schema_.split("(", 1)[0].strip()
        wanted = nf.read_attrs(body.closure_of, attrs, name) if body.closure_of.strip() else None
    except nf.NormalizeError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None

    def names(s: frozenset[str]) -> list[str]:
        return [a for a in attrs if a in s]

    def dep(fd: nf.FD) -> dict[str, list[str]]:
        return {"lhs": names(fd.lhs), "rhs": names(fd.rhs)}

    def decomposition(d: nf.Decomposition) -> dict[str, Any]:
        return {
            "relations": [
                {
                    "attrs": r.attrs,
                    "fds": [dep(f) for f in r.fds],
                    "keys": [names(k) for k in r.keys],
                    "form": nf.normal_form(r.attrs, r.fds).form,
                }
                for r in d.relations
            ],
            "steps": [
                {"relation": s.relation, **dep(s.fd), "split": list(s.split)} for s in d.steps
            ],
            "lossless": d.lossless,
            "chase_table": d.chase_table,
            "kept": [dep(f) for f in d.kept],
            "lost": [dep(f) for f in d.lost],
        }

    try:
        with nf.budget():  # one limit for the whole request
            cover = nf.minimal_cover(fds)
            keys = nf.candidate_keys(attrs, cover)
            report = nf.normal_form(attrs, fds)
            bcnf = nf.bcnf_decompose(attrs, fds)
            third = nf.synthesize_3nf(attrs, fds)
    except nf.NormalizeError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    result: dict[str, Any] = {
        "attributes": attrs,
        "dependencies": [dep(f) for f in fds],
        "cover": [dep(f) for f in _merged(cover, attrs)],
        "keys": [names(k) for k in keys],
        "prime": names(frozenset().union(*keys)),
        "form": report.form,
        "violations": [
            {"form": v.form, **dep(v.fd), "reason": v.reason} for v in report.violations
        ],
        "bcnf": decomposition(bcnf),
        "third": decomposition(third),
    }
    if wanted is not None:
        plus = nf.closure(wanted, fds)
        result["closure"] = {
            "of": names(wanted),
            "is": names(plus),
            "superkey": plus == frozenset(attrs),
        }
    return result


def _merged(fds: list[nf.FD], attrs: list[str]) -> list[nf.FD]:
    """Equal left sides merged and sorted by schema order, as textbooks print a cover."""
    pos = {a: i for i, a in enumerate(attrs)}
    groups: dict[frozenset[str], set[str]] = {}
    for fd in fds:
        groups.setdefault(fd.lhs, set()).update(fd.rhs)
    merged = [nf.FD(lhs, frozenset(rhs)) for lhs, rhs in groups.items()]
    return sorted(merged, key=lambda f: (len(f.lhs), sorted(pos[a] for a in f.lhs)))
