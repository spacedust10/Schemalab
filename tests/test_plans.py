"""EXPLAIN ANALYZE plans: the tree, where time goes, misestimates, and index advice.
The fixtures are real PostgreSQL 17 plans in JSON."""

import json
from pathlib import Path
from typing import Any

from schemalab.plans import advise, summarize

FIXTURES = Path(__file__).parent / "fixtures" / "plans"


def plan(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def node(summary: dict[str, Any], node_type: str) -> dict[str, Any]:
    return next(n for n in summary["nodes"] if n["type"] == node_type)


def test_summary_has_timings_and_a_labelled_tree() -> None:
    s = summarize(plan("seq_scan_filter"))
    assert s["execution_ms"] == 7.482 and s["planning_ms"] == 0.206
    assert [n["label"] for n in s["nodes"]] == [
        "Limit",
        "Gather Merge",
        "Sort by total DESC",
        "Seq Scan on orders",
    ]
    assert s["tree"]["label"] == "Limit" and s["tree"]["children"][0]["label"] == "Gather Merge"


def test_per_loop_figures_are_multiplied_out() -> None:
    scan = node(summarize(plan("seq_scan_filter")), "Seq Scan")
    # Two parallel processes each returned 100 rows and removed 99,900.
    assert scan["loops"] == 2 and scan["rows"] == 200 and scan["removed"] == 199_800


def test_misestimates_are_flagged() -> None:
    s = summarize(plan("seq_scan_filter"))
    scan = node(s, "Seq Scan")
    assert scan["misestimate"] == 50.0
    assert any("estimated 2 rows, got 100" in w for w in s["warnings"])


def test_the_hotspot_is_the_node_with_the_most_time_of_its_own() -> None:
    s = summarize(plan("nested_loop"))
    loop = node(s, "Nested Loop")
    assert s["hotspot"] == loop["id"]
    assert loop["self_ms"] > 40  # comparing 600,000 pairs, not reading rows


def test_advice_for_a_filtered_scan_with_order_and_limit() -> None:
    advice = advise(summarize(plan("seq_scan_filter")), existing={})
    assert [a["sql"] for a in advice] == [
        "CREATE INDEX orders_status_customer_id_total_idx ON orders (status, customer_id, total);"
    ]
    assert "read 200,000 rows to return 200" in advice[0]["reason"]
    assert "ORDER BY total" in advice[0]["reason"]


def test_advice_for_a_nested_loop_join_filter() -> None:
    advice = advise(summarize(plan("nested_loop")), existing={})
    assert [a["sql"] for a in advice] == [
        "CREATE INDEX orders_customer_id_idx ON orders (customer_id);"
    ]
    assert "600,000" in advice[0]["reason"]


def test_no_advice_once_an_index_is_used() -> None:
    assert advise(summarize(plan("index_scan")), existing={}) == []


def test_no_advice_for_an_index_that_already_exists() -> None:
    existing = {"orders": [["status", "customer_id", "total"]]}
    assert advise(summarize(plan("seq_scan_filter")), existing=existing) == []
