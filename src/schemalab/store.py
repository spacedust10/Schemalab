"""App database: accounts, sessions, question history and correction notes.

Postgres in Compose, SQLite in tests. Passwords are Argon2id hashes; session tokens are random
and only their SHA-256 hash is stored, so a database leak gives no working tokens.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import (
    Column,
    Connection,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    insert,
    select,
)
from sqlalchemy.exc import IntegrityError

SESSION_TTL = timedelta(days=7)

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(254), nullable=False, unique=True),
    Column("password_hash", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

sessions = Table(
    "sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

_hasher = PasswordHasher()
# Verified against when the email is unknown, so both failures take the same time.
_DUMMY_HASH = _hasher.hash("not a real password")


class EmailTakenError(ValueError):
    """An account with this email already exists."""


def make_engine(url: str) -> Engine:
    engine = create_engine(url)
    # ponytail: create_all, no migrations. Add Alembic once a table needs to change shape.
    metadata.create_all(engine)
    return engine


def create_user(conn: Connection, email: str, password: str) -> int:
    try:
        row = conn.execute(
            insert(users)
            .values(email=email, password_hash=_hasher.hash(password), created_at=_now())
            .returning(users.c.id)
        ).one()
    except IntegrityError as e:
        raise EmailTakenError(email) from e
    return int(row.id)


def authenticate(conn: Connection, email: str, password: str) -> int | None:
    row = conn.execute(
        select(users.c.id, users.c.password_hash).where(users.c.email == email)
    ).one_or_none()
    try:
        _hasher.verify(row.password_hash if row else _DUMMY_HASH, password)
    except VerificationError:
        return None
    return int(row.id) if row else None


def create_session(conn: Connection, user_id: int, now: datetime | None = None) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        insert(sessions).values(
            token_hash=_hash_token(token),
            user_id=user_id,
            expires_at=(now or _now()) + SESSION_TTL,
        )
    )
    return token


def user_for_token(conn: Connection, token: str) -> dict[str, Any] | None:
    row = conn.execute(
        select(users.c.id, users.c.email, sessions.c.expires_at)
        .join(sessions, sessions.c.user_id == users.c.id)
        .where(sessions.c.token_hash == _hash_token(token))
    ).one_or_none()
    if row is None or _aware(row.expires_at) <= _now():
        return None
    return {"id": row.id, "email": row.email}


def revoke_session(conn: Connection, token: str) -> None:
    conn.execute(delete(sessions).where(sessions.c.token_hash == _hash_token(token)))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    # SQLite returns naive datetimes; values are always written in UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
