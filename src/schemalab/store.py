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
    JSON,
    Column,
    Connection,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    insert,
    select,
)
from sqlalchemy.exc import IntegrityError

SESSION_TTL = timedelta(days=7)
MAX_LOGIN_FAILURES = 5
LOGIN_WINDOW = timedelta(minutes=15)

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

history = Table(
    "history",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("question", Text, nullable=False),
    Column("outcome", JSON, nullable=False),  # the final event: an answer or an error
    Column("created_at", DateTime(timezone=True), nullable=False),
)

corrections = Table(
    "corrections",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("text", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

login_failures = Table(
    "login_failures",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(254), nullable=False, index=True),
    Column("at", DateTime(timezone=True), nullable=False),
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


def record_login_failure(conn: Connection, email: str, now: datetime | None = None) -> None:
    conn.execute(insert(login_failures).values(email=email, at=now or _now()))


def login_locked_for(conn: Connection, email: str) -> timedelta | None:
    """Time left on the lock if this email has too many recent failures, else None."""
    since = _now() - LOGIN_WINDOW
    times = (
        conn.execute(
            select(login_failures.c.at)
            .where(login_failures.c.email == email, login_failures.c.at > since)
            .order_by(login_failures.c.at)
        )
        .scalars()
        .all()
    )
    if len(times) < MAX_LOGIN_FAILURES:
        return None
    # Unlocks once the oldest failure that still counts leaves the window.
    return _aware(times[-MAX_LOGIN_FAILURES]) + LOGIN_WINDOW - _now()


def clear_login_failures(conn: Connection, email: str) -> None:
    # Also drops rows older than the window for everyone, so the table stays small.
    conn.execute(delete(login_failures).where(login_failures.c.email == email))
    conn.execute(delete(login_failures).where(login_failures.c.at <= _now() - LOGIN_WINDOW))


def create_session(conn: Connection, user_id: int, now: datetime | None = None) -> str:
    # Expired sessions are useless; drop the user's old ones whenever a new one starts.
    conn.execute(
        delete(sessions).where(sessions.c.user_id == user_id, sessions.c.expires_at <= _now())
    )
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


def add_history(conn: Connection, user_id: int, question: str, outcome: dict[str, Any]) -> None:
    conn.execute(
        insert(history).values(
            user_id=user_id, question=question, outcome=outcome, created_at=_now()
        )
    )


def list_history(conn: Connection, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(history.c.id, history.c.question, history.c.outcome, history.c.created_at)
        .where(history.c.user_id == user_id)
        .order_by(history.c.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "question": r.question,
            "outcome": r.outcome,
            "created_at": _aware(r.created_at).isoformat(),
        }
        for r in rows
    ]


def add_correction(conn: Connection, user_id: int, text: str) -> dict[str, Any]:
    row = conn.execute(
        insert(corrections)
        .values(user_id=user_id, text=text, created_at=_now())
        .returning(corrections.c.id)
    ).one()
    return {"id": row.id, "text": text}


def list_corrections(conn: Connection, user_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(corrections.c.id, corrections.c.text)
        .where(corrections.c.user_id == user_id)
        .order_by(corrections.c.id)
    ).all()
    return [{"id": r.id, "text": r.text} for r in rows]


def delete_correction(conn: Connection, user_id: int, correction_id: int) -> bool:
    """Delete one of the user's notes. False if it does not exist or belongs to someone else."""
    result = conn.execute(
        delete(corrections).where(
            corrections.c.id == correction_id, corrections.c.user_id == user_id
        )
    )
    return result.rowcount == 1


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    # SQLite returns naive datetimes; values are always written in UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
