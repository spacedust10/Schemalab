"""POST /ask: signed-in users only, streamed as SSE, saved to the caller's history."""

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemalab.api.main import get_gateway, get_settings
from schemalab.config import Settings
from tests.agent.scripted import ScriptedGateway
from tests.conftest import register

SQL = "SELECT count(*) AS n FROM customers"
OK = '{"ok": true}'


def script(app: FastAPI, *replies: str | Exception) -> None:
    app.dependency_overrides[get_gateway] = lambda: ScriptedGateway(*replies)


def ask(client: TestClient, headers: dict[str, str], question: str) -> list[dict[str, Any]]:
    with client.stream("POST", "/ask", json={"question": question}, headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_streams_the_agent_steps(app: FastAPI, client: TestClient) -> None:
    script(app, "Count customers.", SQL, OK)
    got = ask(client, register(client), "How many customers?")
    assert [e["step"] for e in got] == ["schema", "plan", "sql", "result", "critic", "answer"]
    assert got[-1]["text"] == "n is 10."


def test_share_results_setting_reaches_the_agent(app: FastAPI, client: TestClient) -> None:
    script(app, "Count.", SQL, OK, "Ten customers.")
    app.dependency_overrides[get_settings] = lambda: Settings(share_results_with_model=True)
    answer = ask(client, register(client), "How many customers?")[-1]
    assert answer["text"] == "Ten customers."
    assert answer["provenance"]["results_shared_with_model"] is True


def test_ask_needs_a_session(client: TestClient) -> None:
    assert client.post("/ask", json={"question": "hi"}).status_code == 401


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
def test_rejects_empty_or_huge_questions(client: TestClient, question: str) -> None:
    resp = client.post("/ask", json={"question": question}, headers=register(client))
    assert resp.status_code == 422


def test_history_keeps_question_answer_and_sql(app: FastAPI, client: TestClient) -> None:
    headers = register(client)
    script(app, "Count.", SQL, OK)
    ask(client, headers, "How many customers?")
    script(app, "Count.", "DROP TABLE a", "DROP TABLE b", "DROP TABLE c")
    ask(client, headers, "Drop everything")

    items = client.get("/history", headers=headers).json()
    assert [i["question"] for i in items] == ["Drop everything", "How many customers?"]
    failed, answered = items
    assert answered["outcome"]["step"] == "answer"
    assert answered["outcome"]["text"] == "n is 10."
    assert answered["outcome"]["provenance"]["sql"] == "SELECT COUNT(*) AS n FROM customers"
    assert failed["outcome"]["step"] == "error" and failed["outcome"]["stage"] == "guard"
    assert answered["created_at"]


def test_history_is_private_to_each_user(app: FastAPI, client: TestClient) -> None:
    ada = register(client, "ada@umich.edu")
    script(app, "Count.", SQL, OK)
    ask(client, ada, "Ada's question")
    grace = register(client, "grace@umich.edu")
    assert client.get("/history", headers=grace).json() == []


def test_history_needs_a_session(client: TestClient) -> None:
    assert client.get("/history").status_code == 401
