"""The join guard: every join in the analyst's query must be an allowed key equality."""

import pytest

from schemalab.guards.joins import JoinError, check_joins, edge

SCHEMA = {
    "taxi_trips": {
        "VendorID": "INT",
        "PULocationID": "INT",
        "DOLocationID": "INT",
        "fare": "DOUBLE",
    },
    "taxi_zones": {"LocationID": "INT", "Borough": "VARCHAR"},
    "mi_insurance": {"id": "INT", "year": "INT", "value": "DOUBLE"},
}
GRAPH = {
    edge("taxi_trips", "PULocationID", "taxi_zones", "LocationID"),
    edge("taxi_trips", "DOLocationID", "taxi_zones", "LocationID"),
}


def check(sql: str) -> list[str]:
    return check_joins(sql, SCHEMA, GRAPH)


def test_edges_are_unordered_and_case_blind() -> None:
    assert edge("A", "X", "b", "y") == edge("b", "Y", "a", "x")


def test_a_query_without_joins_passes_with_nothing_to_report() -> None:
    assert check("SELECT count(*) FROM taxi_trips") == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT z.Borough, count(*) FROM taxi_trips t JOIN taxi_zones z "
        "ON t.PULocationID = z.LocationID GROUP BY 1",
        "SELECT * FROM taxi_trips t, taxi_zones z WHERE z.LocationID = t.PULocationID",
        "SELECT * FROM taxi_zones z JOIN taxi_trips ON PULocationID = LocationID",
        "WITH z AS (SELECT LocationID AS lid, Borough FROM taxi_zones) "
        "SELECT * FROM taxi_trips t JOIN z ON t.DOLocationID = z.lid",
        "SELECT * FROM taxi_trips t WHERE EXISTS "
        "(SELECT 1 FROM taxi_zones z WHERE z.LocationID = t.PULocationID)",
    ],
)
def test_allowed_key_joins_pass_and_are_reported(sql: str) -> None:
    [used] = check(sql)
    assert used in (
        "taxi_trips.pulocationid = taxi_zones.locationid",
        "taxi_trips.dolocationid = taxi_zones.locationid",
    )


def test_joining_a_lookup_twice_reports_both_joins() -> None:
    sql = (
        "SELECT * FROM taxi_trips t JOIN taxi_zones pu ON t.PULocationID = pu.LocationID "
        "JOIN taxi_zones du ON t.DOLocationID = du.LocationID"
    )
    assert check(sql) == [
        "taxi_trips.dolocationid = taxi_zones.locationid",
        "taxi_trips.pulocationid = taxi_zones.locationid",
    ]


@pytest.mark.parametrize(
    ("sql", "why"),
    [
        # The eval's wrong joins: tables from different datasets on unrelated columns.
        (
            "SELECT * FROM mi_insurance i JOIN taxi_zones z ON i.id = z.LocationID",
            "not a known join",
        ),
        (
            "SELECT * FROM mi_insurance i JOIN taxi_trips t ON i.year = EXTRACT(YEAR FROM t.fare)",
            "key",
        ),
        (
            "SELECT * FROM taxi_trips t JOIN taxi_zones z ON t.VendorID = z.LocationID",
            "not a known join",
        ),
        ("SELECT * FROM taxi_trips, taxi_zones", "key"),
        ("SELECT * FROM taxi_trips t CROSS JOIN taxi_zones z", "key"),
        ("SELECT * FROM taxi_trips NATURAL JOIN taxi_zones", "NATURAL"),
        (
            "SELECT * FROM taxi_trips t JOIN taxi_zones z "
            "ON t.PULocationID = z.LocationID OR 1 = 1",
            "OR",
        ),
        ("SELECT * FROM taxi_trips t JOIN taxi_zones z ON t.PULocationID > z.LocationID", "key"),
    ],
)
def test_other_joins_are_refused_with_a_reason(sql: str, why: str) -> None:
    with pytest.raises(JoinError, match=why):
        check(sql)


def test_a_refusal_lists_the_joins_that_exist_for_those_tables() -> None:
    with pytest.raises(JoinError) as e:
        check("SELECT * FROM taxi_trips t JOIN taxi_zones z ON t.VendorID = z.LocationID")
    assert "taxi_trips.pulocationid = taxi_zones.locationid" in str(e.value)
    assert "taxi_trips.dolocationid = taxi_zones.locationid" in str(e.value)


def test_a_table_with_no_joins_says_so() -> None:
    with pytest.raises(JoinError, match="mi_insurance has no joins"):
        check("SELECT * FROM mi_insurance i JOIN taxi_zones z ON i.id = z.LocationID")


def test_a_self_join_on_the_same_column_passes() -> None:
    sql = "SELECT * FROM mi_insurance a JOIN mi_insurance b ON a.id = b.id AND a.year = b.year"
    assert check(sql) == []


@pytest.mark.parametrize(
    "sql",
    [
        # A semi-join is a join: insurance ids matched to zone ids.
        "SELECT * FROM mi_insurance WHERE id IN (SELECT LocationID FROM taxi_zones)",
        # A correlated subquery that links the tables without a key equality.
        "SELECT * FROM mi_insurance i WHERE EXISTS "
        "(SELECT 1 FROM taxi_zones z WHERE z.LocationID > i.id)",
        # A UNION CTE: the first branch alone would look like the zone key.
        "WITH k AS (SELECT LocationID AS lid FROM taxi_zones "
        "UNION ALL SELECT id FROM mi_insurance) "
        "SELECT * FROM taxi_trips t JOIN k ON t.PULocationID = k.lid",
        # Columns qualify cannot resolve, across several tables.
        "SELECT * FROM taxi_trips t JOIN taxi_zones z ON t.PULocationID = z.nope",
    ],
)
def test_joins_hidden_in_subqueries_and_unions_are_refused(sql: str) -> None:
    with pytest.raises(JoinError):
        check(sql)


def test_an_allowed_semi_join_passes() -> None:
    sql = "SELECT * FROM taxi_trips WHERE PULocationID IN (SELECT LocationID FROM taxi_zones)"
    assert check(sql) == ["taxi_trips.pulocationid = taxi_zones.locationid"]


def test_an_unknown_column_in_a_one_table_query_is_left_to_duckdb() -> None:
    # DuckDB's error names the near match; the join guard has nothing to say.
    assert check("SELECT nope FROM taxi_trips") == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM taxi_trips t, taxi_zones z "
        "WHERE t.PULocationID IN (1, 2) AND z.LocationID IN (3)",
        "SELECT * FROM taxi_trips t, taxi_zones z WHERE t.fare + 1 IN (1, 2)",
    ],
)
def test_a_value_list_does_not_link_tables(sql: str) -> None:
    with pytest.raises(JoinError, match="key"):
        check(sql)
