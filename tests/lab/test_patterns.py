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
