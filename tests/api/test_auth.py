"""Accounts: register, login, logout, and what is stored."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
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


def login(client: TestClient, password: str, email: str = "ada@umich.edu") -> int:
    return client.post("/auth/login", json={"email": email, "password": password}).status_code


def from_ip(app: FastAPI, ip: str) -> TestClient:
    return TestClient(app, client=(ip, 50000))


def test_five_failures_lock_that_email_from_that_address(client: TestClient) -> None:
    register(client)
    assert [login(client, "wrong password!") for _ in range(5)] == [401] * 5
    resp = client.post("/auth/login", json={"email": "ada@umich.edu", "password": PASSWORD})
    assert resp.status_code == 429  # even the right password, so guessing cannot continue
    assert int(resp.headers["Retry-After"]) > 0


def test_an_attacker_cannot_lock_the_owner_out(app: FastAPI, client: TestClient) -> None:
    register(client)
    attacker = from_ip(app, "203.0.113.9")
    assert [login(attacker, "wrong password!") for _ in range(6)][-1] == 429
    assert login(from_ip(app, "198.51.100.4"), PASSWORD) == 200


def test_lock_is_per_email(client: TestClient) -> None:
    register(client, "ada@umich.edu")
    register(client, "grace@umich.edu")
    for _ in range(5):
        login(client, "wrong password!", "ada@umich.edu")
    assert login(client, PASSWORD, "grace@umich.edu") == 200


def test_old_failures_do_not_count(client: TestClient, engine: Engine) -> None:
    register(client)
    old = datetime.now(UTC) - timedelta(minutes=16)
    with engine.begin() as conn:
        for _ in range(5):
            store.record_login_attempt(conn, "ada@umich.edu", "testclient", now=old)
    assert login(client, PASSWORD) == 200


def test_every_attempt_prunes_expired_rows(client: TestClient, engine: Engine) -> None:
    old = datetime.now(UTC) - timedelta(minutes=16)
    with engine.begin() as conn:
        for i in range(50):
            store.record_login_attempt(conn, f"spam{i}@x.io", "203.0.113.9", now=old)
    login(client, "wrong password!", "someone@umich.edu")  # a failure, not a success
    with engine.connect() as conn:
        assert conn.execute(select(store.login_attempts)).all().__len__() == 1


def test_success_clears_earlier_failures(client: TestClient) -> None:
    register(client)
    for _ in range(4):
        login(client, "wrong password!")
    assert login(client, PASSWORD) == 200
    assert [login(client, "wrong password!") for _ in range(4)] == [401] * 4
    assert login(client, PASSWORD) == 200


def test_expired_sessions_are_deleted_on_next_sign_in(client: TestClient, engine: Engine) -> None:
    register(client)
    with engine.begin() as conn:
        user_id = store.authenticate(conn, "ada@umich.edu", PASSWORD)
        assert user_id is not None
        store.create_session(conn, user_id, now=datetime.now(UTC) - timedelta(days=8))
    assert login(client, PASSWORD) == 200
    with engine.connect() as conn:
        expiries = conn.execute(select(store.sessions.c.expires_at)).scalars().all()
    assert all(store._aware(e) > datetime.now(UTC) for e in expiries)
    assert len(expiries) == 2  # the one from register and the new one


def test_rotating_addresses_hits_a_per_email_ceiling(app: FastAPI, client: TestClient) -> None:
    register(client)
    per_address = store.MAX_LOGIN_FAILURES
    for i in range(store.MAX_LOGIN_FAILURES_PER_EMAIL // per_address):
        attacker = from_ip(app, f"10.0.0.{i + 1}")
        for _ in range(per_address):
            login(attacker, "wrong password!")
    assert login(from_ip(app, "192.0.2.77"), "wrong password!") == 429


def test_ipv6_addresses_in_one_64_count_together(app: FastAPI, client: TestClient) -> None:
    register(client)
    for i in range(5):
        login(from_ip(app, f"2001:db8:1:2::{i + 1:x}"), "wrong password!")
    assert login(from_ip(app, "2001:db8:1:2:ffff::9"), "wrong password!") == 429
    assert login(from_ip(app, "2001:db8:1:3::1"), PASSWORD) == 200  # a different /64


def test_address_key_normalizes_ipv6_to_its_64() -> None:
    assert store.address_key("2001:db8:1:2:abcd::1") == "2001:db8:1:2::/64"
    assert store.address_key("203.0.113.9") == "203.0.113.9"
    assert store.address_key("testclient") == "testclient"


def stored_attempts(engine: Engine) -> int:
    with engine.connect() as conn:
        return len(conn.execute(select(store.login_attempts)).all())


def test_locked_attempts_are_not_counted(client: TestClient, engine: Engine) -> None:
    register(client)
    for _ in range(5):
        login(client, "wrong password!")
    assert [login(client, "wrong password!") for _ in range(10)] == [429] * 10
    assert stored_attempts(engine) == 5  # hammering a lock does not extend it


def test_success_clears_failures_from_every_address(
    app: FastAPI, client: TestClient, engine: Engine
) -> None:
    register(client)
    for _ in range(4):
        login(from_ip(app, "203.0.113.9"), "wrong password!")
    assert login(from_ip(app, "198.51.100.4"), PASSWORD) == 200
    assert stored_attempts(engine) == 0
