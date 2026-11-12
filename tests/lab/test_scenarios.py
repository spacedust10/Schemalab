"""Every guided scenario, played against real PostgreSQL, does what its steps say."""

import os
import time
from typing import Any

import pytest

from schemalab.lab.scenarios import SCENARIOS, Scenario
from schemalab.lab.session import Lab

DSN = os.environ.get("LAB_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="set LAB_TEST_DSN to a Postgres superuser DSN")


def matches(outcome: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(outcome.get(k) == v for k, v in expected.items())


def finish(lab: Lab, session: str) -> dict[str, Any]:
    end = time.monotonic() + 5
    while time.monotonic() < end:
        outcome = lab.result(session)
        if outcome is None or outcome["state"] != "waiting":
            return outcome or {}
        time.sleep(0.05)
    raise AssertionError(f"{session} never finished")


def play(lab: Lab, scenario: Scenario) -> list[dict[str, Any]]:
    for statement in scenario.setup:
        assert lab.run("A", statement)["state"] == "done", statement
    seen: list[dict[str, Any]] = []
    waiting: dict[str, int] = {}  # session -> index of its step still waiting
    for i, step in enumerate(scenario.steps):
        # A step's session must be free: wait for an earlier statement there to finish.
        if step.session in waiting:
            j = waiting.pop(step.session)
            seen[j] = {**seen[j], "finished": finish(lab, step.session)}
        outcome = lab.run(step.session, step.sql, wait_s=1.5)
        seen.append({"outcome": outcome})
        if outcome["state"] == "waiting":
            waiting[step.session] = i
        # Whatever was waiting may have been released by this step.
        for other, j in list(waiting.items()):
            if other != step.session:
                result = lab.result(other)
                if result and result["state"] != "waiting":
                    seen[j] = {**seen[j], "finished": result}
                    waiting.pop(other)
    for other, j in waiting.items():
        seen[j] = {**seen[j], "finished": finish(lab, other)}
    return seen


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_scenario_behaves_as_described(scenario: Scenario) -> None:
    lab = Lab(DSN, user_id=990)
    lab.start()
    try:
        seen = play(lab, scenario)
        for step, record in zip(scenario.steps, seen, strict=True):
            assert matches(record["outcome"], step.expect), (step.sql, record)
            if step.then:
                assert matches(record.get("finished", {}), step.then), (step.sql, record)
        if scenario.id == "deadlock":
            codes = {r.get("outcome", {}).get("code") for r in seen} | {
                r.get("finished", {}).get("code") for r in seen
            }
            assert "40P01" in codes
    finally:
        lab.close()
