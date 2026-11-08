"""Accounts: register, login, logout, and what is stored."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from schemalab import store
from tests.conftest import register

PASSWORD = "correct horse battery"


def test_register_then_me(client: TestClient) -> None:
    headers = register(client, "Ada@UMich.edu")
    me = client.get("/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "ada@umich.edu"


def test_email_is_unique_ignoring_case(client: TestClient) -> None:
    register(client, "ada@umich.edu")
    resp = client.post("/auth/register", json={"email": "ADA@umich.edu", "password": PASSWORD})
    assert resp.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"email": "ada@umich.edu", "password": "short"},
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "a" * 250 + "@x.io", "password": PASSWORD},
        {"email": "ada@umich.edu", "password": "x" * 1025},
    ],
)
def test_register_validates_input(client: TestClient, body: dict[str, str]) -> None:
    assert client.post("/auth/register", json=body).status_code == 422


def test_login_returns_a_working_token(client: TestClient) -> None:
    register(client)
    resp = client.post("/auth/login", json={"email": "ada@umich.edu", "password": PASSWORD})
    assert resp.status_code == 200
    token = resp.json()["token"]
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


@pytest.mark.parametrize(
    ("email", "password"),
    [("ada@umich.edu", "wrong password here"), ("nobody@umich.edu", PASSWORD)],
)
def test_bad_login_gives_one_generic_error(client: TestClient, email: str, password: str) -> None:
    register(client)
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password."


@pytest.mark.parametrize("header", [None, "Bearer nonsense", "Basic abc", "Bearer "])
def test_me_needs_a_valid_token(client: TestClient, header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    assert client.get("/me", headers=headers).status_code == 401


def test_logout_revokes_the_token(client: TestClient) -> None:
    headers = register(client)
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/me", headers=headers).status_code == 401


def test_expired_session_is_rejected(client: TestClient, engine: Engine) -> None:
    register(client)
    with engine.begin() as conn:
        user_id = store.authenticate(conn, "ada@umich.edu", PASSWORD)
        assert user_id is not None
        old = datetime.now(UTC) - timedelta(days=8)
        token = store.create_session(conn, user_id, now=old)
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_password_and_token_are_not_stored_in_plain_text(
    client: TestClient, engine: Engine
) -> None:
    headers = register(client)
    token = headers["Authorization"].removeprefix("Bearer ")
    with engine.connect() as conn:
        user = conn.execute(select(store.users)).one()
        session = conn.execute(select(store.sessions)).one()
    assert PASSWORD not in user.password_hash and user.password_hash.startswith("$argon2id$")
    assert token not in session.token_hash and len(session.token_hash) == 64
