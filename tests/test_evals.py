"""Eval harness: the question file, the result comparison and scoring, all without a real model."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from schemalab.evals import Case, load_cases, run_case, same_result, summarize
from schemalab.guards.sql import check_sql
from tests.agent.scripted import ScriptedGateway

SAMPLE_TABLES = {"taxi_trips", "taxi_zones", "mi_insurance"}
Rows = list[tuple[object, ...]]


def test_question_file_has_enough_unique_cases() -> None:
    cases = load_cases()
    assert len(cases) >= 50
    assert len({c.id for c in cases}) == len(cases)
    assert all(c.question.strip() and c.sql.strip() for c in cases)


def test_every_reference_query_passes_the_sql_guard() -> None:
    # The reference answers are held to the same rules as the agent's queries.
    for case in load_cases():
        check_sql(case.sql, SAMPLE_TABLES)


def test_the_cases_from_issue_24_are_included() -> None:
    questions = {c.question for c in load_cases()}
    assert "Which pickup borough had the most taxi trips?" in questions
    assert "How many taxi trips started in Queens?" in questions


@pytest.mark.parametrize(
    ("expected", "got"),
    [
        ([(273128,)], [(273128.0,)]),
        ([(3.35,)], [(3.3499999,)]),
        ([("a", 1), ("b", 2)], [("b", 2), ("a", 1)]),  # row order does not matter
        ([("a", 1), ("b", 2)], [(1, "a"), (2, "b")]),  # neither does column order
        ([("a", 1)], [("a", 1, "extra")]),  # extra columns are fine
        ([("Manhattan", 9)], [("Manhattan", 9), ("Queens", 3)]),  # top row of a ranking
        ([(Decimal("1.50"),)], [(1.5,)]),
        ([(date(2024, 1, 2),)], [("2024-01-02",)]),
        ([], []),
        ([(None,)], [(None,)]),
    ],
)
def test_same_result_accepts(
    expected: list[tuple[object, ...]], got: list[tuple[object, ...]]
) -> None:
    assert same_result(expected, got)


@pytest.mark.parametrize(
    ("expected", "got"),
    [
        ([(273128,)], [(146987,)]),
        ([("a", 1), ("b", 2)], [("a", 1)]),
        ([("a", 1), ("b", 2)], [("a", 2), ("b", 1)]),  # same columns, rows paired wrongly
        ([("Manhattan", 9)], [("Queens", 3), ("Manhattan", 9)]),  # wrong top row
        ([(1,)], []),
        ([("a", 1)], [("a",)]),  # a missing column
    ],
)
def test_same_result_rejects(
    expected: list[tuple[object, ...]], got: list[tuple[object, ...]]
) -> None:
    assert not same_result(expected, got)


CASE = Case(id="orders", question="How many orders?", sql="SELECT count(*) FROM orders")


def test_run_case_scores_a_matching_answer(sample_db: Path) -> None:
    gw = ScriptedGateway("Count.", "```sql\nSELECT count(id) AS n FROM orders\n```", '{"ok": true}')
    record = run_case(CASE, sample_db, gw)
    assert record["correct"] is True
    assert record["label"] == "high" and record["attempts"] == 1 and record["error"] is None


def test_run_case_scores_a_wrong_answer(sample_db: Path) -> None:
    gw = ScriptedGateway("Count.", "SELECT count(*) FROM customers", '{"ok": true}')
    assert run_case(CASE, sample_db, gw)["correct"] is False


def test_run_case_records_a_failure(sample_db: Path) -> None:
    gw = ScriptedGateway("Count.", "SELECT nope", "SELECT nope", "SELECT nope")
    record = run_case(CASE, sample_db, gw)
    assert record["correct"] is False and record["error"] == "run"


def test_run_case_without_the_critic(sample_db: Path) -> None:
    gw = ScriptedGateway("Count.", "SELECT count(*) FROM orders")
    record = run_case(CASE, sample_db, gw, critic=False)
    assert record["correct"] is True and record["label"] == "medium"


def test_summarize_reports_accuracy_per_confidence_label() -> None:
    records = [
        {"correct": True, "label": "high", "error": None},
        {"correct": False, "label": "high", "error": None},
        {"correct": True, "label": "medium", "error": None},
        {"correct": False, "label": None, "error": "run"},
    ]
    summary = summarize(records)
    assert summary["n"] == 4 and summary["accuracy"] == 0.5 and summary["errors"] == 1
    assert summary["by_label"] == {
        "high": {"n": 2, "accuracy": 0.5},
        "medium": {"n": 1, "accuracy": 1.0},
        "none": {"n": 1, "accuracy": 0.0},
    }


def test_run_case_can_share_category_values(sample_db: Path) -> None:
    gw = ScriptedGateway("Count.", "SELECT count(*) FROM orders", '{"ok": true}')
    run_case(CASE, sample_db, gw, categories=True)
    assert "values: 'c0'" in gw.prompt(0)
