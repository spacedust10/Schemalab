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
        if got["state"] != "waiting":
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
