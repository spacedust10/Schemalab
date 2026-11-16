"""Where the allowed joins come from: the sample registry and the keys stored with uploads."""

import json
from pathlib import Path

import duckdb

from schemalab.datasets import REGISTRY
from schemalab.guards.joins import edge
from schemalab.joingraph import describe_joins, join_graph, schema_of


def db(tmp_path: Path, *statements: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    for s in statements:
        con.execute(s)
    return con


def test_the_sample_registry_declares_the_taxi_lookups(tmp_path: Path) -> None:
    con = db(
        tmp_path,
        "CREATE TABLE taxi_trips (PULocationID INT, DOLocationID INT, fare DOUBLE)",
        "CREATE TABLE taxi_zones (LocationID INT, Borough VARCHAR)",
        "CREATE TABLE mi_insurance (id INT, year INT)",
    )
    assert join_graph(con) == {
        edge("taxi_trips", "PULocationID", "taxi_zones", "LocationID"),
        edge("taxi_trips", "DOLocationID", "taxi_zones", "LocationID"),
    }


def test_registry_joins_apply_only_to_tables_and_columns_that_exist(tmp_path: Path) -> None:
    con = db(tmp_path, "CREATE TABLE taxi_trips (PULocationID INT)")
    assert join_graph(con) == set()  # no taxi_zones here
    con.execute("CREATE TABLE taxi_zones (Borough VARCHAR)")
    assert join_graph(con) == set()  # no LocationID column


def test_no_registry_join_crosses_datasets() -> None:
    for ds in REGISTRY.values():
        own = {s.table for s in ds.sources}
        for left, _, right, _ in ds.joins:
            assert {left, right} <= own, (ds.name, left, right)


def test_upload_foreign_keys_become_joins(tmp_path: Path) -> None:
    keys = {"primary": ["id"], "foreign": [[["customer_id"], "customers", ["id"]]]}
    con = db(
        tmp_path,
        "CREATE TABLE customers (id INT, name VARCHAR)",
        "CREATE TABLE orders (id INT, customer_id INT)",
        "COMMENT ON TABLE orders IS 'schemalab keys " + json.dumps(keys) + "'",
    )
    assert join_graph(con) == {edge("orders", "customer_id", "customers", "id")}


def test_composite_foreign_keys_pair_up_column_by_column(tmp_path: Path) -> None:
    keys = {"foreign": [[["a", "b"], "parent", ["x", "y"]]]}
    con = db(
        tmp_path,
        "CREATE TABLE parent (x INT, y INT)",
        "CREATE TABLE child (a INT, b INT)",
        "COMMENT ON TABLE child IS 'schemalab keys " + json.dumps(keys) + "'",
    )
    assert join_graph(con) == {edge("child", "a", "parent", "x"), edge("child", "b", "parent", "y")}


def test_a_broken_keys_comment_adds_nothing(tmp_path: Path) -> None:
    con = db(
        tmp_path,
        "CREATE TABLE t (a INT)",
        "COMMENT ON TABLE t IS 'schemalab keys {not json'",
    )
    assert join_graph(con) == set()


def test_the_schema_maps_tables_to_columns(tmp_path: Path) -> None:
    con = db(tmp_path, "CREATE TABLE t (a INT, b VARCHAR)")
    assert schema_of(con) == {"t": {"a": "INTEGER", "b": "VARCHAR"}}


def test_the_prompt_lists_the_joins() -> None:
    graph = {edge("taxi_trips", "PULocationID", "taxi_zones", "LocationID")}
    assert describe_joins(graph) == (
        "Joins you may use (no others will run):\n  taxi_trips.pulocationid = taxi_zones.locationid"
    )
    assert describe_joins(set()) == ""
