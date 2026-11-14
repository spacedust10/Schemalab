"""The transaction lab over HTTP. Most tests need LAB_TEST_DSN."""

import os
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemalab.api import lab as lab_api
from tests.conftest import register

DSN = os.environ.get("LAB_TEST_DSN", "")
needs_postgres = pytest.mark.skipif(not DSN, reason="set LAB_TEST_DSN")


@pytest.fixture
def lab_client(app: FastAPI, client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[lab_api.get_lab_dsn] = lambda: DSN
    yield client
    lab_api.close_all()


def test_lab_needs_sign_in(client: TestClient) -> None:
    assert client.post("/lab/start").status_code == 401


def test_lab_without_a_database_says_so(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[lab_api.get_lab_dsn] = lambda: ""
    resp = client.post("/lab/start", headers=register(client))
    assert resp.status_code == 503 and "not set up" in resp.json()["detail"]


def test_running_before_starting_says_what_to_do(lab_client: TestClient) -> None:
    resp = lab_client.post(
        "/lab/run", json={"session": "A", "sql": "SELECT 1"}, headers=register(lab_client)
    )
    assert resp.status_code == 409 and "Start the lab" in resp.json()["detail"]


def run(client: TestClient, headers: dict[str, str], session: str, sql: str) -> Any:
    return client.post("/lab/run", json={"session": session, "sql": sql}, headers=headers)


@needs_postgres
def test_two_sessions_wait_and_finish(lab_client: TestClient) -> None:
    headers = register(lab_client)
    assert lab_client.post("/lab/start", headers=headers).status_code == 200
    run(lab_client, headers, "A", "CREATE TABLE t (id int PRIMARY KEY, n int)")
    run(lab_client, headers, "A", "INSERT INTO t VALUES (1, 0)")
    run(lab_client, headers, "A", "BEGIN")
    run(lab_client, headers, "A", "UPDATE t SET n = 1 WHERE id = 1")
    assert (
        run(lab_client, headers, "B", "UPDATE t SET n = 2 WHERE id = 1").json()["state"]
        == "waiting"
    )
    assert run(lab_client, headers, "B", "SELECT 1").status_code == 409
    state = lab_client.get("/lab/state", headers=headers).json()
    assert state["sessions"]["B"]["blocked_by"] == ["A"]
    run(lab_client, headers, "A", "COMMIT")
    for _ in range(50):
        got = lab_client.get("/lab/result", params={"session": "B"}, headers=headers).json()
        if got["state"] not in ("waiting", "running"):
            break
        time.sleep(0.05)
    assert got["status"] == "UPDATE 1"


@needs_postgres
def test_refused_statements_are_400(lab_client: TestClient) -> None:
    headers = register(lab_client)
    lab_client.post("/lab/start", headers=headers)
    resp = run(lab_client, headers, "A", "SET statement_timeout = 0")
    assert resp.status_code == 400 and "SET TRANSACTION" in resp.json()["detail"]


@needs_postgres
def test_loading_a_scenario_sets_up_its_tables_and_returns_its_steps(
    lab_client: TestClient,
) -> None:
    headers = register(lab_client)
    listed = lab_client.get("/lab/scenarios", headers=headers).json()
    assert {s["id"] for s in listed} >= {"lost-update", "deadlock", "write-skew"}
    got = lab_client.post("/lab/scenario", json={"id": "lost-update"}, headers=headers).json()
    assert got["title"] == "Lost update" and got["steps"][5]["expect"] == {"state": "waiting"}
    rows = run(lab_client, headers, "B", "SELECT count(*) FROM accounts").json()["rows"]
    assert rows == [[2]]


@needs_postgres
def test_each_users_lab_is_separate(lab_client: TestClient) -> None:
    ada, bob = register(lab_client, "ada@umich.edu"), register(lab_client, "bob@umich.edu")
    lab_client.post("/lab/start", headers=ada)
    lab_client.post("/lab/start", headers=bob)
    run(lab_client, ada, "A", "CREATE TABLE mine (x int)")
    out = run(lab_client, bob, "A", "SELECT * FROM mine").json()
    assert out["state"] == "error" and "does not exist" in out["error"]


@needs_postgres
def test_explain_over_http(lab_client: TestClient) -> None:
    headers = register(lab_client)
    lab_client.post("/lab/start", headers=headers)
    run(
        lab_client,
        headers,
        "A",
        "CREATE TABLE t AS SELECT i AS id, i % 300 AS g FROM generate_series(1, 30000) i",
    )
    run(lab_client, headers, "A", "ANALYZE t")
    got = lab_client.post(
        "/lab/explain", json={"sql": "SELECT id FROM t WHERE g = 7"}, headers=headers
    ).json()
    assert got["summary"]["tree"]["label"]
    assert got["advice"][0]["sql"] == "CREATE INDEX t_g_idx ON t (g);"
    refused = lab_client.post("/lab/explain", json={"sql": "DELETE FROM t"}, headers=headers)
    assert refused.status_code == 400 and "Explain a SELECT" in refused.json()["detail"]


def test_patterns_are_listed_with_their_ways(lab_client: TestClient) -> None:
    got = lab_client.get("/lab/patterns", headers=register(lab_client)).json()
    assert [p["id"] for p in got] == [
        "transfer",
        "idempotency",
        "optimistic",
        "outbox",
        "new_order",
    ]
    transfer = got[0]
    assert [m["id"] for m in transfer["modes"]] == ["naive", "for_update", "atomic"]
    assert all(m["label"] and m["how"] for m in transfer["modes"]) and transfer["question"]


def test_an_unknown_pattern_is_a_400(lab_client: TestClient) -> None:
    headers = register(lab_client)
    resp = lab_client.post("/lab/pattern", json={"id": "transfer", "mode": "yolo"}, headers=headers)
    assert resp.status_code in (400, 409)


@needs_postgres
def test_a_pattern_runs_in_the_users_lab(lab_client: TestClient) -> None:
    headers = register(lab_client)
    assert lab_client.post("/lab/start", headers=headers).status_code in (200, 204)
    got = lab_client.post(
        "/lab/pattern", json={"id": "idempotency", "mode": "key"}, headers=headers
    ).json()
    assert got["name"] == "idempotency" and all(c["ok"] for c in got["checks"])
    assert got["summary"].startswith("Every check passed")


@needs_postgres
def test_experiments_are_refused_when_the_server_is_busy(
    lab_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    headers = register(lab_client)
    lab_client.post("/lab/start", headers=headers)
    monkeypatch.setattr(lab_api, "_pattern_slots", threading.BoundedSemaphore(1))
    lab_api._pattern_slots.acquire()  # every slot taken by other users
    resp = lab_client.post(
        "/lab/pattern", json={"id": "transfer", "mode": "atomic"}, headers=headers
    )
    assert resp.status_code == 503 and "busy" in resp.json()["detail"]


@needs_postgres
def test_a_refused_second_run_does_not_take_a_server_slot(
    lab_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from schemalab.lab import patterns

    running, release = threading.Event(), threading.Event()

    def slow(name: str, mode: str, connect: Any) -> dict[str, Any]:
        running.set()
        release.wait(timeout=30)
        return {"name": name, "checks": []}

    monkeypatch.setattr(patterns, "run", slow)
    monkeypatch.setattr(lab_api, "_pattern_slots", threading.BoundedSemaphore(1))
    headers = register(lab_client)
    lab_client.post("/lab/start", headers=headers)
    body = {"id": "transfer", "mode": "atomic"}
    first = threading.Thread(
        target=lambda: lab_client.post("/lab/pattern", json=body, headers=headers)
    )
    first.start()
    assert running.wait(timeout=10)
    resp = lab_client.post("/lab/pattern", json=body, headers=headers)
    release.set()
    first.join(timeout=30)
    assert resp.status_code == 409 and "already running" in resp.json()["detail"]


@needs_postgres
def test_a_failed_experiment_does_not_echo_connection_details(
    lab_client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import psycopg

    from schemalab.lab.session import Lab

    def broken(self: Lab, name: str, mode: str, slots: Any = None) -> dict[str, Any]:
        raise psycopg.OperationalError(
            'connection to server at "10.0.0.5", port 5432 failed: password authentication '
            'failed for user "lab_u1"'
        )

    headers = register(lab_client)
    lab_client.post("/lab/start", headers=headers)
    monkeypatch.setattr(Lab, "pattern", broken)
    resp = lab_client.post(
        "/lab/pattern", json={"id": "transfer", "mode": "atomic"}, headers=headers
    )
    assert resp.status_code == 409
    assert "10.0.0.5" not in resp.text and "password" not in resp.text
    assert "10.0.0.5" not in caplog.text and "password" not in caplog.text  # nor in the logs


@needs_postgres
def test_an_authentication_error_from_the_server_is_not_repeated(
    lab_client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import psycopg

    from schemalab.lab.session import Lab

    class Diag:
        message_primary = 'password authentication failed for user "lab_u1" at 10.0.0.5'

    class AuthFailed(psycopg.errors.InvalidPassword):  # SQLSTATE 28P01, from the server
        @property
        def diag(self) -> Any:
            return Diag()

    def broken(self: Lab, name: str, mode: str, slots: Any = None) -> dict[str, Any]:
        raise AuthFailed("auth")

    headers = register(lab_client)
    lab_client.post("/lab/start", headers=headers)
    monkeypatch.setattr(Lab, "pattern", broken)
    resp = lab_client.post(
        "/lab/pattern", json={"id": "transfer", "mode": "atomic"}, headers=headers
    )
    assert resp.status_code == 409 and "could not finish" in resp.json()["detail"]
    for text in (resp.text, caplog.text):
        assert "10.0.0.5" not in text and "lab_u1" not in text


@needs_postgres
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("TooManyConnections", "too many connections"),  # 53300, can come at connect time
        ("QueryCanceled", "30-second"),  # 57014, the statement timeout
        ("DeadlockDetected", "deadlock"),  # 40P01
        ("InsufficientPrivilege", "could not finish"),  # 42501, unlisted: fixed default
    ],
)
def test_errors_get_written_messages_never_server_text(
    lab_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: str,
    expected: str,
) -> None:
    import psycopg

    from schemalab.lab.session import Lab

    class Diag:
        message_primary = 'role "lab_u1" at 10.0.0.5: details from the server'

    base = getattr(psycopg.errors, error)
    failure = type("Failure", (base,), {"diag": property(lambda self: Diag())})

    def broken(self: Lab, name: str, mode: str, slots: Any = None) -> dict[str, Any]:
        raise failure("x")

    headers = register(lab_client)
    lab_client.post("/lab/start", headers=headers)
    monkeypatch.setattr(Lab, "pattern", broken)
    resp = lab_client.post(
        "/lab/pattern", json={"id": "transfer", "mode": "atomic"}, headers=headers
    )
    assert resp.status_code == 409 and expected in resp.json()["detail"].lower()
    for text in (resp.text, caplog.text):
        assert "10.0.0.5" not in text and "lab_u1" not in text
