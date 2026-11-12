"""POST /schedules and POST /recovery."""

from fastapi.testclient import TestClient

from tests.conftest import register


def test_need_sign_in(client: TestClient) -> None:
    assert client.post("/schedules", json={"schedule": "r1(A) c1"}).status_code == 401
    assert client.post("/recovery", json={"log": "T1 write A 1 2"}).status_code == 401


def test_schedule_analysis(client: TestClient) -> None:
    got = client.post(
        "/schedules", json={"schedule": "r1(A) r2(A) w1(A) w2(A) c1 c2"}, headers=register(client)
    ).json()
    assert got["conflict_serializable"] is False and got["cycle"] == [1, 2, 1]


def test_schedule_errors_are_400(client: TestClient) -> None:
    resp = client.post("/schedules", json={"schedule": "z1(A)"}, headers=register(client))
    assert resp.status_code == 400 and "position 1" in resp.json()["detail"]


def test_recovery_simulation(client: TestClient) -> None:
    got = client.post(
        "/recovery",
        json={"log": "T1 write A 1 2\nT2 write B 3 4\nT1 commit\ncrash"},
        headers=register(client),
    ).json()
    assert got["analysis"]["losers"] == [2]
    assert got["final"] == {"A": "2", "B": "3"}


def test_recovery_errors_are_400(client: TestClient) -> None:
    resp = client.post("/recovery", json={"log": "T1 dance"}, headers=register(client))
    assert resp.status_code == 400 and resp.json()["detail"].startswith("line 1:")


def test_inputs_are_size_limited(client: TestClient) -> None:
    resp = client.post("/schedules", json={"schedule": "r1(A) " * 2000}, headers=register(client))
    assert resp.status_code == 422
