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
    assert import_file(db, src, "Orders.csv").tables == ["orders"]
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
    assert import_file(db, src, "Sales.xlsx").tables == ["sales_q1", "sales_q2"]
    tables = {t["name"]: t for t in list_tables(db)}
    assert tables["sales_q1"]["rows"] == 2
    types = {c["name"]: c["type"] for c in tables["sales_q1"]["columns"]}
    # DuckDB prefixes reserved words ("day") so the model never has to quote a column.
    assert types == {"region": "VARCHAR", "revenue": "BIGINT", "_day": "DATE"}


def test_single_sheet_workbook_is_named_after_the_file(tmp_path: Path, db: Path) -> None:
    src = xlsx_file(tmp_path, {"Sheet1": [["a"], [1]]})
    assert import_file(db, src, "budget.xlsx").tables == ["budget"]


def test_uploads_add_to_existing_tables(tmp_path: Path, db: Path) -> None:
    src = csv_file(tmp_path, "a\n1\n")
    import_file(db, src, "t.csv")
    import_file(db, src, "t.csv")
    assert [t["name"] for t in list_tables(db)] == ["t", "t_2"]


@pytest.mark.parametrize("filename", ["data.xls", "data.json", "data.parquet", "data"])
def test_other_file_types_are_refused(tmp_path: Path, db: Path, filename: str) -> None:
    with pytest.raises(UploadError, match=r"\.csv, \.xlsx or \.sql"):
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
    with pytest.raises(UploadError, match=f"has 1 table and you have {MAX_TABLES}; you can"):
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
    assert import_file(db, spooled, "budget.xlsx").tables == ["budget"]


def test_excel_xml_is_parsed_with_defusedxml() -> None:
    # openpyxl only defends against entity-expansion attacks when defusedxml is installed.
    assert openpyxl.DEFUSEDXML is True


def test_workbook_that_unpacks_too_large_is_refused(
    tmp_path: Path, db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A zip-compressed sheet can expand far past the upload limit; cap what reaches disk.
    monkeypatch.setattr("schemalab.uploads.MAX_UNPACKED_BYTES", 1000)
    src = xlsx_file(tmp_path, {"Big": [["text"]] + [["x" * 50] for _ in range(100)]})
    with pytest.raises(UploadError, match="too large once unpacked"):
        import_file(db, src, "big.xlsx")
    assert list_tables(db) == []


DUMP = """
CREATE TABLE public.customers (id integer NOT NULL, name text, joined date);
CREATE TABLE public.orders (
    id serial PRIMARY KEY,
    customer_id integer REFERENCES public.customers(id),
    total numeric(10,2),
    paid_with money
);
COPY public.customers (id, name, joined) FROM stdin;
1\tAda\t2024-01-02
2\tBob\tnot a date
\\.
INSERT INTO orders (id, customer_id, total) VALUES (10, 1, 19.5), (11, 2, NULL);
ALTER TABLE ONLY public.customers ADD CONSTRAINT customers_pkey PRIMARY KEY (id);
CREATE INDEX orders_customer ON orders (customer_id);
GRANT SELECT ON orders TO analyst;
"""


def sql_file(tmp_path: Path, text: str = DUMP) -> Path:
    path = tmp_path / "dump.sql"
    path.write_text(text)
    return path


def test_sql_dump_loads_tables_rows_and_keys(tmp_path: Path, db: Path) -> None:
    result = import_file(db, sql_file(tmp_path), "shop.sql")
    assert result.tables == ["customers", "orders"]
    assert result.skipped == {"CREATE INDEX": 1, "GRANT": 1}
    tables = {t["name"]: t for t in list_tables(db)}
    assert tables["customers"]["rows"] == 2 and tables["orders"]["rows"] == 2
    customers = {c["name"]: c for c in tables["customers"]["columns"]}
    orders = {c["name"]: c for c in tables["orders"]["columns"]}
    assert customers["id"]["note"] == "primary key"
    assert orders["customer_id"]["note"] == "references customers(id)"
    assert orders["total"]["type"] == "DECIMAL(10,2)"
    assert customers["joined"]["type"] == "DATE"
    # A value the column type cannot hold becomes empty instead of failing the whole file.
    assert customers["joined"]["null_percent"] == 50.0
    # MONEY is not a DuckDB type, so the column keeps its text.
    assert orders["paid_with"]["type"] == "VARCHAR"


def test_sql_dump_names_avoid_existing_tables_and_keep_references(tmp_path: Path, db: Path) -> None:
    import_file(db, csv_file(tmp_path, "a\n1\n"), "customers.csv")
    result = import_file(db, sql_file(tmp_path), "shop.sql")
    assert result.tables == ["customers_2", "orders"]
    orders = next(t for t in list_tables(db) if t["name"] == "orders")
    note = next(c["note"] for c in orders["columns"] if c["name"] == "customer_id")
    assert note == "references customers_2(id)"


def test_schema_only_dump_creates_empty_tables(tmp_path: Path, db: Path) -> None:
    result = import_file(db, sql_file(tmp_path, "CREATE TABLE t (a int, b text);"), "s.sql")
    assert result.tables == ["t"]
    assert list_tables(db)[0]["rows"] == 0


def test_dump_without_tables_is_refused(tmp_path: Path, db: Path) -> None:
    with pytest.raises(UploadError, match="no CREATE TABLE"):
        import_file(db, sql_file(tmp_path, "SELECT 1;"), "s.sql")


def test_dump_with_too_many_tables_is_refused(tmp_path: Path, db: Path) -> None:
    text = "".join(f"CREATE TABLE t{i} (a int);" for i in range(MAX_TABLES + 1))
    with pytest.raises(UploadError, match=f"has {MAX_TABLES + 1} tables; you can keep at most"):
        import_file(db, sql_file(tmp_path, text), "big.sql")
    assert list_tables(db) == []


def test_room_for_a_whole_sample_database() -> None:
    assert MAX_TABLES >= 100  # AdventureWorks for Postgres has 68 tables


def test_dump_that_is_not_utf8_still_loads(tmp_path: Path, db: Path) -> None:
    path = tmp_path / "latin1.sql"
    path.write_bytes("CREATE TABLE t (a text); INSERT INTO t VALUES ('caf\xe9');".encode("latin-1"))
    assert import_file(db, path, "latin1.sql").tables == ["t"]


def test_csv_and_excel_report_nothing_skipped(tmp_path: Path, db: Path) -> None:
    assert import_file(db, csv_file(tmp_path, "a\n1\n"), "t.csv").skipped == {}
