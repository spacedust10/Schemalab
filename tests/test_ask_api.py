"""POST /ask streams the analyst's steps as server-sent events."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from schemalab.api.main import create_app, get_db_path, get_gateway, get_settings
from schemalab.config import Settings
from tests.agent.scripted import ScriptedGateway

SQL = "SELECT count(*) AS n FROM customers"


@pytest.fixture
def make_client(sample_db: Path) -> Callable[..., TestClient]:
    def make(*replies: str, share_results: bool = False) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_db_path] = lambda: sample_db
        app.dependency_overrides[get_gateway] = lambda: ScriptedGateway(*replies)
        app.dependency_overrides[get_settings] = lambda: Settings(
            share_results_with_model=share_results
        )
        return TestClient(app)

    return make


def events(body: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def post(client: TestClient, question: str) -> list[dict[str, Any]]:
    with client.stream("POST", "/ask", json={"question": question}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        return events("".join(resp.iter_text()))


def test_streams_the_agent_steps_as_sse(make_client: Callable[..., TestClient]) -> None:
    got = post(make_client("Count customers.", SQL, '{"ok": true}'), "How many customers?")
    assert [e["step"] for e in got] == ["schema", "plan", "sql", "result", "critic", "answer"]
    assert got[-1]["text"] == "n is 10."
    assert got[-1]["confidence"]["label"] == "high"


def test_share_results_setting_reaches_the_agent(make_client: Callable[..., TestClient]) -> None:
    client = make_client("Count.", SQL, '{"ok": true}', "Ten customers.", share_results=True)
    answer = post(client, "How many customers?")[-1]
    assert answer["text"] == "Ten customers."
    assert answer["provenance"]["results_shared_with_model"] is True


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
def test_rejects_empty_or_huge_questions(
    make_client: Callable[..., TestClient], question: str
) -> None:
    assert make_client().post("/ask", json={"question": question}).status_code == 422
