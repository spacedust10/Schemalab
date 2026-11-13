"""POST /algebra and GET /constraints."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import register


def algebra(client: TestClient, body: dict[str, Any]) -> Any:
    return client.post("/algebra", json=body, headers=register(client))


def test_needs_sign_in(client: TestClient) -> None:
    assert client.post("/algebra", json={"expression": "orders"}).status_code == 401
    assert client.get("/constraints", params={"table": "orders"}).status_code == 401


def test_algebra_translates_draws_and_runs(client: TestClient) -> None:
    got = algebra(client, {"expression": "π[customer_id](σ[amount > 140](orders))"}).json()
    assert "SELECT DISTINCT customer_id FROM orders" in " ".join(got["sql"].split())
    assert got["tree"]["label"] == "π customer_id"
    assert got["result"]["columns"] == ["customer_id"]
    assert sorted(r[0] for r in got["result"]["rows"]) == [0, 4, 5, 6, 7, 8, 9]
    assert got["ra"] == "π[customer_id](σ[amount > 140](orders))"


def test_sql_translates_to_algebra(client: TestClient) -> None:
    got = algebra(client, {"sql": "SELECT name FROM customers WHERE id < 3"}).json()
    assert got["ra"] == "π[name](σ[id < 3](customers))"
    assert any("duplicates" in n for n in got["notes"])
    assert len(got["result"]["rows"]) == 3


def test_unknown_relations_are_named(client: TestClient) -> None:
    resp = algebra(client, {"expression": "σ[x > 1](students)"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "students is not a table here. Tables: customers, orders."


def test_parse_errors_are_400(client: TestClient) -> None:
    resp = algebra(client, {"expression": "π[name](customers"})
    assert resp.status_code == 400 and "expected )" in resp.json()["detail"]


def test_exactly_one_of_expression_or_sql(client: TestClient) -> None:
    headers = register(client)
    assert client.post("/algebra", json={}, headers=headers).status_code == 422
    both = {"expression": "orders", "sql": "SELECT 1"}
    assert client.post("/algebra", json=both, headers=headers).status_code == 422


def test_a_query_that_fails_to_run_still_returns_its_translation(client: TestClient) -> None:
    got = algebra(client, {"expression": "σ[nope > 1](orders)"}).json()
    assert got["result"] is None and "nope" in got["run_error"]
    assert got["tree"]["label"] == "σ nope > 1"


def test_constraints_for_a_sample_table(client: TestClient) -> None:
    got = client.get("/constraints", params={"table": "orders"}, headers=register(client)).json()
    kinds = {s["kind"] for s in got}
    assert "primary key" in kinds
    pk = next(s for s in got if s["kind"] == "primary key")
    assert pk["columns"] == ["id"] and pk["sql"].startswith("ALTER TABLE orders")


def test_constraints_for_an_unknown_table(client: TestClient) -> None:
    resp = client.get("/constraints", params={"table": "nope"}, headers=register(client))
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"expression": " ∪ ".join(["orders"] * 450)},  # inside the 5,000-character limit
        {"sql": "SELECT * FROM orders" + " UNION SELECT * FROM orders" * 600},
    ],
)
def test_too_deep_trees_are_a_400_not_a_crash(client: TestClient, body: dict[str, str]) -> None:
    resp = client.post("/algebra", json=body, headers=register(client))
    assert resp.status_code == 400 and "nested too deeply" in resp.json()["detail"]
