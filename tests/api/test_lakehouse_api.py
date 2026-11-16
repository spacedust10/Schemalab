"""/lakehouse: hands loads and models to the ingest service, records runs, serves Ask "lake"."""

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemalab import lakehouse
from schemalab.api import lakehouse as api
from schemalab.api.main import get_gateway
from tests.agent.scripted import ScriptedGateway
from tests.api.test_datasets import events
from tests.conftest import register

BRONZE = [{"layer": "bronze", "name": "orders", "rows": 3, "columns": []}]


class FakeIngest:
    def __init__(self, inbox: Path) -> None:
        self.inbox = inbox
        self.calls: list[tuple[Any, ...]] = []
        self.seen: dict[str, bytes] = {}
        self.fail: Exception | None = None
        self.bronze: list[dict[str, Any]] = list(BRONZE)

    def _do(self, *call: Any) -> dict[str, int]:
        self.calls.append(call)
        if self.fail:
            raise self.fail
        return {"orders": 3}

    def load_file(self, lake: str, file: str, table: str) -> dict[str, int]:
        self.seen[file] = (self.inbox / file).read_bytes()
        return self._do("file", lake, file, table)

    def load_sheet(self, lake: str, url: str, table: str) -> dict[str, int]:
        return self._do("sheet", lake, url, table)

    def load_quakes(self, lake: str) -> dict[str, int]:
        return self._do("quakes", lake)

    def transform(self, lake: str, models: list[dict[str, str]]) -> dict[str, int]:
        return self._do("transform", lake, models)

    def tables(self, lake: str) -> list[dict[str, Any]]:
        return self.bronze

    def snapshots(self, lake: str) -> list[dict[str, Any]]:
        return [{"id": 2, "at": "2026-11-14T00:00:00", "changes": {}}]

    def preview(self, lake: str, layer: str, table: str, version: int | None) -> dict[str, Any]:
        self.calls.append(("preview", lake, layer, table, version))
        return {"columns": ["id"], "rows": [[1]], "total": 1}


@pytest.fixture
def lake_dir(tmp_path: Path) -> Path:
    return tmp_path / "lake"


@pytest.fixture
def ingest(app: FastAPI, lake_dir: Path) -> FakeIngest:
    fake = FakeIngest(lake_dir / "inbox")
    app.dependency_overrides[api.get_ingest] = lambda: fake
    app.dependency_overrides[api.get_lake_dir] = lambda: lake_dir
    return fake


@pytest.fixture(autouse=True)
def inline(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Callable[[], None]]]:
    """Runs start inline, so a test sees the finished run."""
    held: list[Callable[[], None]] = []
    monkeypatch.setattr(api, "_spawn", lambda job: job())
    yield held


def runs(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    return client.get("/lakehouse", headers=headers).json()["runs"]


def send(client: TestClient, headers: dict[str, str], body: bytes, name: str, **params: str) -> Any:
    return client.post(
        "/lakehouse/files",
        params={"name": name, **params},
        content=body,
        headers={**headers, "content-type": "application/octet-stream"},
    )


def test_the_lakehouse_needs_sign_in(client: TestClient) -> None:
    assert client.get("/lakehouse").status_code == 401


def test_without_an_ingest_service_the_lakehouse_says_so(client: TestClient) -> None:
    resp = client.get("/lakehouse", headers=register(client))
    assert resp.status_code == 503
    assert "lakehouse" in resp.json()["detail"].lower()


def test_a_csv_is_handed_over_and_the_run_recorded(
    client: TestClient, ingest: FakeIngest, lake_dir: Path
) -> None:
    headers = register(client)
    resp = send(client, headers, b"id\n1\n2\n3\n", "Orders.csv")
    assert resp.status_code == 202, resp.text
    [(kind, lake, file, table)] = ingest.calls
    assert (kind, lake, table) == ("file", "u1", "orders")
    assert re.fullmatch(r"[A-Za-z0-9_-]+\.csv", file)
    assert ingest.seen[file] == b"id\n1\n2\n3\n"
    [run] = runs(client, headers)
    assert (run["kind"], run["label"], run["status"]) == ("file", "Orders.csv", "done")
    assert run["result"] == {"orders": 3}


def test_a_table_name_can_be_chosen(client: TestClient, ingest: FakeIngest) -> None:
    send(client, register(client), b"id\n1\n", "x.csv", table="Sales 2026")
    assert ingest.calls[0][3] == "sales_2026"


def test_each_excel_sheet_is_its_own_load(
    client: TestClient, ingest: FakeIngest, tmp_path: Path
) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["a"])  # type: ignore[union-attr]
    wb.active.append([1])  # type: ignore[union-attr]
    wb.create_sheet("West").append(["b"])
    path = tmp_path / "book.xlsx"
    wb.save(path)
    assert send(client, register(client), path.read_bytes(), "Book.xlsx").status_code == 202
    assert [c[3] for c in ingest.calls] == ["book_sheet", "book_west"]


