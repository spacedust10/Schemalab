"""Accounts: register, login, logout, and the current-user dependency."""

import re
import threading
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import AfterValidator, BaseModel, StringConstraints
from sqlalchemy import Engine

from schemalab import store
from schemalab.config import get_settings

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL.fullmatch(value):
        raise ValueError("not a valid email address")
    return value


Email = Annotated[str, AfterValidator(_email)]
# Long passphrases are welcome; the upper bound stops hashing megabytes per request.
Password = Annotated[str, StringConstraints(min_length=12, max_length=1024)]


class Credentials(BaseModel):
    email: Email
    password: Password


class LoginRequest(BaseModel):
    email: Email
    password: Annotated[str, StringConstraints(max_length=1024)]


class TokenResponse(BaseModel):
    token: str


_engines: dict[str, Engine] = {}
_engines_lock = threading.Lock()


def _engine_for(url: str) -> Engine:
    # A lock, not lru_cache: concurrent first requests each ran create_all, and Postgres
    # refused the duplicate CREATE TABLE.
    with _engines_lock:
        if url not in _engines:
            _engines[url] = store.make_engine(url)
        return _engines[url]


def get_engine() -> Engine:
    return _engine_for(get_settings().database_url)


def _bearer(authorization: Annotated[str | None, Header()] = None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    return token.strip()


def current_user(
    token: Annotated[str, Depends(_bearer)], engine: Annotated[Engine, Depends(get_engine)]
) -> dict[str, Any]:
    with engine.connect() as conn:
        user = store.user_for_token(conn, token)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    return user


router = APIRouter()
EngineDep = Annotated[Engine, Depends(get_engine)]
UserDep = Annotated[dict[str, Any], Depends(current_user)]


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(body: Credentials, engine: EngineDep) -> TokenResponse:
    with engine.begin() as conn:
        try:
            user_id = store.create_user(conn, body.email, body.password)
        except store.EmailTakenError:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "That email is already registered."
            ) from None
        return TokenResponse(token=store.create_session(conn, user_id))


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, engine: EngineDep) -> TokenResponse:
    # Keyed on email and client address: a guesser locks only their own address, and the
    # owner can still sign in from theirs. ponytail: request.client is the direct peer;
    # behind a reverse proxy, trust its forwarded-for header here instead.
    ip = request.client.host if request.client else "unknown"
    with engine.begin() as conn:  # committed before the check, so parallel tries all count
        attempt_id = store.record_login_attempt(conn, body.email, ip)
    with engine.begin() as conn:
        locked = store.login_locked_for(conn, body.email, ip)
        if locked is not None:
            store.forget_login_attempt(conn, attempt_id)
    if locked is not None:  # raised after the commit above, so the forget is kept
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed sign-ins. Try again later.",
            headers={"Retry-After": str(max(1, int(locked.total_seconds())))},
        )
    with engine.begin() as conn:
        user_id = store.authenticate(conn, body.email, body.password)
        if user_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
        store.clear_login_attempts(conn, body.email)
        return TokenResponse(token=store.create_session(conn, user_id))


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(token: Annotated[str, Depends(_bearer)], engine: EngineDep) -> None:
    with engine.begin() as conn:
        store.revoke_session(conn, token)


@router.get("/me")
def me(user: UserDep) -> dict[str, Any]:
    return {"email": user["email"]}
