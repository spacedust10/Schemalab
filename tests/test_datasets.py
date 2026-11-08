"""Sample dataset loader. Uses file:// URLs to local fixtures, so no network."""

import hashlib
import re
from pathlib import Path

import duckdb
import pytest

from schemalab.datasets import (
    ChecksumError,
    Dataset,
    MissingFileError,
    Source,
    build_database,
)
from schemalab.guards.run import run_query


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    src = tmp_path / "src"
    src.mkdir()
    csv = src / "zones.csv"
    csv.write_text("zone_id,borough\n1,Manhattan\n2,Queens\n3,Bronx\n")
    parquet = src / "trips.parquet"
    duckdb.sql(
        f"COPY (SELECT i AS trip_id, i % 3 + 1 AS zone_id FROM range(50) t(i)) TO '{parquet}'"
    )
    return {"csv": csv, "parquet": parquet}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def demo(files: dict[str, Path], **overrides: str) -> Dataset:
    return Dataset(
        name="demo",
        sources=[
            Source(table="zones", url=files["csv"].as_uri(), sha256=sha256(files["csv"])),
            Source(
                table="trips",
                url=files["parquet"].as_uri(),
                sha256=overrides.get("trips_sha", sha256(files["parquet"])),
            ),
        ],
    )


def test_builds_one_table_per_source(files: dict[str, Path], tmp_path: Path) -> None:
    db = tmp_path / "out.duckdb"
    build_database(db, [demo(files)], cache_dir=tmp_path / "cache")
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM zones").fetchone() == (3,)
        assert con.execute("SELECT count(*) FROM trips").fetchone() == (50,)


def test_reuses_cached_files(files: dict[str, Path], tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    build_database(tmp_path / "a.duckdb", [demo(files)], cache_dir=cache)
    ds = demo(files)
    for f in files.values():
        f.unlink()  # the source is gone; only the cache can serve the second build
    build_database(tmp_path / "b.duckdb", [ds], cache_dir=cache)


def test_rejects_a_file_with_the_wrong_checksum(files: dict[str, Path], tmp_path: Path) -> None:
    db = tmp_path / "out.duckdb"
    with pytest.raises(ChecksumError, match="trips"):
        build_database(db, [demo(files, trips_sha="0" * 64)], cache_dir=tmp_path / "cache")
    # Nothing from a dataset with a bad file is loaded.
    with duckdb.connect(str(db)) as con:
        assert con.execute("SHOW TABLES").fetchall() == []


def test_manual_source_explains_where_to_put_the_file(tmp_path: Path) -> None:
    ds = Dataset(
        name="olist",
        sources=[Source(table="orders", filename="olist_orders_dataset.csv")],
        manual_steps="Download the archive from Kaggle and unzip it.",
    )
    with pytest.raises(MissingFileError, match=re.escape("olist_orders_dataset.csv")) as err:
        build_database(tmp_path / "out.duckdb", [ds], cache_dir=tmp_path / "cache")
    assert "Kaggle" in str(err.value)


@pytest.mark.parametrize("name", ["drop table x", "a;b", "1abc", ""])
def test_rejects_unsafe_table_names(name: str) -> None:
    with pytest.raises(ValueError, match="table name"):
        Source(table=name, url="file:///x.csv")


def test_built_database_works_with_guarded_queries(files: dict[str, Path], tmp_path: Path) -> None:
    db = tmp_path / "out.duckdb"
    build_database(db, [demo(files)], cache_dir=tmp_path / "cache")
    result = run_query(
        db,
        "SELECT z.borough, count(*) AS n FROM trips t JOIN zones z USING (zone_id)"
        " GROUP BY z.borough ORDER BY z.borough",
    )
    assert result.rows == [("Bronx", 16), ("Manhattan", 17), ("Queens", 17)]
