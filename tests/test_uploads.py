"""User uploads: CSV and Excel files become tables in a DuckDB file of the user's own."""

from datetime import date
from pathlib import Path

import openpyxl
import pytest

from schemalab.uploads import (
    MAX_TABLES,
    UploadError,
    delete_table,
    import_file,
    list_tables,
    table_name,
)


def csv_file(tmp_path: Path, text: str, name: str = "in.csv") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def xlsx_file(tmp_path: Path, sheets: dict[str, list[list[object]]]) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    path = tmp_path / "in.xlsx"
    wb.save(path)
    return path


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "user" / "1.duckdb"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Sales 2024.csv", "sales_2024"),
        ("orders.CSV", "orders"),
        ("2024-report.xlsx", "t_2024_report"),
        ("../../etc/passwd.csv", "passwd"),
        ('x"; DROP TABLE y; --.csv', "x_drop_table_y"),
        ("été.csv", "t"),
        (".csv", "upload"),
    ],
)
def test_table_name_is_a_safe_identifier(filename: str, expected: str) -> None:
    assert table_name(filename, taken=set()) == expected


def test_table_name_avoids_taken_names() -> None:
    assert table_name("orders.csv", taken={"orders", "orders_2"}) == "orders_3"


def test_csv_becomes_a_table_with_typed_columns(tmp_path: Path, db: Path) -> None:
    src = csv_file(tmp_path, "Order ID,Amount,Placed On\n1,9.5,2024-01-02\n2,,2024-01-03\n")
    assert import_file(db, src, "Orders.csv") == ["orders"]
    [table] = list_tables(db)
    assert table["name"] == "orders" and table["rows"] == 2
    # Names are normalized so the model can write SQL without quoting.
    columns = {c["name"]: c for c in table["columns"]}
    assert set(columns) == {"order_id", "amount", "placed_on"}
    assert columns["amount"]["type"] == "DOUBLE"
    assert columns["placed_on"]["type"] == "DATE"
    assert columns["amount"]["null_percent"] == 50.0
    assert columns["order_id"]["min"] == "1" and columns["order_id"]["max"] == "2"


def test_each_excel_sheet_becomes_a_table(tmp_path: Path, db: Path) -> None:
    src = xlsx_file(
        tmp_path,
        {
            "Q1": [
                ["Region", "Revenue", "Day"],
                ["North", 10, date(2024, 1, 1)],
                ["South", 20, None],
            ],
            "Empty": [],
            "Q2": [["Region", "Revenue"], ["North", 30]],
        },
    )
    assert import_file(db, src, "Sales.xlsx") == ["sales_q1", "sales_q2"]
    tables = {t["name"]: t for t in list_tables(db)}
    assert tables["sales_q1"]["rows"] == 2
    types = {c["name"]: c["type"] for c in tables["sales_q1"]["columns"]}
    # DuckDB prefixes reserved words ("day") so the model never has to quote a column.
    assert types == {"region": "VARCHAR", "revenue": "BIGINT", "_day": "DATE"}


def test_single_sheet_workbook_is_named_after_the_file(tmp_path: Path, db: Path) -> None:
    src = xlsx_file(tmp_path, {"Sheet1": [["a"], [1]]})
    assert import_file(db, src, "budget.xlsx") == ["budget"]


def test_uploads_add_to_existing_tables(tmp_path: Path, db: Path) -> None:
    src = csv_file(tmp_path, "a\n1\n")
    import_file(db, src, "t.csv")
    import_file(db, src, "t.csv")
    assert [t["name"] for t in list_tables(db)] == ["t", "t_2"]


@pytest.mark.parametrize("filename", ["data.xls", "data.json", "data.parquet", "data"])
def test_other_file_types_are_refused(tmp_path: Path, db: Path, filename: str) -> None:
    with pytest.raises(UploadError, match=r"\.csv or \.xlsx"):
        import_file(db, csv_file(tmp_path, "a\n1\n"), filename)
    assert not db.exists()


def test_a_file_that_is_not_really_excel_is_refused(tmp_path: Path, db: Path) -> None:
    with pytest.raises(UploadError, match="Excel"):
        import_file(db, csv_file(tmp_path, "a,b\n1,2\n"), "fake.xlsx")


def test_unreadable_csv_is_refused_and_leaves_tables_alone(tmp_path: Path, db: Path) -> None:
    import_file(db, csv_file(tmp_path, "a\n1\n"), "keep.csv")
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"")
    with pytest.raises(UploadError):
        import_file(db, bad, "bad.csv")
    assert [t["name"] for t in list_tables(db)] == ["keep"]


def test_too_many_columns_is_refused(tmp_path: Path, db: Path) -> None:
    header = ",".join(f"c{i}" for i in range(501))
    with pytest.raises(UploadError, match="500 columns"):
        import_file(db, csv_file(tmp_path, header + "\n" + ",".join("1" * 501) + "\n"), "wide.csv")
    assert list_tables(db) == []


def test_table_limit_is_enforced(tmp_path: Path, db: Path) -> None:
    src = csv_file(tmp_path, "a\n1\n")
    for _ in range(MAX_TABLES):
        import_file(db, src, "t.csv")
    with pytest.raises(UploadError, match=f"{MAX_TABLES} tables"):
        import_file(db, src, "t.csv")
    assert len(list_tables(db)) == MAX_TABLES


def test_list_tables_without_uploads_is_empty(db: Path) -> None:
    assert list_tables(db) == []


def test_delete_table(tmp_path: Path, db: Path) -> None:
    src = csv_file(tmp_path, "a\n1\n")
    import_file(db, src, "one.csv")
    import_file(db, src, "two.csv")
    assert delete_table(db, "one") is True
    assert [t["name"] for t in list_tables(db)] == ["two"]


@pytest.mark.parametrize("name", ["missing", 'two"; DROP TABLE two; --'])
def test_delete_unknown_table_changes_nothing(tmp_path: Path, db: Path, name: str) -> None:
    import_file(db, csv_file(tmp_path, "a\n1\n"), "two.csv")
    assert delete_table(db, name) is False
    assert [t["name"] for t in list_tables(db)] == ["two"]


def test_excel_is_read_whatever_the_temp_file_is_called(tmp_path: Path, db: Path) -> None:
    # The API spools uploads to a temp file without an extension; openpyxl checks extensions
    # on paths, so a real workbook was refused as "not an Excel file".
    src = xlsx_file(tmp_path, {"Sheet1": [["a"], [1]]})
    spooled = src.rename(tmp_path / "tmpk2j9x")
    assert import_file(db, spooled, "budget.xlsx") == ["budget"]
