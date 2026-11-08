"""The analyst loop: plan -> write SQL -> guarded run -> critic -> answer, with retries."""

from pathlib import Path
from typing import Any

import litellm

from schemalab.agent.graph import run_agent
from tests.agent.scripted import MODEL, ScriptedGateway

PLAN = "Count rows in orders."
GOOD_SQL = "```sql\nSELECT count(*) AS n FROM orders\n```"
OK = '{"ok": true}'


def steps(events: list[dict[str, Any]]) -> list[str]:
    return [e["step"] for e in events]


def run(db: Path, gw: ScriptedGateway, question: str = "How many orders?") -> list[dict[str, Any]]:
    return list(run_agent(question, db, gw))


def test_happy_path_streams_every_step(sample_db: Path) -> None:
    gw = ScriptedGateway(PLAN, GOOD_SQL, OK)
    events = run(sample_db, gw)
    assert steps(events) == ["schema", "plan", "sql", "result", "critic", "answer"]
    assert events[1]["text"] == PLAN
    assert events[2]["sql"] == "SELECT COUNT(*) AS n FROM orders"
    assert events[2]["attempt"] == 1
    assert events[3]["rows"] == [[100]]
    assert gw.replies == []


def test_sql_writer_sees_the_plan(sample_db: Path) -> None:
    gw = ScriptedGateway(PLAN, GOOD_SQL, OK)
    run(sample_db, gw)
    assert PLAN in gw.prompt(1)


def test_guard_rejection_is_fed_back_and_retried(sample_db: Path) -> None:
    gw = ScriptedGateway(PLAN, "DROP TABLE orders", GOOD_SQL, OK)
    events = run(sample_db, gw)
    assert steps(events) == ["schema", "plan", "retry", "sql", "result", "critic", "answer"]
    retry = events[2]
    assert (
        retry["stage"] == "guard" and retry["attempt"] == 1 and retry["sql"] == "DROP TABLE orders"
    )
    assert "DROP" in gw.prompt(2)  # the second SQL prompt carries the guard's message
    assert events[3]["attempt"] == 2


def test_database_error_is_fed_back_and_retried(sample_db: Path) -> None:
    gw = ScriptedGateway(PLAN, "SELECT nope FROM orders", GOOD_SQL, OK)
    events = run(sample_db, gw)
    assert events[2]["step"] == "retry" and events[2]["stage"] == "run"
    assert "nope" in gw.prompt(2)


def test_gives_up_after_three_attempts(sample_db: Path) -> None:
    gw = ScriptedGateway(PLAN, "DROP TABLE a", "DROP TABLE b", "DROP TABLE c")
    events = run(sample_db, gw)
    assert steps(events) == ["schema", "plan", "retry", "retry", "error"]
    assert events[-1]["stage"] == "guard"
    assert events[-1]["attempts"] == 3


def test_model_outage_ends_with_an_error(sample_db: Path) -> None:
    down = litellm.APIConnectionError("connection refused", llm_provider="ollama", model=MODEL)
    events = run(sample_db, ScriptedGateway(down))
    assert steps(events) == ["schema", "error"]
    assert events[-1]["stage"] == "model"
