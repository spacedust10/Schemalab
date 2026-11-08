"""Correction notes: per user, and fed into that user's prompts only."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemalab.api.main import get_gateway
from tests.agent.scripted import ScriptedGateway
from tests.conftest import register

NOTE = "Fiscal year starts in July."


def test_add_list_delete(client: TestClient) -> None:
    headers = register(client)
    made = client.post("/corrections", json={"text": NOTE}, headers=headers)
    assert made.status_code == 201
    assert client.get("/corrections", headers=headers).json() == [made.json()]
    assert client.delete(f"/corrections/{made.json()['id']}", headers=headers).status_code == 204
    assert client.get("/corrections", headers=headers).json() == []


@pytest.mark.parametrize("text", ["", "   ", "x" * 501])
def test_rejects_empty_or_long_notes(client: TestClient, text: str) -> None:
    resp = client.post("/corrections", json={"text": text}, headers=register(client))
    assert resp.status_code == 422


def test_cannot_see_or_delete_another_users_note(client: TestClient) -> None:
    ada = register(client, "ada@umich.edu")
    note_id = client.post("/corrections", json={"text": NOTE}, headers=ada).json()["id"]
    grace = register(client, "grace@umich.edu")
    assert client.get("/corrections", headers=grace).json() == []
    assert client.delete(f"/corrections/{note_id}", headers=grace).status_code == 404
    assert len(client.get("/corrections", headers=ada).json()) == 1


def test_corrections_need_a_session(client: TestClient) -> None:
    assert client.get("/corrections").status_code == 401
    assert client.post("/corrections", json={"text": NOTE}).status_code == 401


def test_notes_reach_only_their_owners_prompts(app: FastAPI, client: TestClient) -> None:
    ada = register(client, "ada@umich.edu")
    client.post("/corrections", json={"text": NOTE}, headers=ada)
    grace = register(client, "grace@umich.edu")

    for headers, should_see in [(ada, True), (grace, False)]:
        gw = ScriptedGateway("Plan.", "SELECT count(*) FROM orders", '{"ok": true}')
        # A default-arg lambda would look like a query parameter to FastAPI.
        app.dependency_overrides[get_gateway] = (lambda g: lambda: g)(gw)
        with client.stream(
            "POST", "/ask", json={"question": "Revenue by FY?"}, headers=headers
        ) as r:
            "".join(r.iter_text())
        for i in range(3):  # planner, SQL writer, critic
            assert (NOTE in gw.prompt(i)) is should_see, (headers, i)
