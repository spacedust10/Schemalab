"""Transaction patterns run against real PostgreSQL connections. Needs LAB_TEST_DSN."""

import os
from collections.abc import Iterator
from typing import Any

import pytest

from schemalab.lab.session import Lab

DSN = os.environ.get("LAB_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="set LAB_TEST_DSN to a Postgres superuser DSN")


@pytest.fixture
def lab() -> Iterator[Lab]:
    lab = Lab(DSN, user_id=950)
    lab.start()
    yield lab
    lab.close()


def check(result: dict[str, Any], label: str) -> dict[str, Any]:
    return next(c for c in result["checks"] if c["label"] == label)


def test_naive_transfers_lose_money(lab: Lab) -> None:
    got = lab.pattern("transfer", "naive")
    total = check(got, "Money in the bank")
    assert total["ok"] is False and total["actual"] != total["expected"] == 3000
    assert got["stats"]["transfers"] == 60


@pytest.mark.parametrize("mode", ["for_update", "atomic"])
def test_locked_or_atomic_transfers_keep_every_cent(lab: Lab, mode: str) -> None:
    got = lab.pattern("transfer", mode)
    assert all(c["ok"] for c in got["checks"]), got["checks"]
    assert check(got, "Money in the bank")["actual"] == 3000
    assert got["stats"]["deadlocks"] == 0


def test_without_a_key_every_retry_charges_again(lab: Lab) -> None:
    got = lab.pattern("idempotency", "no_key")
    assert check(got, "Charges made") == {
        "label": "Charges made",
        "expected": 10,
        "actual": 30,
        "ok": False,
    }


def test_checking_first_still_races(lab: Lab) -> None:
    got = lab.pattern("idempotency", "check_first")
    assert check(got, "Customers charged twice")["actual"] > 0


def test_an_idempotency_key_charges_each_payment_once(lab: Lab) -> None:
    got = lab.pattern("idempotency", "key")
    assert all(c["ok"] for c in got["checks"]), got["checks"]
    assert got["stats"]["requests"] == 30 and got["stats"]["replayed"] == 20


def test_last_write_wins_loses_edits(lab: Lab) -> None:
    got = lab.pattern("optimistic", "last_write_wins")
    assert check(got, "Edits lost")["actual"] > 0


def test_a_version_column_detects_conflicts_and_keeps_every_edit(lab: Lab) -> None:
    got = lab.pattern("optimistic", "version")
    assert all(c["ok"] for c in got["checks"]), got["checks"]
    assert check(got, "Stock after every edit")["actual"] == 115
    assert got["stats"]["conflicts"] > 0  # detected, then retried


def test_a_dual_write_loses_events_when_the_app_crashes(lab: Lab) -> None:
    got = lab.pattern("outbox", "dual_write")
    assert check(got, "Orders without an event") == {
        "label": "Orders without an event",
        "expected": 0,
        "actual": 5,
        "ok": False,
    }


def test_relays_without_row_locks_publish_events_twice(lab: Lab) -> None:
    got = lab.pattern("outbox", "outbox_no_lock")
    assert check(got, "Orders without an event")["actual"] == 0
    assert check(got, "Events published twice")["actual"] > 0


def test_an_outbox_with_skip_locked_relays_publishes_each_event_once(lab: Lab) -> None:
    got = lab.pattern("outbox", "outbox")
    assert all(c["ok"] for c in got["checks"]), got["checks"]
