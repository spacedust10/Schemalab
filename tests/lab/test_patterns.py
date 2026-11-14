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


def test_new_order_without_locks_repeats_order_numbers_and_miscounts_stock(lab: Lab) -> None:
    got = lab.pattern("new_order", "naive")
    assert check(got, "Orders that failed")["actual"] > 0  # a repeated order number
    assert check(got, "Stock count off by")["actual"] != 0


@pytest.mark.parametrize("mode", ["for_update", "atomic"])
def test_new_order_with_locks_or_atomic_updates_places_every_order(lab: Lab, mode: str) -> None:
    got = lab.pattern("new_order", mode)
    assert all(c["ok"] for c in got["checks"]), got["checks"]
    assert check(got, "Orders placed")["actual"] == 60


def test_a_second_run_while_one_is_running_is_refused_at_once(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    from schemalab.lab import patterns

    running, release = threading.Event(), threading.Event()

    def slow(name: str, mode: str, connect: Any) -> dict[str, Any]:
        running.set()
        release.wait(timeout=30)  # stays busy until the test has tried a second run
        return {"name": name}

    monkeypatch.setattr(patterns, "run", slow)
    first = threading.Thread(target=lab.pattern, args=("transfer", "naive"))
    first.start()
    assert running.wait(timeout=10)
    start = time.perf_counter()
    with pytest.raises(RuntimeError, match="already running"):
        lab.pattern("transfer", "naive")
    assert time.perf_counter() - start < 0.5  # refused at once, not queued
    release.set()
    first.join(timeout=30)
    assert lab.pattern("transfer", "naive") == {"name": "transfer"}  # free again afterwards


def test_polling_a_timestamp_misses_deletes_and_same_instant_changes(lab: Lab) -> None:
    got = lab.pattern("cdc", "polling")
    assert check(got, "Deleted rows still in the replica")["actual"] > 0
    assert check(got, "Replica matches the source")["actual"] is False


def test_a_change_log_replays_into_an_exact_replica(lab: Lab) -> None:
    got = lab.pattern("cdc", "change_log")
    assert all(c["ok"] for c in got["checks"]), got["checks"]
    assert got["stats"]["changes"] > 0


def test_one_big_table_reads_every_month_and_keeps_deleted_space(lab: Lab) -> None:
    got = lab.pattern("partitioning", "one_table")
    assert check(got, "Rows read to total March")["actual"] == 120_000
    assert check(got, "January's disk space given back")["actual"] is False


def test_monthly_partitions_read_one_month_and_drop_one_cleanly(lab: Lab) -> None:
    got = lab.pattern("partitioning", "partitioned")
    assert all(c["ok"] for c in got["checks"]), got["checks"]
    assert got["stats"]["tables_scanned"] == 1  # one partition of twelve


def test_overwriting_a_customers_state_moves_past_sales(lab: Lab) -> None:
    got = lab.pattern("scd2", "overwrite")
    assert (
        check(got, "Q1 revenue in Michigan")["actual"] == 50
    )  # Ada's January and February sales moved to Ohio
    assert check(got, "Q1 revenue in Ohio")["actual"] == 670
    assert check(got, "Versions of Ada kept")["actual"] == 1


def test_type_2_rows_keep_each_sale_where_it_happened(lab: Lab) -> None:
    got = lab.pattern("scd2", "type2")
    assert all(c["ok"] for c in got["checks"]), got["checks"]


def test_the_normalized_model_joins_six_tables_for_one_question(lab: Lab) -> None:
    got = lab.pattern("star", "oltp")
    assert check(got, "Tables joined for the question")["actual"] == 6


def test_a_star_schema_answers_the_same_with_four_tables(lab: Lab) -> None:
    got = lab.pattern("star", "star")
    assert all(c["ok"] for c in got["checks"]), got["checks"]
    assert check(got, "Same answer as the source")["actual"] is True
