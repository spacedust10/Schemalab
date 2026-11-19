"""Models run in the order their tables need, worked out from each model's SQL."""

import pytest

from schemalab.lakehouse import LakehouseError, check_models, order_models

BRONZE = [{"layer": "bronze", "name": "orders"}]


def model(layer: str, name: str, sql: str) -> dict[str, str]:
    return {"layer": layer, "name": name, "sql": sql}


def targets(models: list[dict[str, str]]) -> list[str]:
    return [f"{x['layer']}.{x['name']}" for x in models]


def test_a_silver_model_runs_after_the_silver_model_it_reads_whatever_the_listed_order() -> None:
    models = [
        model("gold", "total", "SELECT count(*) AS n FROM silver_big"),
        model("silver", "big", "SELECT id FROM silver_paid WHERE id > 1"),
        model("silver", "paid", "SELECT id FROM bronze_orders"),
    ]
    assert targets(check_models(models, BRONZE)) == ["silver.paid", "silver.big", "gold.total"]


def test_models_that_need_nothing_keep_the_order_they_were_listed_in() -> None:
    models = [
        model("silver", "b", "SELECT 1 AS x"),
        model("gold", "g", "SELECT 2 AS x"),
        model("silver", "a", "SELECT 3 AS x"),
    ]
    assert targets(order_models(models)) == ["silver.b", "gold.g", "silver.a"]


def test_a_cte_named_like_a_model_is_not_a_dependency() -> None:
    models = [
        model("silver", "a", "WITH silver_b AS (SELECT 1 AS x) SELECT x FROM silver_b"),
        model("silver", "b", "SELECT 1 AS x FROM silver_a"),
    ]
    assert targets(order_models(models)) == ["silver.a", "silver.b"]


def test_a_cte_in_one_subquery_does_not_hide_a_model_read_in_another() -> None:
    sql = "SELECT * FROM (WITH silver_b AS (SELECT 1 AS x) SELECT x FROM silver_b), silver_b"
    models = [model("silver", "a", sql), model("silver", "b", "SELECT 1 AS x")]
    assert targets(order_models(models)) == ["silver.b", "silver.a"]


def test_models_that_read_each_other_are_refused_with_the_loop_named() -> None:
    models = [
        model("silver", "a", "SELECT id FROM silver_b"),
        model("silver", "b", "SELECT id FROM silver_a"),
    ]
    with pytest.raises(LakehouseError, match=r"loop: silver\.(a|b) → silver\.(a|b) → silver\."):
        check_models(models, BRONZE)


def test_a_model_may_read_its_own_previous_table() -> None:
    tables = [*BRONZE, {"layer": "silver", "name": "paid"}]
    sql = "SELECT id FROM silver_paid UNION SELECT id FROM bronze_orders"
    models = [model("silver", "paid", sql)]
    assert targets(check_models(models, tables)) == ["silver.paid"]


def test_gold_still_cannot_read_gold() -> None:
    models = [model("gold", "a", "SELECT 1 AS x"), model("gold", "b", "SELECT x FROM gold_a")]
    with pytest.raises(LakehouseError, match=r"gold\.b"):
        check_models(models, BRONZE)


def test_a_model_that_does_not_parse_is_ordered_as_is_and_refused_by_the_check() -> None:
    models = [model("silver", "a", "SELEC oops"), model("silver", "b", "SELECT 1 AS x")]
    assert targets(order_models(models)) == ["silver.a", "silver.b"]
    with pytest.raises(LakehouseError, match=r"silver\.a"):
        check_models(models, BRONZE)
