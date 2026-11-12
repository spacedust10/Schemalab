"""Schedule checker and recovery simulator. Pure computation on small inputs."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, StringConstraints

from schemalab.api.auth import UserDep
from schemalab.recovery import RecoveryError, simulate
from schemalab.schedules import ScheduleError, analyze

router = APIRouter(tags=["theory"])


class ScheduleRequest(BaseModel):
    schedule: Annotated[str, StringConstraints(max_length=2000)]


class RecoveryRequest(BaseModel):
    log: Annotated[str, StringConstraints(max_length=5000)]


@router.post("/schedules")
def schedules(user: UserDep, body: ScheduleRequest) -> dict[str, Any]:
    try:
        return analyze(body.schedule)
    except ScheduleError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None


@router.post("/recovery")
def recovery(user: UserDep, body: RecoveryRequest) -> dict[str, Any]:
    try:
        return simulate(body.log)
    except RecoveryError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
