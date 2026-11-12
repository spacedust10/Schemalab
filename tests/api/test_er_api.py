"""ER designer endpoints."""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemalab.api.main import get_gateway
from tests.agent.scripted import ScriptedGateway
from tests.conftest import register

DESIGN = """entity Student { sid int key, name text }
entity Course { cid varchar(10) key, title text }
relationship Enrolls: Student M -- N Course { grade text }"""


def post(
    client: TestClient, path: str, body: dict[str, Any], headers: dict[str, str] | None = None
) -> Any:
    return client.post(path, json=body, headers=headers or register(client))


def test_needs_sign_in(client: TestClient) -> None:
    assert client.post("/er/ddl", json={"design": DESIGN}).status_code == 401


def test_design_to_ddl_with_the_model_for_drawing(client: TestClient) -> None:
    got = post(client, "/er/ddl", {"design": DESIGN, "dialect": "oracle"}).json()
    assert "CREATE TABLE enrolls" in got["ddl"] and "VARCHAR2(10)" in got["ddl"]
    assert [e["name"] for e in got["model"]["entities"]] == ["Student", "Course"]
    [rel] = got["model"]["relationships"]
    assert rel["kind"] == "M:N" and rel["left"] == {
        "entity": "Student",
        "card": "N",
        "total": False,
    }
    assert got["model"]["entities"][0]["attributes"][0] == {
        "name": "sid",
        "type": "INT",
        "key": True,
    }


def test_design_errors_are_400_with_the_line(client: TestClient) -> None:
    resp = post(client, "/er/ddl", {"design": "entity A { x int }"})
    assert resp.status_code == 400 and resp.json()["detail"].startswith("line 1:")


def test_unknown_dialect_is_422(client: TestClient) -> None:
    assert post(client, "/er/ddl", {"design": DESIGN, "dialect": "db2"}).status_code == 422


def test_english_to_design_uses_the_model(app: FastAPI, client: TestClient) -> None:
    gw = ScriptedGateway(f"```\n{DESIGN}\n```")
    app.dependency_overrides[get_gateway] = lambda: gw
    got = post(client, "/er/from-english", {"description": "Students take courses."}).json()
    assert "relationship Enrolls" in got["design"]


def test_english_that_never_parses_is_400(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[get_gateway] = lambda: ScriptedGateway("nope", "still nope")
    resp = post(client, "/er/from-english", {"description": "Students take courses."})
    assert resp.status_code == 400 and "start a line with entity" in resp.json()["detail"]


def test_design_from_the_users_uploaded_dump(client: TestClient) -> None:
    headers = register(client)
    dump = (
        b"CREATE TABLE customers (id int PRIMARY KEY, name text);"
        b"CREATE TABLE orders (id int PRIMARY KEY,"
        b" customer_id int REFERENCES customers(id), total numeric(8,2));"
        b"INSERT INTO customers VALUES (1, 'Ada'); INSERT INTO orders VALUES (10, 1, 9.5);"
    )
    client.post(
        "/datasets",
        params={"name": "shop.sql"},
        content=dump,
        headers={**headers, "content-type": "application/octet-stream"},
    )
    got = client.get("/er/mine", headers=headers).json()
    assert [e["name"] for e in got["model"]["entities"]] == ["Customers", "Orders"]
    [rel] = got["model"]["relationships"]
    assert (rel["kind"], rel["left"]["entity"], rel["right"]["entity"]) == (
        "1:N",
        "Customers",
        "Orders",
    )


def test_design_from_uploads_without_any(client: TestClient) -> None:
    resp = client.get("/er/mine", headers=register(client))
    assert resp.status_code == 400 and "Upload" in resp.json()["detail"]


def test_the_design_box_also_takes_create_table_statements(client: TestClient) -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY);\n"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id));"
    )
    got = post(client, "/er/ddl", {"design": sql}).json()
    assert got["source"] == "sql"
    assert [e["name"] for e in got["model"]["entities"]] == ["A", "B"]
    assert got["model"]["relationships"][0]["kind"] == "1:N"


def test_design_text_is_still_read_as_design(client: TestClient) -> None:
    assert post(client, "/er/ddl", {"design": DESIGN}).json()["source"] == "design"


def test_a_whole_dump_file_fits(client: TestClient) -> None:
    rows = "\n".join(f"INSERT INTO a VALUES ({i});" for i in range(8000))
    dump = f"CREATE TABLE a (id INT PRIMARY KEY);\n{rows}"
    assert len(dump) > 100_000
    assert post(client, "/er/ddl", {"design": dump}).status_code == 200
