"""Two real PostgreSQL sessions per user, in a sandbox database. Needs LAB_TEST_DSN."""

import os
import time
from collections.abc import Iterator

import pytest

from schemalab.lab.session import Lab

DSN = os.environ.get("LAB_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="set LAB_TEST_DSN to a Postgres superuser DSN")


@pytest.fixture
def lab() -> Iterator[Lab]:
    lab = Lab(DSN, user_id=901)
    lab.start()
    yield lab
    lab.close()


def until_done(lab: Lab, session: str, seconds: float = 5.0) -> dict[str, object]:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        outcome = lab.result(session)
        if outcome and outcome["state"] != "waiting":
            return outcome
        time.sleep(0.05)
    raise AssertionError(f"session {session} still waiting")


def setup(lab: Lab) -> None:
    lab.run("A", "CREATE TABLE accounts (id int PRIMARY KEY, balance int)")
    lab.run("A", "INSERT INTO accounts VALUES (1, 100), (2, 100)")


def test_a_statement_runs_and_returns_rows(lab: Lab) -> None:
    setup(lab)
    out = lab.run("B", "SELECT id, balance FROM accounts ORDER BY id")
    assert out["state"] == "done" and out["status"] == "SELECT 2"
    assert out["columns"] == ["id", "balance"] and out["rows"] == [[1, 100], [2, 100]]


def test_the_session_timeouts_are_fixed_for_the_role(lab: Lab) -> None:
    assert lab.run("A", "SHOW statement_timeout")["rows"] == [["30s"]]
    assert lab.run("A", "SHOW idle_in_transaction_session_timeout")["rows"] == [["5min"]]


def test_the_lab_role_cannot_read_server_files(lab: Lab) -> None:
    out = lab.run("A", "SELECT pg_read_file('/etc/passwd')")
    assert out["state"] == "error" and "permission denied" in str(out["error"])


def test_one_users_tables_are_invisible_to_another() -> None:
    first, second = Lab(DSN, user_id=902), Lab(DSN, user_id=903)
    first.start()
    second.start()
    try:
        first.run("A", "CREATE TABLE secret (x int)")
        out = second.run("A", "SELECT * FROM lab_u902.secret")
        assert out["state"] == "error" and "permission denied" in str(out["error"])
    finally:
        first.close()
        second.close()


def test_a_blocked_update_waits_then_finishes_after_commit(lab: Lab) -> None:
    setup(lab)
    lab.run("A", "BEGIN")
    lab.run("A", "UPDATE accounts SET balance = balance - 10 WHERE id = 1")
    waiting = lab.run("B", "UPDATE accounts SET balance = balance - 20 WHERE id = 1", wait_s=0.5)
    assert waiting["state"] == "waiting"
    state = lab.state()
    assert state["sessions"]["B"]["blocked_by"] == ["A"]
    assert any(lock["session"] == "B" and not lock["granted"] for lock in state["locks"])
    lab.run("A", "COMMIT")
    assert until_done(lab, "B")["status"] == "UPDATE 1"
    assert lab.run("A", "SELECT balance FROM accounts WHERE id = 1")["rows"] == [[70]]


def test_a_deadlock_is_detected(lab: Lab) -> None:
    setup(lab)
    lab.run("A", "BEGIN")
    lab.run("B", "BEGIN")
    lab.run("A", "UPDATE accounts SET balance = 1 WHERE id = 1")
    lab.run("B", "UPDATE accounts SET balance = 2 WHERE id = 2")
    lab.run("A", "UPDATE accounts SET balance = 1 WHERE id = 2", wait_s=0.3)
    second = lab.run("B", "UPDATE accounts SET balance = 2 WHERE id = 1", wait_s=3)
    first = until_done(lab, "A")
    errors = [o for o in (first, second) if o["state"] == "error"]
    assert len(errors) == 1 and errors[0]["code"] == "40P01"  # deadlock_detected


def test_a_failed_statement_leaves_the_transaction_failed(lab: Lab) -> None:
    lab.run("A", "BEGIN")
    assert lab.run("A", "SELECT 1/0")["state"] == "error"
    assert lab.state()["sessions"]["A"]["transaction"] == "failed"
    lab.run("A", "ROLLBACK")
    assert lab.state()["sessions"]["A"]["transaction"] == "idle"


def test_row_versions_show_who_created_and_who_is_changing_a_row(lab: Lab) -> None:
    setup(lab)
    lab.run("A", "BEGIN")
    lab.run("A", "UPDATE accounts SET balance = 0 WHERE id = 1")
    [table] = lab.state()["versions"]
    assert table["table"] == "accounts"
    row = next(r for r in table["rows"] if r["values"][0] == 1)
    assert int(row["xmax"]) != 0  # A's transaction is updating it
    lab.run("A", "ROLLBACK")


def test_a_second_statement_while_waiting_is_refused(lab: Lab) -> None:
    setup(lab)
    lab.run("A", "BEGIN")
    lab.run("A", "UPDATE accounts SET balance = 0 WHERE id = 1")
    lab.run("B", "UPDATE accounts SET balance = 5 WHERE id = 1", wait_s=0.2)
    with pytest.raises(RuntimeError, match="still waiting"):
        lab.run("B", "SELECT 1")
    lab.run("A", "ROLLBACK")
    until_done(lab, "B")


def test_reset_drops_the_users_tables(lab: Lab) -> None:
    setup(lab)
    lab.reset()
    out = lab.run("A", "SELECT * FROM accounts")
    assert out["state"] == "error" and "does not exist" in str(out["error"])
