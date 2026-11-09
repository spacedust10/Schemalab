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


def test_column_notes_tell_the_model_how_tables_join(db: Path) -> None:
    with duckdb.connect(str(db)) as con:
        con.execute("COMMENT ON COLUMN patients.id IS 'references clinics(clinic_id)'")
    assert "  id INTEGER -- references clinics(clinic_id)" in describe_schema(db)


@pytest.fixture
def survey(tmp_path: Path) -> Path:
    path = tmp_path / "survey.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE answers (id INTEGER, sex VARCHAR, county VARCHAR, note VARCHAR)")
        con.execute(
            "INSERT INTO answers SELECT i,"
            " CASE WHEN i % 2 = 0 THEN 'Male Only' ELSE 'Both Sexes' END,"
            " CASE WHEN i % 3 = 0 THEN 'O''Brien County' ELSE 'Kent County, MI' END,"
            " 'free text ' || i FROM range(300) t(i)"
        )
    return path


def test_category_values_are_off_by_default(survey: Path) -> None:
    text = describe_schema(survey)
    assert "Male Only" not in text and "Kent County" not in text


def test_category_values_list_short_text_columns_as_sql_literals(survey: Path) -> None:
    text = describe_schema(survey, categories=True)
    assert "  sex VARCHAR -- values: 'Both Sexes', 'Male Only'" in text
    # Quotes are doubled so the model can copy a value straight into SQL.
    assert "  county VARCHAR -- values: 'Kent County, MI', 'O''Brien County'" in text


def test_category_values_skip_high_cardinality_and_non_text_columns(survey: Path) -> None:
    text = describe_schema(survey, categories=True)
    assert "free text" not in text  # 300 distinct values: free text, not a category
    assert "  id INTEGER\n" in text + "\n"


def test_category_values_and_key_notes_share_a_line(survey: Path) -> None:
    with duckdb.connect(str(survey)) as con:
        con.execute("COMMENT ON COLUMN answers.sex IS 'references sexes(code)'")
    text = describe_schema(survey, categories=True)
    assert "  sex VARCHAR -- references sexes(code); values: 'Both Sexes', 'Male Only'" in text
