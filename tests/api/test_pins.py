"""Pinned answers: saved per user and re-run through the SQL guard each time they are listed."""

from typing import Any

from fastapi.testclient import TestClient

from schemalab.api.pins import MAX_PINS
from tests.conftest import register

PIN = {
    "question": "How many orders?",
    "sql": "SELECT count(*) AS n FROM orders",
    "dataset": "sample",
}


def pin(client: TestClient, headers: dict[str, str], **override: Any) -> Any:
    return client.post("/pins", json={**PIN, **override}, headers=headers)


def test_pin_and_list_with_a_fresh_result(client: TestClient) -> None:
    headers = register(client)
    made = pin(client, headers)
    assert made.status_code == 201, made.text
    [listed] = client.get("/pins", headers=headers).json()
    assert listed["id"] == made.json()["id"]
    assert listed["question"] == PIN["question"] and listed["sql"] == PIN["sql"]
    assert listed["columns"] == ["n"] and listed["rows"] == [[100]]
    assert listed["error"] is None


def test_unsafe_sql_is_refused_when_pinning(client: TestClient) -> None:
    resp = pin(client, register(client), sql="DROP TABLE orders")
    assert resp.status_code == 400
    assert client.get("/pins", headers=register(client, "b@umich.edu")).json() == []


def test_sql_that_fails_is_refused_when_pinning(client: TestClient) -> None:
    assert pin(client, register(client), sql="SELECT nope FROM orders").status_code == 400


def test_pins_on_uploaded_tables_read_the_users_own_file(client: TestClient) -> None:
    headers = register(client)
    client.post(
        "/datasets",
        params={"name": "sales.csv"},
        content=b"region,amount\nN,1\nS,2\n",
        headers={**headers, "content-type": "application/octet-stream"},
    )
    assert (
        pin(client, headers, sql="SELECT sum(amount) FROM sales", dataset="mine").status_code == 201
    )
    [listed] = client.get("/pins", headers=headers).json()
    assert listed["rows"] == [[3]]
    # Another user's "mine" is their own file, so the same SQL does not reach these tables.
    other = register(client, "other@umich.edu")
    assert (
        pin(client, other, sql="SELECT sum(amount) FROM sales", dataset="mine").status_code == 400
    )


def test_a_pin_that_stops_working_shows_its_error(client: TestClient) -> None:
    headers = register(client)
    client.post(
        "/datasets",
        params={"name": "t.csv"},
        content=b"a\n1\n",
        headers={**headers, "content-type": "application/octet-stream"},
    )
    pin(client, headers, sql="SELECT a FROM t", dataset="mine")
    client.delete("/datasets/t", headers=headers)
    [listed] = client.get("/pins", headers=headers).json()
    assert listed["rows"] is None and listed["error"]


def test_users_cannot_see_or_delete_each_others_pins(client: TestClient) -> None:
    ada, bob = register(client, "ada@umich.edu"), register(client, "bob@umich.edu")
    pin_id = pin(client, ada).json()["id"]
    assert client.get("/pins", headers=bob).json() == []
    assert client.delete(f"/pins/{pin_id}", headers=bob).status_code == 404
    assert client.delete(f"/pins/{pin_id}", headers=ada).status_code == 204
    assert client.get("/pins", headers=ada).json() == []


def test_pin_limit(client: TestClient) -> None:
    headers = register(client)
    for _ in range(MAX_PINS):
        assert pin(client, headers).status_code == 201
    resp = pin(client, headers)
    assert resp.status_code == 400 and str(MAX_PINS) in resp.json()["detail"]


def test_pins_need_sign_in(client: TestClient) -> None:
    assert client.get("/pins").status_code == 401
    assert client.post("/pins", json=PIN).status_code == 401
