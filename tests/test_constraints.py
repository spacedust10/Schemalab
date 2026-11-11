"""Constraint coach: constraints the data already satisfies, with evidence and DDL."""

from pathlib import Path

import duckdb
import pytest

from schemalab.constraints import Suggestion, suggest


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute(
            "CREATE TABLE customers (id INTEGER, email VARCHAR, tier VARCHAR, note VARCHAR)"
        )
        con.execute(
            "INSERT INTO customers SELECT i, 'c' || i || '@x.org',"
            " CASE WHEN i % 3 = 0 THEN 'gold' ELSE 'basic' END,"
            " CASE WHEN i % 2 = 0 THEN NULL ELSE 'n' END FROM range(1, 31) t(i)"
        )
        con.execute(
            "CREATE TABLE orders"
            " (order_id INTEGER, customer_id INTEGER, total DECIMAL(8,2), qty INTEGER)"
        )
        con.execute(
            "INSERT INTO orders SELECT 100 + i, 1 + i % 30, i * 1.5, 1 + i % 4 FROM range(60) t(i)"
        )
        con.execute("CREATE TABLE lines (order_id INTEGER, product VARCHAR, n INTEGER)")
        con.execute(
            "INSERT INTO lines VALUES"
            " (100, 'pen', 1), (100, 'ink', 2), (101, 'pen', 1), (102, 'pen', -1)"
        )
    return path


def by_kind(items: list[Suggestion], kind: str) -> list[Suggestion]:
    return [s for s in items if s.kind == kind]


def test_primary_key_prefers_an_id_column(db: Path) -> None:
    [pk] = by_kind(suggest(db, "customers"), "primary key")
    assert pk.columns == ["id"]
    assert pk.sql == "ALTER TABLE customers ADD PRIMARY KEY (id);"
    assert pk.evidence == "All 30 rows have a value, and no two are the same."


def test_link_tables_get_a_two_column_key(db: Path) -> None:
    [pk] = by_kind(suggest(db, "lines"), "primary key")
    assert pk.columns == ["order_id", "product"]


def test_foreign_keys_need_matching_values_and_a_name_hint(db: Path) -> None:
    [fk] = by_kind(suggest(db, "orders"), "foreign key")
    assert fk.columns == ["customer_id"]
    assert fk.sql == ("ALTER TABLE orders ADD FOREIGN KEY (customer_id) REFERENCES customers (id);")
    assert "every one of the 60 values appears in customers.id" in fk.evidence
    # qty values 1..4 all appear in customers.id too, but nothing in the names links them.
    assert all(s.columns != ["qty"] for s in by_kind(suggest(db, "orders"), "foreign key"))


def test_not_null_only_where_nothing_is_missing(db: Path) -> None:
    columns = {tuple(s.columns) for s in by_kind(suggest(db, "customers"), "not null")}
    assert ("email",) in columns and ("tier",) in columns
    assert ("note",) not in columns
    assert ("id",) not in columns  # the primary key already implies it


def test_unique_columns_other_than_the_key(db: Path) -> None:
    [unique] = by_kind(suggest(db, "customers"), "unique")
    assert unique.columns == ["email"]
    assert unique.sql == "ALTER TABLE customers ADD UNIQUE (email);"


def test_checks_for_non_negative_numbers_and_short_category_lists(db: Path) -> None:
    checks = {s.sql for s in by_kind(suggest(db, "orders"), "check")}
    assert "ALTER TABLE orders ADD CHECK (total >= 0);" in checks
    assert "ALTER TABLE orders ADD CHECK (qty >= 0);" in checks
    tiers = {s.sql for s in by_kind(suggest(db, "customers"), "check")}
    assert "ALTER TABLE customers ADD CHECK (tier IN ('basic', 'gold'));" in tiers
    # lines.n has a negative value, so no check is suggested for it.
    assert not any("(n >= 0)" in s.sql for s in suggest(db, "lines"))


def test_unknown_table(db: Path) -> None:
    with pytest.raises(KeyError):
        suggest(db, "nope")


def test_names_that_need_quoting_are_quoted(tmp_path: Path) -> None:
    path = tmp_path / "q.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute('CREATE TABLE "order" ("select" INTEGER)')
        con.execute('INSERT INTO "order" VALUES (1), (2)')
    [pk] = by_kind(suggest(path, "order"), "primary key")
    assert pk.sql == 'ALTER TABLE "order" ADD PRIMARY KEY ("select");'


def test_link_table_prefers_its_reference_pair_over_a_coincidence(tmp_path: Path) -> None:
    # Live case: four enrollments whose grades happened to differ were offered grade as key.
    path = tmp_path / "uni.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute(
            "CREATE TABLE enrollments (student_id INTEGER, course_code VARCHAR, grade DECIMAL(3,1))"
        )
        con.execute(
            "INSERT INTO enrollments VALUES"
            " (1, 'A', 3.7), (1, 'B', 4.0), (2, 'A', 3.0), (3, 'B', 3.3)"
        )
        con.execute("COMMENT ON COLUMN enrollments.student_id IS 'references students(id)'")
        con.execute("COMMENT ON COLUMN enrollments.course_code IS 'references courses(code)'")
    found = suggest(path, "enrollments")
    [pk] = by_kind(found, "primary key")
    assert pk.columns == ["student_id", "course_code"]
    assert not any("student_id >= 0" in s.sql for s in found)  # it already references students
    assert by_kind(found, "unique") == []  # four different grades prove nothing


def test_a_unique_measurement_is_not_offered_as_a_key(tmp_path: Path) -> None:
    path = tmp_path / "m.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE readings (taken DATE, value DOUBLE, ratio DECIMAL(4,2))")
        con.execute(
            "INSERT INTO readings VALUES ('2024-01-01', 1.5, 0.1), ('2024-01-02', 2.5, 0.2)"
        )
    assert by_kind(suggest(path, "readings"), "primary key") == []
