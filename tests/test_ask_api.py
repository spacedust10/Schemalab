"""POST /ask streams the analyst's steps as server-sent events."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from schemalab.api.main import create_app, get_db_path, get_gateway
from schemalab.llm import Gateway

MODEL = "ollama_chat/llama3.1"


@pytest.fixture
def client(sample_db: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db_path] = lambda: sample_db
    app.dependency_overrides[get_gateway] = lambda: Gateway(
        [MODEL], options={MODEL: {"mock_response": "SELECT count(*) AS n FROM customers"}}
    )
    return TestClient(app)


def events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]


def test_streams_events_as_sse(client: TestClient) -> None:
    with client.stream("POST", "/ask", json={"question": "How many customers?"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    got = events(body)
    assert [e["step"] for e in got] == ["schema", "sql", "result"]
    assert got[-1]["rows"] == [[10]]


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
def test_rejects_empty_or_huge_questions(client: TestClient, question: str) -> None:
    assert client.post("/ask", json={"question": question}).status_code == 422