def test_parquet_is_handed_over_as_parquet(client: TestClient, ingest: FakeIngest) -> None:
    send(client, register(client), b"PAR1....PAR1", "t.parquet")
    assert ingest.calls[0][2].endswith(".parquet")


def test_other_files_are_refused_and_nothing_is_kept(
    client: TestClient, ingest: FakeIngest, lake_dir: Path
) -> None:
    resp = send(client, register(client), b"#!/bin/sh", "x.sh")
    assert resp.status_code == 400
    assert ".csv, .xlsx or .parquet" in resp.json()["detail"]
    assert not any((lake_dir / "inbox").glob("*")) if (lake_dir / "inbox").exists() else True
    assert ingest.calls == []


def test_quakes_and_sheets_start_runs(client: TestClient, ingest: FakeIngest) -> None:
    headers = register(client)
    assert client.post("/lakehouse/quakes", headers=headers).status_code == 202
    url = "https://docs.google.com/spreadsheets/d/abc/edit"
    resp = client.post("/lakehouse/sheets", json={"url": url, "table": "Budget"}, headers=headers)
    assert resp.status_code == 202
    assert ingest.calls == [("quakes", "u1"), ("sheet", "u1", url, "budget")]


def test_a_failed_run_keeps_its_reason(client: TestClient, ingest: FakeIngest) -> None:
    ingest.fail = lakehouse.LakehouseError("The file could not be read: bad quote")
    headers = register(client)
    send(client, headers, b"x", "a.csv")
    [run] = runs(client, headers)
    assert run["status"] == "failed"
    assert run["error"] == "The file could not be read: bad quote"


def test_an_unexpected_failure_does_not_leak_its_details(
    client: TestClient, ingest: FakeIngest
) -> None:
    ingest.fail = RuntimeError("password=hunter2 at lakedb:5432")
    headers = register(client)
    client.post("/lakehouse/quakes", headers=headers)
    [run] = runs(client, headers)
    assert run["status"] == "failed"
    assert "hunter2" not in run["error"]


def test_one_run_per_user_at_a_time(
    client: TestClient, ingest: FakeIngest, monkeypatch: pytest.MonkeyPatch
) -> None:
    held: list[Callable[[], None]] = []
    monkeypatch.setattr(api, "_spawn", held.append)
    ada, bob = register(client), register(client, "bob@umich.edu")
    assert client.post("/lakehouse/quakes", headers=ada).status_code == 202
    assert client.post("/lakehouse/quakes", headers=ada).status_code == 409
    assert client.post("/lakehouse/quakes", headers=bob).status_code == 202
    for job in held:
        job()
    assert client.post("/lakehouse/quakes", headers=ada).status_code == 202
    for job in held[2:]:
        job()


