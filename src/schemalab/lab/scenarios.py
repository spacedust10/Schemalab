"""Guided scenarios for the transaction lab.

Each step names the session that runs it and what should happen. `expect` is checked when the
step runs; `then` is checked when a step that had to wait finally finishes. The integration
tests play every scenario against a real PostgreSQL and check each expectation, so a scenario
cannot drift from what it claims to teach.
"""

from dataclasses import dataclass, field
from typing import Any

ACCOUNTS = [
    "CREATE TABLE accounts (id int PRIMARY KEY, owner text, balance int)",
    "INSERT INTO accounts VALUES (1, 'Ada', 100), (2, 'Bob', 100)",
]
DOCTORS = [
    "CREATE TABLE doctors (name text PRIMARY KEY, on_call boolean)",
    "INSERT INTO doctors VALUES ('Alice', true), ('Bob', true)",
]


@dataclass(frozen=True)
class Step:
    session: str
    sql: str
    note: str
    expect: dict[str, Any] = field(default_factory=dict)
    then: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    summary: str
    setup: list[str]
    steps: list[Step]
    lesson: str


def _rows(*values: Any) -> dict[str, Any]:
    return {"rows": [[v] for v in values]}


SCENARIOS = [
    Scenario(
        "non-repeatable-read",
        "Non-repeatable read",
        "A reads the same row twice inside one transaction while B changes it.",
        ACCOUNTS,
        [
            Step(
                "A",
                "BEGIN ISOLATION LEVEL READ COMMITTED",
                "A starts at READ COMMITTED, Postgres's default.",
            ),
            Step(
                "A",
                "SELECT balance FROM accounts WHERE id = 1",
                "A reads Ada's balance.",
                _rows(100),
            ),
            Step(
                "B",
                "UPDATE accounts SET balance = 50 WHERE id = 1",
                "B changes it and commits at once (no BEGIN).",
                {"status": "UPDATE 1"},
            ),
            Step(
                "A",
                "SELECT balance FROM accounts WHERE id = 1",
                "A reads again, in the same transaction.",
                _rows(50),
            ),
            Step("A", "COMMIT", "A finishes."),
        ],
        "At READ COMMITTED each statement sees everything committed before it started, so one "
        "transaction can read the same row twice and get two answers.",
    ),
    Scenario(
        "repeatable-read",
        "Repeatable read keeps a snapshot",
        "The same steps at REPEATABLE READ.",
        ACCOUNTS,
        [
            Step("A", "BEGIN ISOLATION LEVEL REPEATABLE READ", "A starts at REPEATABLE READ."),
            Step(
                "A",
                "SELECT balance FROM accounts WHERE id = 1",
                "A's first read fixes its snapshot.",
                _rows(100),
            ),
            Step(
                "B",
                "UPDATE accounts SET balance = 50 WHERE id = 1",
                "B changes the row and commits.",
                {"status": "UPDATE 1"},
            ),
            Step(
                "A",
                "SELECT balance FROM accounts WHERE id = 1",
                "A still sees its snapshot.",
                _rows(100),
            ),
            Step("A", "COMMIT", "A finishes."),
            Step(
                "A",
                "SELECT balance FROM accounts WHERE id = 1",
                "A new statement sees B's change.",
                _rows(50),
            ),
        ],
        "REPEATABLE READ takes one snapshot at the transaction's first statement. Look at the row "
        "versions: B's update made a new version with a new xmin; A keeps reading the old one.",
    ),
    Scenario(
        "phantom-read",
        "Phantom read",
        "A counts rows twice while B inserts a matching one.",
        ACCOUNTS,
        [
            Step("A", "BEGIN ISOLATION LEVEL READ COMMITTED", "A starts at READ COMMITTED."),
            Step(
                "A",
                "SELECT count(*) FROM accounts WHERE balance > 50",
                "A counts rich accounts.",
                _rows(2),
            ),
            Step(
                "B",
                "INSERT INTO accounts VALUES (3, 'Cy', 80)",
                "B inserts a new one and commits.",
                {"status": "INSERT 0 1"},
            ),
            Step(
                "A",
                "SELECT count(*) FROM accounts WHERE balance > 50",
                "A counts again: a phantom row appears.",
                _rows(3),
            ),
            Step("A", "COMMIT", "A finishes."),
        ],
        "A phantom is a row that starts matching a query mid-transaction. In Postgres, REPEATABLE "
        "READ prevents phantoms as well, since the whole snapshot is fixed.",
    ),
    Scenario(
        "lost-update",
        "Lost update",
        "Two transactions read a balance, add to it in the application, and write it back.",
        ACCOUNTS,
        [
            Step("A", "BEGIN", "A starts."),
            Step("B", "BEGIN", "B starts."),
            Step(
                "A",
                "SELECT balance FROM accounts WHERE id = 1",
                "A reads 100 and plans to add 10.",
                _rows(100),
            ),
            Step(
                "B",
                "SELECT balance FROM accounts WHERE id = 1",
                "B reads 100 and plans to add 20.",
                _rows(100),
            ),
            Step(
                "A",
                "UPDATE accounts SET balance = 110 WHERE id = 1",
                "A writes 100 + 10.",
                {"status": "UPDATE 1"},
            ),
            Step(
                "B",
                "UPDATE accounts SET balance = 120 WHERE id = 1",
                "B writes 100 + 20 and has to wait: A holds the row lock.",
                {"state": "waiting"},
                {"status": "UPDATE 1"},
            ),
            Step("A", "COMMIT", "A commits; B's update now goes through."),
            Step("B", "COMMIT", "B commits."),
            Step(
                "A",
                "SELECT balance FROM accounts WHERE id = 1",
                "Both deposits ran, but the balance shows only B's.",
                _rows(120),
            ),
        ],
        "Each transaction computed from a value that was stale by the time it wrote. Locking "
        "rows does not help when the read happened before the lock; the next two scenarios fix it.",
    ),
    Scenario(
        "select-for-update",
        "Fixing it with SELECT ... FOR UPDATE",
        "The same deposits, locking the row when reading it.",
        ACCOUNTS,
        [
            Step("A", "BEGIN", "A starts."),
            Step(
                "A",
                "SELECT balance FROM accounts WHERE id = 1 FOR UPDATE",
                "A reads and locks the row.",
                _rows(100),
            ),
            Step("B", "BEGIN", "B starts."),
            Step(
                "B",
                "SELECT balance FROM accounts WHERE id = 1 FOR UPDATE",
                "B wants the same row and waits.",
                {"state": "waiting"},
                _rows(110),
            ),
            Step(
                "A",
                "UPDATE accounts SET balance = 110 WHERE id = 1",
                "A writes 100 + 10.",
                {"status": "UPDATE 1"},
            ),
            Step("A", "COMMIT", "A commits; B's read now returns 110, not 100."),
            Step(
                "B",
                "UPDATE accounts SET balance = 130 WHERE id = 1",
                "B writes 110 + 20.",
                {"status": "UPDATE 1"},
            ),
            Step("B", "COMMIT", "B commits."),
            Step(
                "A", "SELECT balance FROM accounts WHERE id = 1", "Both deposits count.", _rows(130)
            ),
        ],
        "FOR UPDATE takes the row lock at read time, so the second reader waits and then sees the "
        "committed value. Watch the lock table: B's lock request is not granted until A commits.",
    ),
    Scenario(
        "serialization-failure",
        "REPEATABLE READ refuses the stale write",
        "The lost-update steps at REPEATABLE READ.",
        ACCOUNTS,
        [
            Step("A", "BEGIN ISOLATION LEVEL REPEATABLE READ", "A starts at REPEATABLE READ."),
            Step("B", "BEGIN ISOLATION LEVEL REPEATABLE READ", "B starts at REPEATABLE READ."),
            Step("A", "SELECT balance FROM accounts WHERE id = 1", "A reads 100.", _rows(100)),
            Step("B", "SELECT balance FROM accounts WHERE id = 1", "B reads 100.", _rows(100)),
            Step(
                "A",
                "UPDATE accounts SET balance = 110 WHERE id = 1",
                "A writes 110.",
                {"status": "UPDATE 1"},
            ),
            Step(
                "B",
                "UPDATE accounts SET balance = 120 WHERE id = 1",
                "B writes 120 and waits for A.",
                {"state": "waiting"},
                {"code": "40001"},
            ),
            Step("A", "COMMIT", "A commits; B's update fails: its snapshot is out of date."),
            Step("B", "ROLLBACK", "B must roll back and retry."),
            Step(
                "A",
                "SELECT balance FROM accounts WHERE id = 1",
                "Only A's deposit happened; B can retry.",
                _rows(110),
            ),
        ],
        "At REPEATABLE READ Postgres will not let a transaction overwrite a row that changed "
        "after its snapshot. It raises a serialization failure (SQLSTATE 40001) and the "
        "application retries the whole transaction.",
    ),
    Scenario(
        "write-skew",
        "Write skew",
        "Two doctors each check that someone else is on call, then both go off call.",
        DOCTORS,
        [
            Step("A", "BEGIN ISOLATION LEVEL REPEATABLE READ", "Alice's transaction."),
            Step("B", "BEGIN ISOLATION LEVEL REPEATABLE READ", "Bob's transaction."),
            Step(
                "A",
                "SELECT count(*) FROM doctors WHERE on_call",
                "Alice sees two doctors on call.",
                _rows(2),
            ),
            Step(
                "B", "SELECT count(*) FROM doctors WHERE on_call", "Bob sees two as well.", _rows(2)
            ),
            Step(
                "A",
                "UPDATE doctors SET on_call = false WHERE name = 'Alice'",
                "Alice goes off call.",
                {"status": "UPDATE 1"},
            ),
            Step(
                "B",
                "UPDATE doctors SET on_call = false WHERE name = 'Bob'",
                "Bob goes off call: a different row, so no wait.",
                {"status": "UPDATE 1"},
            ),
            Step("A", "COMMIT", "Alice commits.", {"state": "done"}),
            Step("B", "COMMIT", "Bob commits too.", {"state": "done"}),
            Step("A", "SELECT count(*) FROM doctors WHERE on_call", "Nobody is on call.", _rows(0)),
        ],
        "Each transaction kept the rule (at least one doctor on call) given its snapshot, but "
        "together they broke it. The rows written were different, so neither waited or failed.",
    ),
    Scenario(
        "serializable",
        "SERIALIZABLE catches write skew",
        "The doctors again, at SERIALIZABLE.",
        DOCTORS,
        [
            Step("A", "BEGIN ISOLATION LEVEL SERIALIZABLE", "Alice's transaction."),
            Step("B", "BEGIN ISOLATION LEVEL SERIALIZABLE", "Bob's transaction."),
            Step("A", "SELECT count(*) FROM doctors WHERE on_call", "Alice sees two.", _rows(2)),
            Step("B", "SELECT count(*) FROM doctors WHERE on_call", "Bob sees two.", _rows(2)),
            Step(
                "A",
                "UPDATE doctors SET on_call = false WHERE name = 'Alice'",
                "Alice goes off call.",
                {"status": "UPDATE 1"},
            ),
            Step(
                "B",
                "UPDATE doctors SET on_call = false WHERE name = 'Bob'",
                "Bob goes off call.",
                {"status": "UPDATE 1"},
            ),
            Step("A", "COMMIT", "Alice commits first.", {"state": "done"}),
            Step("B", "COMMIT", "Bob's commit is refused.", {"code": "40001"}),
            Step(
                "A", "SELECT count(*) FROM doctors WHERE on_call", "Bob is still on call.", _rows(1)
            ),
        ],
        "SERIALIZABLE tracks what each transaction read. When two transactions read data the "
        "other then changed, one of them must fail, so the result matches some serial order.",
    ),
    Scenario(
        "deadlock",
        "Deadlock",
        "Two transfers lock the same two accounts in opposite order.",
        ACCOUNTS,
        [
            Step("A", "BEGIN", "Transfer from Ada to Bob."),
            Step("B", "BEGIN", "Transfer from Bob to Ada."),
            Step(
                "A",
                "UPDATE accounts SET balance = balance - 10 WHERE id = 1",
                "A locks Ada's row.",
                {"status": "UPDATE 1"},
            ),
            Step(
                "B",
                "UPDATE accounts SET balance = balance - 10 WHERE id = 2",
                "B locks Bob's row.",
                {"status": "UPDATE 1"},
            ),
            Step(
                "A",
                "UPDATE accounts SET balance = balance + 10 WHERE id = 2",
                "A needs Bob's row and waits for B.",
                {"state": "waiting"},
            ),
            Step(
                "B",
                "UPDATE accounts SET balance = balance + 10 WHERE id = 1",
                "B needs Ada's row: each waits for the other. After about a second Postgres "
                "breaks the cycle by failing one of them (SQLSTATE 40P01).",
            ),
            Step("A", "ROLLBACK", "Roll both back to finish."),
            Step("B", "ROLLBACK", ""),
        ],
        "Postgres checks for a cycle in the waits-for graph after deadlock_timeout and aborts one "
        "transaction. Locking rows in the same order everywhere (by id, say) prevents deadlocks.",
    ),
]

BY_ID = {s.id: s for s in SCENARIOS}
