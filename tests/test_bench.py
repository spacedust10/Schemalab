"""TPC-H / TPC-DS on DuckDB and PostgreSQL. The Postgres parts need LAB_TEST_DSN."""

import os
from pathlib import Path

import pytest

from schemalab.bench import BENCHMARKS, prepare, run, schema_name, to_postgres

DSN = os.environ.get("LAB_TEST_DSN", "")
needs_postgres = pytest.mark.skipif(not DSN, reason="set LAB_TEST_DSN")


def test_benchmarks_and_scales() -> None:
    assert set(BENCHMARKS) == {"tpch", "tpcds"}
    assert BENCHMARKS["tpch"].scales == (0.01, 0.1, 1.0)


def test_schema_names_are_fixed_identifiers() -> None:
    assert schema_name("tpch", 0.1) == "bench_tpch_sf0_1"
    assert schema_name("tpcds", 1.0) == "bench_tpcds_sf1"


def test_postgres_rounds_numerics_not_doubles() -> None:
    # PostgreSQL has round(numeric, int) but no round(double precision, int); TPC-DS uses it.
    sql = to_postgres("SELECT round(sum(x) / 3.0, 2) FROM t")
    assert sql.startswith("SELECT ROUND(CAST(") and sql.endswith(" AS DECIMAL), 2) FROM t")


@needs_postgres
def test_prepare_builds_both_copies_once(tmp_path: Path) -> None:
    steps: list[str] = []
    prepare(tmp_path, DSN, "tpch", 0.01, steps.append)
    assert (tmp_path / "tpch_sf0_01.duckdb").exists()
    assert any("PostgreSQL" in s for s in steps)
    again: list[str] = []
    prepare(tmp_path, DSN, "tpch", 0.01, again.append)
    assert again == ["Data already prepared."]


@needs_postgres
def test_run_times_both_engines_and_checks_they_agree(tmp_path: Path) -> None:
    prepare(tmp_path, DSN, "tpch", 0.01, lambda _: None)
    result = run(tmp_path, DSN, "tpch", 0.01, reps=2, only=[1, 6])
    assert [q["nr"] for q in result["queries"]] == [1, 6]
    for q in result["queries"]:
        assert q["duckdb_ms"] > 0 and q["postgres_ms"] > 0
        assert q["agree"] is True and q["error"] is None
    s = result["summary"]
    assert s["queries"] == 2 and s["agreed"] == 2
    assert s["geomean_duckdb_ms"] > 0 and s["geomean_postgres_ms"] > 0


@needs_postgres
def test_a_failing_query_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import schemalab.bench as bench

    prepare(tmp_path, DSN, "tpch", 0.01, lambda _: None)
    monkeypatch.setattr(bench, "to_postgres", lambda q: "SELECT * FROM no_such_table")
    result = run(tmp_path, DSN, "tpch", 0.01, reps=1, only=[6])
    [q] = result["queries"]
    assert q["postgres_ms"] is None and "no_such_table" in q["error"]
    assert result["summary"]["agreed"] == 0


@needs_postgres
def test_prepare_analyzes_only_its_own_tables(tmp_path: Path) -> None:
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute("DROP TABLE IF EXISTS public.bench_bystander")
        pg.execute("CREATE TABLE public.bench_bystander AS SELECT 1 AS x")
        pg.execute(f"DROP SCHEMA IF EXISTS {schema_name('tpch', 0.01)} CASCADE")
    prepare(tmp_path / "fresh", DSN, "tpch", 0.01, lambda _: None)
    with psycopg.connect(DSN, autocommit=True) as pg:
        row = pg.execute(
            "SELECT last_analyze FROM pg_stat_user_tables WHERE relname = 'bench_bystander'"
        ).fetchone()
        pg.execute("DROP TABLE public.bench_bystander")
    assert row is not None and row[0] is None


@needs_postgres
def test_lab_users_cannot_read_the_benchmark_schema(tmp_path: Path) -> None:
    from schemalab.lab.session import Lab

    prepare(tmp_path, DSN, "tpch", 0.01, lambda _: None)
    lab = Lab(DSN, user_id=930)
    lab.start()
    try:
        out = lab.run("A", f"SELECT count(*) FROM {schema_name('tpch', 0.01)}.lineitem")
        assert out["state"] == "error" and "permission denied" in str(out["error"])
    finally:
        lab.close()
