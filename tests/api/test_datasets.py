"""/datasets: upload, list and delete a user's own tables; /ask can query them."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemalab import uploads
from schemalab.api.main import get_gateway
from tests.agent.scripted import ScriptedGateway
from tests.conftest import register

CSV = b"id,amount\n1,9.5\n2,3.0\n3,\n"


def upload(
    client: TestClient, headers: dict[str, str], body: Any = CSV, name: str = "Orders.csv"
) -> Any:
    return client.post(
        "/datasets",
        params={"name": name},
        content=body,
        headers={**headers, "content-type": "application/octet-stream"},
    )


def test_upload_creates_a_profiled_table(client: TestClient) -> None:
    resp = upload(client, register(client))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] == ["orders"]
    [table] = body["tables"]
    assert table["name"] == "orders" and table["rows"] == 3
    amount = next(c for c in table["columns"] if c["name"] == "amount")
    assert amount["type"] == "DOUBLE" and amount["null_percent"] > 33


def test_upload_needs_sign_in(client: TestClient) -> None:
    assert upload(client, {}).status_code == 401


def test_each_user_sees_only_their_own_tables(client: TestClient) -> None:
    ada, bob = register(client, "ada@umich.edu"), register(client, "bob@umich.edu")
    upload(client, ada)
    assert client.get("/datasets", headers=bob).json() == []
    assert [t["name"] for t in client.get("/datasets", headers=ada).json()] == ["orders"]
    assert client.delete("/datasets/orders", headers=bob).status_code == 404
    assert len(client.get("/datasets", headers=ada).json()) == 1


def test_unsupported_file_is_refused_with_a_reason(client: TestClient) -> None:
    resp = upload(client, register(client), name="data.json")
    assert resp.status_code == 400
    assert ".csv or .xlsx" in resp.json()["detail"]


def test_name_is_required(client: TestClient) -> None:
    resp = client.post("/datasets", content=CSV, headers=register(client))
    assert resp.status_code == 422


def test_declared_size_over_the_limit_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads, "MAX_BYTES", 10)
    resp = upload(client, register(client))
    assert resp.status_code == 413
    assert client.get("/datasets", headers=register(client, "c@umich.edu")).json() == []


def test_streamed_size_over_the_limit_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No Content-Length: a chunked body must be cut off while it streams.
    monkeypatch.setattr(uploads, "MAX_BYTES", 10)

    def chunks() -> Iterator[bytes]:
        yield CSV[:8]
        yield CSV[8:]

    headers = register(client)
    assert upload(client, headers, body=chunks()).status_code == 413
    assert client.get("/datasets", headers=headers).json() == []


def test_delete_removes_a_table(client: TestClient) -> None:
    headers = register(client)
    upload(client, headers)
    assert client.delete("/datasets/orders", headers=headers).status_code == 204
    assert client.get("/datasets", headers=headers).json() == []
    assert client.delete("/datasets/orders", headers=headers).status_code == 404


def events(client: TestClient, headers: dict[str, str], payload: dict[str, str]) -> Any:
    with client.stream("POST", "/ask", json=payload, headers=headers) as resp:
        assert resp.status_code == 200, resp.read()
        body = "".join(resp.iter_text())
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_ask_can_query_the_users_uploads(app: FastAPI, client: TestClient) -> None:
    gw = ScriptedGateway("Count orders.", "SELECT count(*) AS n FROM orders", '{"ok": true}')
    app.dependency_overrides[get_gateway] = lambda: gw
    headers = register(client)
    upload(client, headers)
    got = events(client, headers, {"question": "How many orders?", "dataset": "mine"})
    assert got[-1]["step"] == "answer"
    assert next(e for e in got if e["step"] == "result")["rows"] == [[3]]
    assert "orders (3 rows)" in gw.prompt(0)
    assert "customers" not in gw.prompt(0)  # the sample tables are not offered


def test_ask_about_uploads_before_any_upload_says_what_to_do(
    app: FastAPI, client: TestClient
) -> None:
    app.dependency_overrides[get_gateway] = lambda: ScriptedGateway()
    resp = client.post(
        "/ask", json={"question": "How many?", "dataset": "mine"}, headers=register(client)
    )
    assert resp.status_code == 400
    assert "Upload a file first" in resp.json()["detail"]


def test_ask_rejects_unknown_datasets(client: TestClient) -> None:
    resp = client.post(
        "/ask", json={"question": "How many?", "dataset": "../1"}, headers=register(client)
    )
    assert resp.status_code == 422


def test_excel_upload_through_the_api(client: TestClient, tmp_path: Any) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["region", "revenue"])  # type: ignore[union-attr]
    wb.active.append(["North", 10])  # type: ignore[union-attr]
    path = tmp_path / "book.xlsx"
    wb.save(path)
    resp = upload(client, register(client), body=path.read_bytes(), name="Sales.xlsx")
    assert resp.status_code == 201, resp.text
    assert resp.json()["created"] == ["sales"]
