"""POST /normalize: the engine's full analysis as JSON, in schema order."""

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import register

BODY = {"schema": "R(A, B, C)", "dependencies": "A -> B\nB -> C"}


def analyze(client: TestClient, body: dict[str, Any] = BODY) -> Any:
    return client.post("/normalize", json=body, headers=register(client))


def test_needs_sign_in(client: TestClient) -> None:
    assert client.post("/normalize", json=BODY).status_code == 401


def test_full_analysis(client: TestClient) -> None:
    resp = analyze(client)
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert got["attributes"] == ["A", "B", "C"]
    assert got["keys"] == [["A"]] and got["prime"] == ["A"]
    assert got["form"] == "2NF"
    assert got["violations"] == [
        {
            "form": "3NF",
            "lhs": ["B"],
            "rhs": ["C"],
            "reason": "B -> C: B is not a superkey and C is not part of any key.",
        }
    ]
    assert got["cover"] == [{"lhs": ["A"], "rhs": ["B"]}, {"lhs": ["B"], "rhs": ["C"]}]
    bcnf = got["bcnf"]
    assert [r["attrs"] for r in bcnf["relations"]] == [["B", "C"], ["A", "B"]]
    assert bcnf["relations"][0]["keys"] == [["B"]] and bcnf["relations"][0]["form"] == "BCNF"
    assert bcnf["steps"] == [
        {"relation": ["A", "B", "C"], "lhs": ["B"], "rhs": ["C"], "split": [["B", "C"], ["A", "B"]]}
    ]
    assert bcnf["lossless"] is True and bcnf["lost"] == []
    assert bcnf["chase_table"][0] == ["b11", "a2", "a3"]
    assert [r["attrs"] for r in got["third"]["relations"]] == [["A", "B"], ["B", "C"]]


def test_closure_of_a_set(client: TestClient) -> None:
    got = analyze(client, {**BODY, "closure_of": "B"}).json()
    assert got["closure"] == {"of": ["B"], "is": ["B", "C"], "superkey": False}


def test_unreadable_input_says_what_to_fix(client: TestClient) -> None:
    resp = analyze(client, {"schema": "R(A, B)", "dependencies": "A -> Z"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "line 1: Z is not an attribute of R."


def test_closure_of_an_unknown_attribute_is_refused(client: TestClient) -> None:
    resp = analyze(client, {**BODY, "closure_of": "Q"})
    assert resp.status_code == 400 and "Q" in resp.json()["detail"]


def test_cover_is_shown_merged_by_left_side_in_schema_order(client: TestClient) -> None:
    body = {"schema": "E(sid, name, major, course)", "dependencies": "sid -> major, name"}
    got = analyze(client, body).json()
    assert got["cover"] == [{"lhs": ["sid"], "rhs": ["name", "major"]}]


def test_a_case_too_costly_to_decompose_is_a_400(client: TestClient) -> None:
    names = ", ".join(f"A{i}" for i in range(16))
    deps = "\n".join(f"A{i}, A{(i + 5) % 16} -> A{(i + 9) % 16}" for i in range(16))
    resp = client.post(
        "/normalize", json={"schema": f"R({names})", "dependencies": deps}, headers=register(client)
    )
    assert resp.status_code == 400 and "too many" in resp.json()["detail"]
