"""/assignments: build, share, take and grade SQL assignments. Hidden data stays hidden."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import register

SECRET = "Zzyzx"  # a name that only the hidden test contains

NEW: dict[str, Any] = {
    "title": "Customers without an email",
    "prompt": "How many customers have no email? One column: no_email.",
    "schema": "CREATE TABLE customers (id INT PRIMARY KEY, name TEXT, email TEXT);",
    "data": "INSERT INTO customers VALUES (1, 'Ada', NULL), (2, 'Alan', 'a@x.org');",
    "reference": "SELECT count(*) AS no_email FROM customers WHERE email IS NULL",
    "hidden": [
        {
            "name": "Everyone is missing an email",
            "data": f"INSERT INTO customers VALUES (1, '{SECRET}', NULL), (2, 'Bo', NULL);",
        }
    ],
    "mode": "exam",
    "max_attempts": 2,
}
RIGHT = NEW["reference"]
WRONG = "SELECT count(*) AS no_email FROM customers WHERE id = 1"


def test_assignments_need_sign_in(client: TestClient) -> None:
    assert client.get("/assignments").status_code == 401
    assert (
        client.post("/assignments/sample:top-paid/submit", json={"sql": "SELECT 1"}).status_code
        == 401
    )


def test_the_samples_are_listed_without_answers(client: TestClient) -> None:
    got = client.get("/assignments", headers=register(client)).json()
    ids = [a["id"] for a in got]
    assert {"sample:courses-per-student", "sample:top-paid", "sample:missing-emails"} <= set(ids)
    assert all("reference" not in a and "hidden" not in a for a in got)


def test_a_student_sees_the_prompt_schema_and_sample_rows_only(client: TestClient) -> None:
    got = client.get("/assignments/sample:courses-per-student", headers=register(client)).json()
    assert got["prompt"] and "CREATE TABLE students" in got["schema"]
    assert got["tables"]["students"]["rows"] == [[1, "Ada"], [2, "Alan"]]
    assert "reference" not in got and "hidden" not in got
    assert got["tests"] == [
        "Sample data",
        "A student with no courses",
        "Two students with the same name",
    ]


def test_submitting_to_a_sample_grades_in_tutor_mode(client: TestClient) -> None:
    headers = register(client)
    got = client.post(
        "/assignments/sample:courses-per-student/submit",
        json={
            "sql": "SELECT s.name, count(*) AS courses FROM students s JOIN grades g "
            "ON g.student_id = s.id GROUP BY s.id, s.name"
        },
        headers=headers,
    ).json()
    assert got["score"] == 77 and any("hint" in t for t in got["tests"])


def test_a_broken_assignment_is_refused_with_its_reasons(client: TestClient) -> None:
    bad = {**NEW, "reference": "SELECT nme FROM customers"}
    resp = client.post("/assignments", json=bad, headers=register(client))
    assert resp.status_code == 400
    assert any("reference query fails" in e for e in resp.json()["detail"]["errors"])


def test_check_previews_without_saving(client: TestClient) -> None:
    headers = register(client)
    got = client.post("/assignments/check", json={**NEW, "hidden": []}, headers=headers).json()
    assert got["errors"] == [] and any("hidden" in w for w in got["warnings"])
    assert all(
        a["id"].startswith("sample:") for a in client.get("/assignments", headers=headers).json()
    )


def test_an_exam_hides_answers_limits_attempts_and_keeps_the_best_score(client: TestClient) -> None:
    teacher = register(client, "teacher@umich.edu")
    made = client.post("/assignments", json=NEW, headers=teacher)
    assert made.status_code == 201
    aid = made.json()["id"]
    assert len(aid) >= 12  # not guessable from other assignments' ids

    student = register(client, "student@umich.edu")
    seen = client.get(f"/assignments/{aid}", headers=student).json()
    assert "reference" not in seen and SECRET not in str(seen)
    assert seen["mode"] == "exam" and seen["attempts_left"] == 2

    first = client.post(f"/assignments/{aid}/submit", json={"sql": WRONG}, headers=student).json()
    assert first["score"] < 100 and SECRET not in str(first)
    assert first["attempts_left"] == 1
    second = client.post(f"/assignments/{aid}/submit", json={"sql": RIGHT}, headers=student).json()
    assert second["score"] == 100 and second["best"] == 100 and second["attempts_left"] == 0
    third = client.post(f"/assignments/{aid}/submit", json={"sql": RIGHT}, headers=student)
    assert third.status_code == 409 and "attempts" in third.json()["detail"]

    owner_view = client.get(f"/assignments/{aid}", headers=teacher).json()
    assert owner_view["reference"] == RIGHT and owner_view["hidden"][0]["data"].count(SECRET) == 1


def test_an_unknown_assignment_is_404(client: TestClient) -> None:
    assert client.get("/assignments/nope", headers=register(client)).status_code == 404


def test_sample_rows_quote_odd_table_names_safely(tmp_path: Any) -> None:
    import duckdb

    from schemalab.api.assignments import _table_rows

    path = tmp_path / "odd.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute('CREATE TABLE "a""b; DROP TABLE x; --" (v INT)')
        con.execute('INSERT INTO "a""b; DROP TABLE x; --" VALUES (1)')
        con.execute("CREATE TABLE x (v INT)")
    got = _table_rows(path)
    assert got['a"b; DROP TABLE x; --']["rows"] == [[1]] and "x" in got


def test_exam_attempts_cannot_be_raced_past_the_limit(tmp_path: Any) -> None:
    import threading

    from schemalab.api.assignments import reserve_attempt
    from schemalab.store import create_user, make_engine

    engine = make_engine(f"sqlite:///{tmp_path / 'race.db'}")
    with engine.begin() as conn:
        uid = create_user(conn, "racer@umich.edu", "correct horse battery")
    wins: list[bool] = []
    start = threading.Barrier(6)

    def go() -> None:
        start.wait()
        wins.append(reserve_attempt(engine, "exam1", uid, limit=2) is not None)

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(wins) == [False] * 4 + [True] * 2


def test_attempts_made_before_slots_existed_still_count(tmp_path: Any) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import insert

    from schemalab.api.assignments import reserve_attempt
    from schemalab.store import create_user, make_engine, submissions

    engine = make_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        uid = create_user(conn, "early@umich.edu", "correct horse battery")
        for _ in range(2):  # recorded before attempt slots were claimed
            conn.execute(
                insert(submissions).values(
                    assignment_id="exam1",
                    user_id=uid,
                    sql="SELECT 1",
                    score=0,
                    created_at=datetime.now(UTC),
                )
            )
    assert reserve_attempt(engine, "exam1", uid, limit=2) is None
    assert reserve_attempt(engine, "exam1", uid, limit=3) is not None


def test_a_slot_without_a_submission_does_not_lock_the_student_out(tmp_path: Any) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import insert

    from schemalab.api.assignments import reserve_attempt
    from schemalab.store import attempt_slots, create_user, make_engine, submissions

    engine = make_engine(f"sqlite:///{tmp_path / 'gap.db'}")
    with engine.begin() as conn:
        uid = create_user(conn, "gap@umich.edu", "correct horse battery")
        for _ in range(2):
            conn.execute(
                insert(submissions).values(
                    assignment_id="exam1",
                    user_id=uid,
                    sql="SELECT 1",
                    score=0,
                    created_at=datetime.now(UTC),
                )
            )
        # A slot whose grading never recorded a submission.
        conn.execute(insert(attempt_slots).values(assignment_id="exam1", user_id=uid, attempt_no=3))
    assert reserve_attempt(engine, "exam1", uid, limit=5) == 4


def test_only_a_failure_on_our_side_gives_the_attempt_back(
    client: TestClient, monkeypatch: Any
) -> None:
    from schemalab import assignments as asg

    teacher = register(client, "t2@umich.edu")
    aid = client.post("/assignments", json={**NEW, "max_attempts": 1}, headers=teacher).json()["id"]
    student = register(client, "s2@umich.edu")

    def unavailable(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise asg.GraderUnavailable("the test databases could not be built")

    monkeypatch.setattr(asg, "grade", unavailable)
    with pytest.raises(asg.GraderUnavailable):  # the test client re-raises it
        client.post(f"/assignments/{aid}/submit", json={"sql": RIGHT}, headers=student)

    def crashed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("something the submission set off")

    monkeypatch.setattr(asg, "grade", crashed)
    with pytest.raises(RuntimeError):
        client.post(f"/assignments/{aid}/submit", json={"sql": RIGHT}, headers=student)
    monkeypatch.undo()
    # The first failure gave the attempt back; the second did not, so none is left.
    resp = client.post(f"/assignments/{aid}/submit", json={"sql": RIGHT}, headers=student)
    assert resp.status_code == 409
