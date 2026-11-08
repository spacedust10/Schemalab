"""Confidence labels and answer text, both computed without the model."""

import pytest

from schemalab.agent.answer import confidence, summarize, tables_read


@pytest.mark.parametrize(
    ("kwargs", "label", "reason"),
    [
        ({}, "high", "first attempt"),
        ({"attempts": 2}, "medium", "2 attempts"),
        ({"truncated": True}, "medium", "truncated"),
        ({"verdict": None}, "medium", "no verdict"),
        ({"verdict": False, "issue": "groups by date"}, "low", "groups by date"),
        ({"row_count": 0}, "low", "no rows"),
        ({"attempts": 3, "verdict": False, "issue": "x"}, "low", "3 attempts"),
    ],
)
def test_confidence_says_what_lowered_it(
    kwargs: dict[str, object], label: str, reason: str
) -> None:
    args: dict[str, object] = {
        "attempts": 1,
        "verdict": True,
        "issue": None,
        "truncated": False,
        "row_count": 5,
    }
    args.update(kwargs)
    result = confidence(**args)  # type: ignore[arg-type]
    assert result["label"] == label
    assert any(reason in r for r in result["reasons"]), result["reasons"]


@pytest.mark.parametrize(
    ("columns", "rows", "truncated", "expected"),
    [
        (["n"], [[100]], False, "n is 100."),
        (
            ["borough", "trips"],
            [["Manhattan", 2646948]],
            False,
            "borough is Manhattan, trips is 2,646,948.",
        ),
        (["x"], [], False, "The query returned no rows."),
        (["x"], [[1], [2], [3]], False, "3 rows, shown below."),
        (["x"], [[1], [2]], True, "The first 2 rows are shown below; there are more."),
        (["avg"], [[4.169670627490249]], False, "avg is 4.17."),
    ],
)
def test_summarize(
    columns: list[str], rows: list[list[object]], truncated: bool, expected: str
) -> None:
    assert summarize(columns, rows, truncated) == expected


def test_tables_read_skips_ctes_and_dedupes() -> None:
    sql = (
        "WITH t AS (SELECT * FROM orders) "
        "SELECT * FROM t JOIN customers c ON true JOIN orders o ON true"
    )
    assert tables_read(sql) == ["customers", "orders"]