def test_runs_are_capped_across_users(
    client: TestClient, ingest: FakeIngest, monkeypatch: pytest.MonkeyPatch
) -> None:
    held: list[Callable[[], None]] = []
    monkeypatch.setattr(api, "_spawn", held.append)
    users = [register(client, f"u{i}@umich.edu") for i in range(api.MAX_RUNS + 1)]
    codes = [client.post("/lakehouse/quakes", headers=h).status_code for h in users]
    assert codes == [202] * api.MAX_RUNS + [503]
    for job in held:
        job()


def test_a_run_lost_to_a_restart_shows_as_interrupted(
    client: TestClient, ingest: FakeIngest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "_spawn", lambda job: None)  # the job never runs
    headers = register(client)
    client.post("/lakehouse/quakes", headers=headers)
    api._ACTIVE.clear()  # what a restart leaves behind
    api._RUNNING.clear()
    api._SLOTS.release()
    [run] = runs(client, headers)
    assert run["status"] == "failed"
    assert "interrupted" in run["error"]


GOOD = [
    {"layer": "gold", "name": "total", "sql": "SELECT count(*) AS n FROM silver_paid"},
    {"layer": "silver", "name": "paid", "sql": "SELECT id FROM bronze_orders"},
]


def test_models_are_saved_and_listed(client: TestClient, ingest: FakeIngest) -> None:
    headers = register(client)
    assert (
        client.put("/lakehouse/models", json={"models": GOOD}, headers=headers).status_code == 200
    )
    assert client.get("/lakehouse", headers=headers).json()["models"] == GOOD
    assert client.get("/lakehouse", headers=register(client, "b@umich.edu")).json()["models"] == []


@pytest.mark.parametrize(
    "model",
    [
        {"layer": "bronze", "name": "x", "sql": "SELECT 1"},
        {"layer": "silver", "name": "Bad Name", "sql": "SELECT 1"},
        {"layer": "silver", "name": "x", "sql": "S" * 5001},
    ],
)
def test_malformed_models_are_refused(
    client: TestClient, ingest: FakeIngest, model: dict[str, str]
) -> None:
    resp = client.put("/lakehouse/models", json={"models": [model]}, headers=register(client))
    assert resp.status_code in (400, 422)


def test_a_transform_runs_silver_before_gold_with_checked_sql(
    client: TestClient, ingest: FakeIngest
) -> None:
    headers = register(client)
    client.put("/lakehouse/models", json={"models": GOOD}, headers=headers)
    assert client.post("/lakehouse/transform", headers=headers).status_code == 202
    [(_, lake, models)] = ingest.calls
    assert lake == "u1"
    assert [(m["layer"], m["name"]) for m in models] == [("silver", "paid"), ("gold", "total")]


