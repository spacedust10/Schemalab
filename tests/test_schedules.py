"""Schedule checker: serializability, recoverability and two-phase locking."""

import re

import pytest

from schemalab.schedules import ScheduleError, analyze, parse_schedule


def test_parse_reads_operations_in_several_spellings() -> None:
    ops = parse_schedule("r1(A), W2[A]; r2(B) w1(b) c1 a2")
    assert [(o.txn, o.kind, o.item) for o in ops] == [
        (1, "r", "A"),
        (2, "w", "A"),
        (2, "r", "B"),
        (1, "w", "b"),
        (1, "c", None),
        (2, "a", None),
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("r1(A) x2(B)", "x2(B) at position 2 is not an operation"),
        ("r1(A) w1(A) c1 r1(B)", "T1 acts after it ended, at position 4"),
        ("", "Write a schedule"),
    ],
)
def test_parse_errors(text: str, message: str) -> None:
    with pytest.raises(ScheduleError, match=re.escape(message)):
        parse_schedule(text)


def test_conflict_serializable_schedule_and_its_serial_order() -> None:
    a = analyze("r1(A) w1(A) r2(A) w2(A) r1(B) w1(B) r2(B) w2(B) c1 c2")
    assert a["conflict_serializable"] is True
    assert a["serial_orders"] == [[1, 2]]
    assert a["edges"] == [{"from": 1, "to": 2, "items": ["A", "B"]}]
    assert a["cycle"] is None


def test_a_cycle_means_not_conflict_serializable() -> None:
    a = analyze("r1(A) r2(A) w1(A) w2(A) c1 c2")
    assert a["conflict_serializable"] is False
    assert a["cycle"] == [1, 2, 1]
    assert a["serial_orders"] == []


def test_view_serializable_but_not_conflict_serializable() -> None:
    # Blind writes: the classic r1(A) w2(A) w1(A) w3(A).
    a = analyze("r1(A) w2(A) w1(A) w3(A) c1 c2 c3")
    assert a["conflict_serializable"] is False
    assert a["view_serializable"] is True
    assert a["view_order"] == [1, 2, 3]


@pytest.mark.parametrize(
    ("schedule", "recoverable", "cascadeless", "strict"),
    [
        ("w1(A) r2(A) c2 c1", False, False, False),
        ("w1(A) r2(A) c1 c2", True, False, False),
        ("w1(A) w2(A) c1 c2", True, True, False),
        ("w1(A) c1 r2(A) w2(A) c2", True, True, True),
    ],
)
def test_recoverability_classes(
    schedule: str, recoverable: bool, cascadeless: bool, strict: bool
) -> None:
    a = analyze(schedule)
    assert (a["recoverable"]["ok"], a["cascadeless"]["ok"], a["strict"]["ok"]) == (
        recoverable,
        cascadeless,
        strict,
    )


def test_unrecoverable_reason_names_the_transactions() -> None:
    reason = analyze("w1(A) r2(A) c2 c1")["recoverable"]["reason"]
    assert reason == "T2 read A from T1 and committed before T1 did."


def test_two_phase_locking_can_produce_it_with_locks_shown() -> None:
    a = analyze("r1(A) w1(A) r2(A) w2(A) r1(B) w1(B) r2(B) w2(B) c1 c2")
    basic = a["locking"]["2PL"]
    assert basic["ok"] is True
    # T1 must lock B before it may release A to T2: that is its lock point.
    trace = " ".join(basic["schedule"])
    assert "xl1(B) u1(A) xl2(A)" in trace or "xl1(B) u1(A) sl2(A)" in trace
    strict = a["locking"]["strict 2PL"]
    assert strict["ok"] is False
    assert strict["step"] == 3
    assert "strict 2PL holds exclusive locks until commit" in strict["reason"]


def test_two_phase_locking_fails_when_a_lock_is_still_needed() -> None:
    a = analyze("r1(A) r2(A) w1(A) w2(A) c1 c2")
    basic = a["locking"]["2PL"]
    assert basic["ok"] is False
    assert "T2 still needs A" in basic["reason"]


def test_rigorous_two_phase_locking_needs_a_serial_looking_schedule() -> None:
    a = analyze("r1(A) r2(A) c1 c2")  # shared locks never conflict
    assert all(v["ok"] for v in a["locking"].values())
    serial = analyze("w1(A) c1 w2(A) c2")
    assert serial["locking"]["rigorous 2PL"]["ok"] is True


def test_limits() -> None:
    with pytest.raises(ScheduleError, match="at most 10 transactions"):
        parse_schedule(" ".join(f"r{i}(A)" for i in range(1, 12)))


def test_textbook_implications_hold_on_random_schedules() -> None:
    import random

    rng = random.Random(12)  # noqa: S311 - test data
    for _ in range(400):
        txns = rng.randint(2, 3)
        ops: list[str] = []
        for t in range(1, txns + 1):
            ops += [f"{rng.choice('rw')}{t}({rng.choice('AB')})" for _ in range(rng.randint(1, 3))]
        rng.shuffle(ops)
        ends = [f"{rng.choice('cccca')}{t}" for t in range(1, txns + 1)]
        rng.shuffle(ends)
        text = " ".join(ops + ends)
        a = analyze(text)
        locking = a["locking"]
        if locking["2PL"]["ok"]:
            assert a["conflict_serializable"], text  # 2PL produces only serializable schedules
        if locking["rigorous 2PL"]["ok"]:
            assert locking["strict 2PL"]["ok"], text
        if locking["strict 2PL"]["ok"]:
            assert locking["2PL"]["ok"], text
        if a["strict"]["ok"]:
            assert a["cascadeless"]["ok"], text
        if a["cascadeless"]["ok"]:
            assert a["recoverable"]["ok"], text
        if a["conflict_serializable"]:
            assert a["view_serializable"], text
