"""The lake against a real Postgres catalog; data files go to a temporary directory."""

import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from ingest import lake as lk
from ingest.config import Settings

DSN = os.environ.get("LAKE_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="set LAKE_TEST_DSN to a Postgres to run")


@pytest.fixture
def s(tmp_path: Path) -> Settings:
    return Settings(
        catalog=DSN,
        data_root=str(tmp_path / "data"),
        publish_dir=tmp_path / "publish",
        work_dir=tmp_path / "work",
    )


@pytest.fixture
def name() -> str:
    return f"u{secrets.randbelow(10**9)}"


def csv(tmp_path: Path, text: str, file: str = "orders.csv") -> Path:
    path = tmp_path / file
    path.write_text(text)
    return path


ORDERS = "id,amount,status\n1,10.5,paid\n2,4,paid\n3,7.25,refunded\n"


def test_a_csv_lands_in_bronze(s: Settings, name: str, tmp_path: Path) -> None:
    assert lk.load_file(s, name, csv(tmp_path, ORDERS), "orders") == {"orders": 3}
    [table] = lk.tables(s, name)
    assert table["layer"] == "bronze"
    assert table["name"] == "orders"
    assert table["rows"] == 3
    assert [c["name"] for c in table["columns"]] == ["id", "amount", "status"]


def test_a_parquet_file_lands_in_bronze(s: Settings, name: str, tmp_path: Path) -> None:
    path = tmp_path / "t.parquet"
    duckdb.sql(f"COPY (SELECT range AS n FROM range(5)) TO '{path}'")
    assert lk.load_file(s, name, path, "numbers") == {"numbers": 5}


def test_reloading_a_file_replaces_the_table(s: Settings, name: str, tmp_path: Path) -> None:
    lk.load_file(s, name, csv(tmp_path, ORDERS), "orders")
    lk.load_file(s, name, csv(tmp_path, "id,amount,status\n9,1,paid\n"), "orders")
    assert [t["rows"] for t in lk.tables(s, name)] == [1]


def _feed(*events: tuple[str, int]) -> dict[str, Any]:
    return {
        "features": [
            {
                "id": i,
                "properties": {"time": ms, "mag": 3.0, "place": "here"},
                "geometry": {"coordinates": [1.0, 2.0, 3.0]},
            }
            for i, ms in events
        ]
    }


def test_quakes_load_only_what_is_new(s: Settings, name: str) -> None:
    asked: list[str] = []
    feeds = [_feed(("a", 1_000_000), ("b", 2_000_000)), _feed(("b", 2_000_000), ("c", 3_000_000))]

    def fetch(url: str) -> dict[str, Any]:
        asked.append(url)
        return feeds[len(asked) - 1]

    assert lk.load_quakes(s, name, fetch) == {"quakes": 2}
    lk.load_quakes(s, name, fetch)
    # The second run asks from the newest event it has, and the repeat of "b" merges away.
    since = datetime.fromtimestamp(2_000, UTC).strftime("%Y-%m-%dT%H%%3A%M%%3A%S")
    assert f"starttime={since}" in asked[1]
    assert [t["rows"] for t in lk.tables(s, name)] == [3]


MODELS = [
    {
        "layer": "silver",
        "name": "orders",
        "sql": "SELECT id, amount FROM bronze_orders WHERE status = 'paid'",
    },
    {"layer": "gold", "name": "revenue", "sql": "SELECT sum(amount) AS total FROM silver_orders"},
]


def test_models_build_silver_then_gold_in_one_snapshot(
    s: Settings, name: str, tmp_path: Path
) -> None:
    lk.load_file(s, name, csv(tmp_path, ORDERS), "orders")
    before = len(lk.snapshots(s, name))
    assert lk.transform(s, name, MODELS) == {"silver.orders": 2, "gold.revenue": 1}
    assert len(lk.snapshots(s, name)) == before + 1
    preview = lk.preview(s, name, "gold", "revenue", None)
    assert preview["rows"] == [[14.5]]


def test_a_failing_model_changes_nothing(s: Settings, name: str, tmp_path: Path) -> None:
    lk.load_file(s, name, csv(tmp_path, ORDERS), "orders")
    broken = [MODELS[0], {"layer": "gold", "name": "bad", "sql": "SELECT nope FROM silver_orders"}]
    with pytest.raises(lk.LakeError, match=r"gold\.bad"):
        lk.transform(s, name, broken)
    assert [t["layer"] for t in lk.tables(s, name)] == ["bronze"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM postgres_query('__ducklake_metadata_lake', 'SELECT 1')",
        "SELECT * FROM __ducklake_metadata_lake.information_schema.schemata",
        "SELECT getenv('INGEST_CATALOG') AS leak",
    ],
)
def test_models_cannot_reach_files_or_the_catalog(
    s: Settings, name: str, tmp_path: Path, sql: str
) -> None:
    # The API's SQL guard refuses these first; this is the second layer.
    lk.load_file(s, name, csv(tmp_path, ORDERS), "orders")
    with pytest.raises(lk.LakeError):
        lk.transform(s, name, [{"layer": "silver", "name": "x", "sql": sql}])


def test_a_table_can_be_read_as_it_was(s: Settings, name: str, tmp_path: Path) -> None:
    lk.load_file(s, name, csv(tmp_path, ORDERS), "orders")
    first = lk.snapshots(s, name)[0]["id"]
    lk.load_file(s, name, csv(tmp_path, "id,amount,status\n9,1,paid\n"), "orders")
    assert lk.preview(s, name, "bronze", "orders", None)["total"] == 1
    assert lk.preview(s, name, "bronze", "orders", first)["total"] == 3


def test_previewing_an_unknown_table_is_refused(s: Settings, name: str, tmp_path: Path) -> None:
    lk.load_file(s, name, csv(tmp_path, ORDERS), "orders")
    with pytest.raises(lk.LakeError):
        lk.preview(s, name, "bronze", "orders; DROP TABLE x", None)


def test_silver_and_gold_are_published_for_ask(s: Settings, name: str, tmp_path: Path) -> None:
    lk.load_file(s, name, csv(tmp_path, ORDERS), "orders")
    lk.transform(s, name, MODELS)
    with duckdb.connect(str(s.publish_dir / f"{name}.duckdb"), read_only=True) as con:
        names = {r[0] for r in con.sql("SELECT table_name FROM duckdb_tables()").fetchall()}
        assert names == {"silver_orders", "gold_revenue"}
        assert con.sql("SELECT total FROM gold_revenue").fetchone() == (14.5,)


def test_lakes_do_not_see_each_other(s: Settings, name: str, tmp_path: Path) -> None:
    lk.load_file(s, name, csv(tmp_path, ORDERS), "orders")
    assert lk.tables(s, f"{name}0") == []


def test_only_lake_names_the_api_makes_are_accepted(s: Settings) -> None:
    for bad in ("u1; DROP", "public", "U1", "u", "u1/../u2"):
        with pytest.raises(lk.LakeError):
            lk.tables(s, bad)
    assert re.fullmatch(lk.NAME.pattern, "u12")


def test_a_new_lake_can_be_opened_by_several_requests_at_once(s: Settings, name: str) -> None:
    # The page asks for the overview while the first load is creating the lake.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: lk.tables(s, name), range(4)))
    assert results == [[]] * 4
