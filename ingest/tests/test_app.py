"""The HTTP face the API calls. Lake work needs LAKE_TEST_DSN; refusals do not."""

import os
import secrets
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ingest.app import create_app
from ingest.config import Settings

DSN = os.environ.get("LAKE_TEST_DSN", "")
needs_lake = pytest.mark.skipif(not DSN, reason="set LAKE_TEST_DSN to a Postgres to run")


@pytest.fixture
def s(tmp_path: Path) -> Settings:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return Settings(
        catalog=DSN or "postgresql://unused/unused",
        data_root=str(tmp_path / "data"),
        publish_dir=tmp_path / "publish",
        work_dir=tmp_path / "work",
        inbox_dir=inbox,
        token=TOKEN,
    )


TOKEN = "t" * 43


@pytest.fixture
def client(s: Settings) -> TestClient:
    return TestClient(create_app(s), headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.mark.parametrize("auth", [None, "Bearer wrong", f"Basic {TOKEN}", f"Bearer {TOKEN}x"])
def test_only_the_api_token_gets_in(s: Settings, auth: str | None) -> None:
    anon = TestClient(create_app(s))
    headers = {"Authorization": auth} if auth else {}
    assert anon.get("/lakes/u1/tables", headers=headers).status_code == 401
    assert anon.post("/lakes/u1/load/quakes", headers=headers).status_code == 401
    assert anon.get("/health").status_code == 200  # for the Compose health check


def test_an_empty_token_is_refused_at_start(s: Settings) -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="INGEST_TOKEN"):
        create_app(replace(s, token=""))


@pytest.fixture
def lake() -> str:
    return f"u{secrets.randbelow(10**9)}"


@pytest.mark.parametrize("file", ["../secret.csv", "a/b.csv", "x.sh", ".csv", "x.CSV"])
def test_only_plain_inbox_file_names_are_accepted(client: TestClient, file: str) -> None:
    r = client.post("/lakes/u1/load/file", json={"file": file, "table": "t"})
    assert r.status_code == 400


def test_an_unknown_lake_name_is_refused(client: TestClient) -> None:
    assert client.get("/lakes/public/tables").status_code == 404


def test_a_bad_sheet_link_is_refused(client: TestClient) -> None:
    r = client.post("/lakes/u1/load/sheet", json={"url": "http://10.0.0.1/", "table": "t"})
    assert r.status_code == 400
    assert "Google Sheet" in r.json()["detail"]


@needs_lake
def test_a_handed_over_file_is_loaded_then_deleted(
    client: TestClient, s: Settings, lake: str
) -> None:
    (s.inbox_dir / "abc123.csv").write_text("id,n\n1,2\n")
    r = client.post(f"/lakes/{lake}/load/file", json={"file": "abc123.csv", "table": "t"})
    assert r.status_code == 200, r.text
    newest = client.get(f"/lakes/{lake}/snapshots").json()[0]["id"]
    assert r.json() == {"tables": {"t": 1}, "snapshot": newest}
    assert not (s.inbox_dir / "abc123.csv").exists()
    [table] = client.get(f"/lakes/{lake}/tables").json()
    assert (table["layer"], table["name"], table["rows"]) == ("bronze", "t", 1)


@needs_lake
def test_models_snapshots_and_previews(client: TestClient, s: Settings, lake: str) -> None:
    (s.inbox_dir / "f.csv").write_text("id,n\n1,2\n2,3\n")
    client.post(f"/lakes/{lake}/load/file", json={"file": "f.csv", "table": "t"})
    models: list[dict[str, Any]] = [
        {"layer": "silver", "name": "t", "sql": "SELECT id, n * 2 AS n2 FROM bronze_t"}
    ]
    r = client.post(f"/lakes/{lake}/transform", json={"models": models})
    assert r.json()["tables"] == {"silver.t": 2}
    assert r.json()["snapshot"] == client.get(f"/lakes/{lake}/snapshots").json()[0]["id"]
    bad = client.post(f"/lakes/{lake}/transform", json={"models": [{**models[0], "sql": "x"}]})
    assert bad.status_code == 400
    assert bad.json()["detail"].startswith("silver.t:")
    snaps = client.get(f"/lakes/{lake}/snapshots").json()
    assert snaps[0]["id"] > snaps[-1]["id"]
    p = client.get(f"/lakes/{lake}/tables/silver/t").json()
    assert p == {"columns": ["id", "n2"], "rows": [[1, 4], [2, 6]], "total": 2}
    assert client.get(f"/lakes/{lake}/tables/silver/nope").status_code == 400


@needs_lake
def test_one_run_per_lake_at_a_time(client: TestClient, s: Settings, lake: str) -> None:
    from ingest import app as app_module

    with app_module.busy(lake):
        r = client.post(f"/lakes/{lake}/transform", json={"models": []})
    assert r.status_code == 409
