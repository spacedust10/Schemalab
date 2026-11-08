from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    """A small DuckDB file with two tables, built with a normal writable connection."""
    path = tmp_path / "sample.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE orders (id INTEGER, customer_id INTEGER, amount DOUBLE)")
        con.execute("INSERT INTO orders SELECT i, i % 10, i * 1.5 FROM range(1, 101) t(i)")
        con.execute("CREATE TABLE customers (id INTEGER, name VARCHAR)")
        con.execute("INSERT INTO customers SELECT i, 'c' || i FROM range(10) t(i)")
    return path


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    from schemalab.store import make_engine

    eng = make_engine(f"sqlite:///{tmp_path / 'app.db'}")
    yield eng
    eng.dispose()


@pytest.fixture
def app(engine: Engine, sample_db: Path) -> FastAPI:
    from schemalab.api.main import create_app, get_db_path, get_engine

    application = create_app()
    application.dependency_overrides[get_engine] = lambda: engine
    application.dependency_overrides[get_db_path] = lambda: sample_db
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def register(client: TestClient, email: str = "ada@umich.edu") -> dict[str, str]:
    """Register a user and return the auth header for them."""
    resp = client.post("/auth/register", json={"email": email, "password": "correct horse battery"})
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}