@pytest.mark.parametrize(
    ("sql", "why"),
    [
        ("SELECT * FROM read_csv('/etc/passwd')", "table functions"),
        ("SELECT * FROM postgres_query('__ducklake_metadata_lake', 'SELECT 1')", "table"),
        ("SELECT * FROM __ducklake_metadata_lake.u2.ducklake_table", "qualified"),
        ("SELECT * FROM bronze_secrets", "unknown table"),
        ("DROP TABLE bronze_orders", "only SELECT"),
    ],
)
def test_the_sql_guard_checks_models_before_ingest_sees_them(
    client: TestClient, ingest: FakeIngest, sql: str, why: str
) -> None:
    headers = register(client)
    model = {"layer": "silver", "name": "x", "sql": sql}
    client.put("/lakehouse/models", json={"models": [model]}, headers=headers)
    resp = client.post("/lakehouse/transform", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("silver.x:")
    assert why in resp.json()["detail"]
    assert ingest.calls == []
    assert runs(client, headers) == []


def test_a_transform_with_no_models_says_what_to_do(client: TestClient, ingest: FakeIngest) -> None:
    resp = client.post("/lakehouse/transform", headers=register(client))
    assert resp.status_code == 400
    assert "model" in resp.json()["detail"]


def test_previews_pass_the_version_and_refuse_bad_names(
    client: TestClient, ingest: FakeIngest
) -> None:
    headers = register(client)
    ok = client.get("/lakehouse/tables/bronze/orders", params={"version": 2}, headers=headers)
    assert ok.json()["rows"] == [[1]]
    assert ingest.calls == [("preview", "u1", "bronze", "orders", 2)]
    assert client.get("/lakehouse/tables/public/x", headers=headers).status_code == 404
    assert client.get("/lakehouse/tables/bronze/x;y", headers=headers).status_code == 404


def test_the_overview_shows_tables_and_snapshots(client: TestClient, ingest: FakeIngest) -> None:
    body = client.get("/lakehouse", headers=register(client)).json()
    assert body["tables"] == BRONZE
    assert body["snapshots"][0]["id"] == 2


def test_ask_reads_the_published_lake(
    app: FastAPI, client: TestClient, ingest: FakeIngest, lake_dir: Path
) -> None:
    headers = register(client)
    (lake_dir / "publish").mkdir(parents=True)
    with duckdb.connect(str(lake_dir / "publish" / "u1.duckdb")) as con:
        con.execute("CREATE TABLE gold_revenue AS SELECT 14.5 AS total")
    gw = ScriptedGateway("Sum it.", "SELECT total FROM gold_revenue", '{"ok": true}')
    app.dependency_overrides[get_gateway] = lambda: gw
    got = events(client, headers, {"question": "Revenue?", "dataset": "lake"})
    assert next(e for e in got if e["step"] == "result")["rows"] == [[14.5]]


def test_ask_on_an_empty_lake_says_what_to_do(
    app: FastAPI, client: TestClient, ingest: FakeIngest
) -> None:
    app.dependency_overrides[get_gateway] = lambda: ScriptedGateway()
    resp = client.post(
        "/ask", json={"question": "How many?", "dataset": "lake"}, headers=register(client)
    )
    assert resp.status_code == 400
    assert "silver or gold" in resp.json()["detail"]


def test_an_interrupted_run_stays_failed_once_a_new_one_starts(
    client: TestClient, ingest: FakeIngest, monkeypatch: pytest.MonkeyPatch
) -> None:
    held: list[Callable[[], None]] = []
    monkeypatch.setattr(api, "_spawn", held.append)
    headers = register(client)
    client.post("/lakehouse/quakes", headers=headers)
    api._ACTIVE.clear()  # a restart: the first run's thread is gone
    api._RUNNING.clear()
    api._SLOTS.release()
    held.clear()
    client.post("/lakehouse/quakes", headers=headers)  # still going
    assert [r["status"] for r in runs(client, headers)] == ["running", "failed"]
    held[0]()
    assert [r["status"] for r in runs(client, headers)] == ["done", "failed"]


def test_runs_and_models_still_show_when_ingest_is_down(
    client: TestClient, ingest: FakeIngest
) -> None:
    headers = register(client)
    client.put("/lakehouse/models", json={"models": GOOD}, headers=headers)
    client.post("/lakehouse/quakes", headers=headers)

    def down(lake: str) -> Any:
        raise lakehouse.Unavailable("connection refused")

    ingest.tables = down  # type: ignore[method-assign]
    body = client.get("/lakehouse", headers=headers).json()
    assert body["available"] is False
    assert body["tables"] == [] and body["snapshots"] == []
    assert len(body["runs"]) == 1 and body["models"] == GOOD


def test_handed_files_are_removed_if_the_run_cannot_be_recorded(
    client: TestClient, ingest: FakeIngest, lake_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from schemalab import store

    def broken(*args: Any) -> int:
        raise RuntimeError("database is down")

    monkeypatch.setattr(store, "add_lake_run", broken)
    headers = register(client)
    with pytest.raises(RuntimeError):
        send(client, headers, b"id\n1\n", "a.csv")
    assert list((lake_dir / "inbox").iterdir()) == []
    monkeypatch.undo()
    monkeypatch.setattr(api, "_spawn", lambda job: job())
    assert send(client, headers, b"id\n1\n", "a.csv").status_code == 202  # the slot came back
