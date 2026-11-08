"""The analyst loop: plan -> write SQL -> guarded run -> critic -> answer, with retries."""

from pathlib import Path
from typing import Any

import litellm
import pytest

from schemalab.agent.graph import extract_sql, run_agent
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


ISSUE = '{"ok": false, "issue": "Groups by date, but the question only asks per customer."}'


def test_critic_objection_is_fed_back_and_retried(sample_db: Path) -> None:
    gw = ScriptedGateway(PLAN, GOOD_SQL, ISSUE, GOOD_SQL, OK)
    events = run(sample_db, gw)
    assert steps(events) == [
        "schema",
        "plan",
        "sql",
        "result",
        "critic",
        "sql",
        "result",
        "critic",
        "answer",
    ]
    first = events[4]
    assert first["ok"] is False and "per customer" in first["issue"] and first["attempt"] == 1
    assert "per customer" in gw.prompt(3)  # the rewrite sees the critic's issue
    assert events[7]["ok"] is True


def test_critic_never_sees_row_values(sample_db: Path) -> None:
    gw = ScriptedGateway(PLAN, "SELECT name FROM customers ORDER BY name", OK)
    events = run(sample_db, gw)
    assert ["c0"] in events[3]["rows"]
    critic_prompt = gw.prompt(2)
    assert "SELECT name FROM customers" in critic_prompt and "10 rows" in critic_prompt
    assert not any(f"c{i}" in critic_prompt.split() for i in range(10))


def test_empty_result_is_retried_without_asking_the_critic(sample_db: Path) -> None:
    empty = "SELECT * FROM orders WHERE amount < 0"
    gw = ScriptedGateway(PLAN, empty, GOOD_SQL, OK)
    events = run(sample_db, gw)
    assert steps(events) == [
        "schema",
        "plan",
        "sql",
        "result",
        "critic",
        "sql",
        "result",
        "critic",
        "answer",
    ]
    assert events[4]["ok"] is False and "no rows" in events[4]["issue"]
    assert "model" not in events[4]  # decided without a model call


def test_persistent_objection_still_answers(sample_db: Path) -> None:
    gw = ScriptedGateway(PLAN, GOOD_SQL, ISSUE, GOOD_SQL, ISSUE, GOOD_SQL, ISSUE)
    events = run(sample_db, gw)
    assert steps(events)[-2:] == ["critic", "answer"]
    assert events[-2]["ok"] is False and events[-2]["attempt"] == 3


def test_unreadable_critic_reply_counts_as_no_verdict(sample_db: Path) -> None:
    gw = ScriptedGateway(PLAN, GOOD_SQL, "Looks fine to me!")
    events = run(sample_db, gw)
    assert events[-2]["step"] == "critic" and events[-2]["ok"] is None
    assert events[-1]["step"] == "answer"


def test_answer_has_text_confidence_and_provenance(sample_db: Path) -> None:
    gw = ScriptedGateway(PLAN, GOOD_SQL, OK)
    answer = run(sample_db, gw)[-1]
    assert answer["text"] == "n is 100."
    assert answer["confidence"]["label"] == "high"
    prov = answer["provenance"]
    assert prov["sql"] == "SELECT COUNT(*) AS n FROM orders"
    assert prov["tables"] == ["orders"]
    assert prov["attempts"] == 1
    assert prov["models"] == [MODEL]
    assert prov["results_shared_with_model"] is False


def test_shared_results_let_the_model_word_the_answer(sample_db: Path) -> None:
    gw = ScriptedGateway(PLAN, GOOD_SQL, OK, "There are 100 orders.")
    answer = list(run_agent("How many orders?", sample_db, gw, share_results=True))[-1]
    assert answer["text"] == "There are 100 orders."
    assert "100" in gw.prompt(3)
    assert answer["provenance"]["results_shared_with_model"] is True


@pytest.mark.parametrize(
    "text",
    [
        "SELECT count(*) FROM orders",
        "```sql\nSELECT count(*) FROM orders\n```",
        "Here is the query:\n```\nSELECT count(*) FROM orders;\n```\nIt counts orders.",
        "```SQL\nSELECT count(*) FROM orders\n```",
    ],
)
def test_extracts_sql_from_model_replies(text: str) -> None:
    assert extract_sql(text).rstrip(";") == "SELECT count(*) FROM orders"
