from fastapi.testclient import TestClient

from schemalab.api.main import app


def test_health_reports_ok_and_version() -> None:
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert body["version"]
