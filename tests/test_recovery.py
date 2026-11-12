"""ARIES recovery: analysis, redo and undo over a write-ahead log."""

import re

import pytest

from schemalab.recovery import RecoveryError, simulate

LOG = """
T1 begin
T1 write A 10 20
T2 begin
T2 write B 5 7
checkpoint
T1 commit
T3 begin
T3 write A 20 30
flush A
T2 write C 1 2
crash
"""


def test_log_gets_lsns_and_prev_lsn_chains() -> None:
    log = simulate(LOG)["log"]
    updates = [r for r in log if r["kind"] == "update"]
    assert [(r["lsn"], r["txn"], r["page"], r["prev"]) for r in updates] == [
        (10, 1, "A", None),
        (20, 2, "B", None),
        (60, 3, "A", None),
        (70, 2, "C", 20),
    ]
    assert [r["kind"] for r in log if r["lsn"] in (30, 40, 50)] == ["checkpoint", "commit", "end"]


def test_disk_at_crash_has_only_flushed_pages() -> None:
    disk = simulate(LOG)["at_crash"]
    assert disk["A"] == {"value": "30", "page_lsn": 60}  # flushed after T3's write
    assert disk["B"] == {"value": "5", "page_lsn": 0}
    assert disk["C"] == {"value": "1", "page_lsn": 0}


def test_analysis_starts_at_the_checkpoint() -> None:
    analysis = simulate(LOG)["analysis"]
    assert analysis["start_lsn"] == 30
    assert analysis["transactions"] == [
        {"txn": 2, "status": "running", "last_lsn": 70},
        {"txn": 3, "status": "running", "last_lsn": 60},
    ]
    # A's flush is not logged, so analysis still thinks A is dirty from LSN 10.
    assert analysis["dirty_pages"] == [
        {"page": "A", "rec_lsn": 10},
        {"page": "B", "rec_lsn": 20},
        {"page": "C", "rec_lsn": 70},
    ]
    assert analysis["losers"] == [2, 3]


def test_redo_repeats_history_but_skips_what_the_disk_has() -> None:
    redo = simulate(LOG)["redo"]
    assert redo["start_lsn"] == 10
    decisions = {s["lsn"]: (s["action"], s["reason"]) for s in redo["steps"]}
    assert decisions[10] == ("skip", "the page on disk already has LSN 60 >= 10")
    assert decisions[20][0] == "redo"
    assert decisions[60] == ("skip", "the page on disk already has LSN 60 >= 60")
    assert decisions[70][0] == "redo"


def test_undo_rolls_back_losers_newest_first_with_clrs() -> None:
    undo = simulate(LOG)["undo"]
    clrs = [s for s in undo if s["kind"] == "CLR"]
    assert [(c["lsn"], c["txn"], c["page"], c["value"], c["undo_next"]) for c in clrs] == [
        (80, 2, "C", "1", 20),
        (90, 3, "A", "20", None),
        (110, 2, "B", "5", None),
    ]
    ends = [(s["lsn"], s["txn"]) for s in undo if s["kind"] == "end"]
    assert ends == [(100, 3), (120, 2)]


def test_after_recovery_only_committed_work_remains() -> None:
    assert simulate(LOG)["final"] == {"A": "20", "B": "5", "C": "1"}


def test_an_abort_before_the_crash_is_redone_through_its_clr() -> None:
    result = simulate("T1 write A 1 2\nT1 abort\ncrash")
    kinds = [(r["lsn"], r["kind"]) for r in result["log"]]
    assert kinds == [(10, "update"), (20, "abort"), (30, "CLR"), (40, "end")]
    assert result["analysis"]["losers"] == []
    assert [s["action"] for s in result["redo"]["steps"]] == ["redo", "redo"]
    assert result["final"] == {"A": "1"}


def test_a_committed_transaction_without_an_end_gets_one() -> None:
    result = simulate("T1 write A 1 2\nT1 commit\ncrash")
    assert result["analysis"]["losers"] == []
    assert result["final"] == {"A": "2"}


def test_no_crash_means_nothing_to_recover() -> None:
    result = simulate("T1 write A 1 2\nT1 commit")
    assert result["crashed"] is False and result["final"] == {"A": "2"}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("T1 write A 1", "line 1: write a change as T1 write A <old> <new>"),
        ("T1 frobnicate", "line 1: T1 frobnicate is not a log action"),
        ("T1 commit\nT1 write A 1 2", "line 2: T1 already ended"),
        ("crash\nT1 write A 1 2", "line 2: nothing can happen after the crash"),
        ("flush Z", "line 1: Z was never written"),
        ("", "Write a log"),
    ],
)
def test_errors_name_the_line(text: str, message: str) -> None:
    with pytest.raises(RecoveryError, match=re.escape(message)):
        simulate(text)


def test_recovery_keeps_exactly_the_committed_writes_on_random_logs() -> None:
    import random

    rng = random.Random(7)  # noqa: S311 - test data
    for _ in range(300):
        value = {"A": "0", "B": "0", "C": "0"}
        owner: dict[str, int] = {}  # page -> open writer (strict locking: no dirty writes)
        open_txns: set[int] = set()
        done: set[int] = set()
        committed_value = dict(value)
        pending: dict[int, dict[str, str]] = {}
        lines: list[str] = []
        next_txn, counter = 1, 0
        for _ in range(rng.randint(4, 14)):
            choice = rng.random()
            if choice < 0.5:
                if not open_txns or (rng.random() < 0.3 and next_txn < 6):
                    open_txns.add(next_txn)
                    next_txn += 1
                t = rng.choice(sorted(open_txns))
                free = [p for p in "ABC" if owner.get(p) in (None, t)]
                if not free:
                    continue
                page = rng.choice(free)
                counter += 1
                lines.append(f"T{t} write {page} {value[page]} v{counter}")
                value[page] = f"v{counter}"
                owner[page] = t
                pending.setdefault(t, {})[page] = f"v{counter}"
            elif choice < 0.7 and open_txns:
                t = rng.choice(sorted(open_txns))
                lines.append(f"T{t} commit")
                committed_value.update(pending.pop(t, {}))
                open_txns.discard(t)
                done.add(t)
                owner = {p: o for p, o in owner.items() if o != t}
            elif choice < 0.8 and open_txns:
                t = rng.choice(sorted(open_txns))
                lines.append(f"T{t} abort")
                for page in pending.pop(t, {}):
                    value[page] = committed_value[page]
                open_txns.discard(t)
                done.add(t)
                owner = {p: o for p, o in owner.items() if o != t}
            elif choice < 0.9:
                lines.append("checkpoint")
            else:
                written = [p for p in "ABC" if any(f" {p} " in line for line in lines)]
                if written:
                    lines.append(f"flush {rng.choice(written)}")
        lines.append("crash")
        text = "\n".join(lines)
        final = simulate(text)["final"]
        touched = {p for p in "ABC" if any(f" write {p} " in line for line in lines)}
        assert final == {p: committed_value[p] for p in sorted(touched)}, text
