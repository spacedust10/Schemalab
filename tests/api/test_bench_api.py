"""GET /bench and POST /bench/run. Runs need LAB_TEST_DSN."""

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemalab.api import bench as bench_api
from schemalab.api.lab import get_lab_dsn
from tests.conftest import register

DSN = os.environ.get("LAB_TEST_DSN", "")
needs_postgres = pytest.mark.skipif(not DSN, reason="set LAB_TEST_DSN")


@pytest.fixture
def bench_client(app: FastAPI, client: TestClient, tmp_path: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_lab_dsn] = lambda: DSN
    app.dependency_overrides[bench_api.get_bench_dir] = lambda: tmp_path / "bench"
    app.dependency_overrides[bench_api.get_bench_operators] = lambda: ["ada@umich.edu"]
    yield client
    bench_api.wait_for_job(timeout_s=120)


def test_needs_sign_in(client: TestClient) -> None:
    assert client.get("/bench").status_code == 401
    assert client.post("/bench/run", json={"benchmark": "tpch", "scale": 0.01}).status_code == 401


def test_lists_benchmarks_and_scales(bench_client: TestClient) -> None:
    got = bench_client.get("/bench", headers=register(bench_client)).json()
    assert {b["id"]: b["scales"] for b in got["benchmarks"]} == {
        "tpch": [0.01, 0.1, 1.0],
        "tpcds": [0.01, 0.1, 1.0],
    }
    assert got["job"]["running"] is False and got["results"] == []


def test_unknown_scale_is_refused(bench_client: TestClient) -> None:
    resp = bench_client.post(
        "/bench/run", json={"benchmark": "tpch", "scale": 5}, headers=register(bench_client)
    )
    assert resp.status_code == 400 and "scale" in resp.json()["detail"]


def test_larger_scales_are_for_operators_only(bench_client: TestClient) -> None:
    headers = register(bench_client, "grace@umich.edu")
    resp = bench_client.post(
        "/bench/run", json={"benchmark": "tpch", "scale": 1.0}, headers=headers
    )
    assert resp.status_code == 403 and "0.01" in resp.json()["detail"]


def test_a_failed_run_shows_a_fixed_message_not_the_exception(
    bench_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(*_: Any) -> None:
        raise RuntimeError("connection to postgresql://postgres:secret@labdb failed")

    monkeypatch.setattr(bench_api.bench, "prepare", boom)
    bench_api._work(tmp_path, "dsn", "tpch", 0.01)
    assert bench_api._job["error"] == "The benchmark failed; the server log has the details."
    assert "secret" not in str(bench_api._job)


def test_a_broken_results_file_is_skipped(bench_client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "bench" / "results"
    folder.mkdir(parents=True)
    (folder / "tpch_sf0_01.json").write_text('{"benchmark": "tp')
    got = bench_client.get("/bench", headers=register(bench_client))
    assert got.status_code == 200 and got.json()["results"] == []


def test_without_a_database_says_so(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[get_lab_dsn] = lambda: ""
    resp = client.post(
        "/bench/run", json={"benchmark": "tpch", "scale": 0.01}, headers=register(client)
    )
    assert resp.status_code == 503


@needs_postgres
def test_a_run_reports_progress_then_saves_its_results(bench_client: TestClient) -> None:
    headers = register(bench_client)
    started = bench_client.post(
        "/bench/run", json={"benchmark": "tpch", "scale": 0.01}, headers=headers
    )
    assert started.status_code == 202
    again = bench_client.post(
        "/bench/run", json={"benchmark": "tpch", "scale": 0.01}, headers=headers
    )
    assert again.status_code == 409
    got: dict[str, Any] = {}
    for _ in range(600):
        got = bench_client.get("/bench", headers=headers).json()
        if not got["job"]["running"]:
            break
        time.sleep(0.2)
    assert got["job"]["error"] is None, got["job"]
    [result] = got["results"]
    assert result["benchmark"] == "tpch" and result["scale"] == 0.01
    assert result["summary"]["queries"] == 22 and result["summary"]["agreed"] == 22
