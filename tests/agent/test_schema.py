"""Schema description sent to the model: structure only, never row values."""

from pathlib import Path

import duckdb
import pytest

from schemalab.agent.schema import describe_schema

PRIVATE_VALUE = "Zanzibar-Patient-4417"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "s.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE patients (id INTEGER, full_name VARCHAR, visit DATE)")
        con.execute(
            f"INSERT INTO patients VALUES (1, '{PRIVATE_VALUE}', DATE '2026-03-14'), (2, 'x', NULL)"
        )
        con.execute("CREATE TABLE clinics (clinic_id BIGINT, city VARCHAR)")
    return path


def test_lists_tables_with_columns_types_and_row_counts(db: Path) -> None:
    text = describe_schema(db)
    assert "patients (2 rows)" in text
    assert "clinics (0 rows)" in text
    assert "full_name VARCHAR" in text
    assert "visit DATE" in text
    assert "clinic_id BIGINT" in text


def test_never_includes_cell_values(db: Path) -> None:
    text = describe_schema(db)
    assert PRIVATE_VALUE not in text
    assert "2026-03-14" not in text


def test_tables_are_in_a_stable_order(db: Path) -> None:
    text = describe_schema(db)
    assert text.index("clinics") < text.index("patients")
    assert describe_schema(db) == text


def test_can_limit_to_some_tables(db: Path) -> None:
    text = describe_schema(db, tables={"clinics"})
    assert "clinics" in text and "patients" not in text
