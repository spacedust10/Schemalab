"""Answer text, confidence and provenance, computed from what happened, not asked of the model."""

from collections.abc import Sequence
from typing import Any, Literal

import sqlglot
from sqlglot import exp

Label = Literal["low", "medium", "high"]
_RANK: dict[Label, int] = {"low": 0, "medium": 1, "high": 2}


def confidence(
    attempts: int, verdict: bool | None, issue: str | None, truncated: bool, row_count: int
) -> dict[str, Any]:
    """The label is the lowest any single reason allows, and every reason is listed."""
    label: Label = "high"
    reasons: list[str] = []

    def lower(to: Label, reason: str) -> None:
        nonlocal label
        reasons.append(reason)
        if _RANK[to] < _RANK[label]:
            label = to

    if verdict is False:
        lower("low", f"the reviewer still objected: {issue}")
    elif verdict is None:
        lower("medium", "the reviewer gave no verdict")
    if row_count == 0:
        lower("low", "the query returned no rows")
    if attempts > 1:
        lower("medium", f"it took {attempts} attempts to get a working query")
    if truncated:
        lower("medium", "the result was truncated")
    if not reasons:
        reasons.append("the reviewer agreed on the first attempt")
    return {"label": label, "reasons": reasons}


def summarize(columns: Sequence[str], rows: Sequence[Sequence[Any]], truncated: bool) -> str:
    """A plain sentence about the result, written without sending any value to a model."""
    if not rows:
        return "The query returned no rows."
    if truncated:
        return f"The first {len(rows)} rows are shown below; there are more."
    if len(rows) == 1:
        parts = [f"{c} is {_fmt(v)}" for c, v in zip(columns, rows[0], strict=True)]
        return ", ".join(parts) + "."
    return f"{len(rows)} rows, shown below."


def tables_read(sql: str) -> list[str]:
    """Base tables a (guarded) query reads, for the provenance panel."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    return sorted({t.name.lower() for t in tree.find_all(exp.Table)} - ctes)


def _fmt(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)
