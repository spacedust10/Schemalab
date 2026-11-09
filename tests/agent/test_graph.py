"""The analyst loop: plan -> write SQL -> guarded run -> critic -> answer, with retries."""

from pathlib import Path
from typing import Any

import duckdb
import litellm
import pytest

from schemalab.agent.graph import extract_sql, redact_values, run_agent
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


def test_critic_objection_lowers_confidence_without_a_rewrite(sample_db: Path) -> None:
    # #24: across 54 eval questions the critic's rewrites fixed no answer and broke two right
    # ones, while its verdict separated right from wrong answers well. So it judges only.
    gw = ScriptedGateway(PLAN, GOOD_SQL, ISSUE)
    events = run(sample_db, gw)
    assert steps(events) == ["schema", "plan", "sql", "result", "critic", "answer"]
    assert events[4]["ok"] is False and "per customer" in events[4]["issue"]
    answer = events[-1]
    assert answer["confidence"]["label"] == "low"
    assert any("per customer" in r for r in answer["confidence"]["reasons"])
    assert answer["provenance"]["attempts"] == 1
    assert gw.replies == []


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


def test_database_errors_reach_the_model_without_row_values(tmp_path: Path) -> None:
    db = tmp_path / "p.duckdb"
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE patients (full_name VARCHAR, balance BIGINT)")
        con.execute("INSERT INTO patients VALUES ('Zanzibar Quill', 987654321)")
    leaky = [
        "SELECT CAST(full_name AS INTEGER) FROM patients",  # quotes the string value
        "SELECT CAST(balance AS TINYINT) FROM patients",  # quotes the number
    ]
    for sql in leaky:
        gw = ScriptedGateway(PLAN, sql, "SELECT count(*) FROM patients", OK)
        events = list(run_agent("?", db, gw))
        retry = events[2]
        assert retry["step"] == "retry" and retry["stage"] == "run"
        retry_prompt = gw.prompt(2)
        assert "Zanzibar" not in retry_prompt and "987654321" not in retry_prompt, retry_prompt
        assert "Conversion Error" in retry_prompt  # the model still learns what went wrong


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Conversion Error: Could not convert string 'Zanzibar Quill' to INT32",
            "Conversion Error: Could not convert string '<value>' to INT32",
        ),
        (
            "Type INT64 with value 987654321 can't be cast",
            "Type INT64 with value <number> can't be cast",
        ),
        ('Referenced column "nope" not found', 'Referenced column "nope" not found'),
        ("value 'O''Brien' and -3.5", "value '<value>' and <number>"),
    ],
)
def test_redact_values_keeps_identifiers_and_types(message: str, expected: str) -> None:
    assert redact_values(message) == expected


def test_missing_database_says_how_to_build_it(tmp_path: Path) -> None:
    events = list(run_agent("?", tmp_path / "missing.duckdb", ScriptedGateway()))
    assert steps(events) == ["error"]
    assert events[0]["stage"] == "data"
    assert "python -m schemalab.datasets" in events[0]["message"]


def test_database_without_tables_says_how_to_build_it(tmp_path: Path) -> None:
    db = tmp_path / "empty.duckdb"
    duckdb.connect(str(db)).close()
    events = list(run_agent("?", db, ScriptedGateway()))
    assert steps(events) == ["error"] and events[0]["stage"] == "data"


def test_prompts_forbid_filters_the_question_does_not_ask_for(sample_db: Path) -> None:
    # Live run: "Which borough had the most trips?" came back with WHERE passenger_count > 1,
    # and the critic approved it at high confidence.
    gw = ScriptedGateway(PLAN, GOOD_SQL, OK)
    run(sample_db, gw)
    plan, write_sql, critic = gw.prompt(0), gw.prompt(1), gw.prompt(2)
    for prompt in (plan, write_sql):
        assert "Add no filter the question does not ask for" in prompt
    assert "a filter the question does not ask for" in critic


def test_unknown_column_retry_lists_the_real_columns(sample_db: Path) -> None:
    # Live run: llama3.1 wrote COUNT(tt.TripID) three times; DuckDB's hint named one column.
    gw = ScriptedGateway(PLAN, "SELECT COUNT(o.TripID) FROM orders AS o", GOOD_SQL, OK)
    run(sample_db, gw)
    retry_message = gw.calls[2][-1]["content"]
    assert "orders" in retry_message
    for column in ("id", "customer_id", "amount"):
        assert column in retry_message
    assert "customers" not in retry_message  # only tables the query used


def test_critic_can_be_switched_off(sample_db: Path) -> None:
    # The eval harness compares runs with and without the reviewer (#24).
    gw = ScriptedGateway(PLAN, GOOD_SQL)
    events = list(run_agent("How many orders?", sample_db, gw, critic=False))
    assert steps(events) == ["schema", "plan", "sql", "result", "answer"]
    assert gw.replies == []
    assert "the reviewer was off" in events[-1]["confidence"]["reasons"]
