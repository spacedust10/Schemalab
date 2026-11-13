"""Reading .sql dumps: tables, rows and keys come from the parsed SQL; nothing in it is run."""

import csv
from pathlib import Path

import pytest

from schemalab.sqldump import DumpError, read_dump

PG_DUMP = r"""
--
-- PostgreSQL database dump
--
SET statement_timeout = 0;
SELECT pg_catalog.set_config('search_path', '', false);

CREATE FUNCTION public.touch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated = now(); RETURN NEW; END; $$;

CREATE TABLE public.customers (
    id integer NOT NULL,
    "Full Name" text,
    signed_up date DEFAULT now()
);

CREATE TABLE public.orders (
    id serial NOT NULL,
    customer_id integer REFERENCES public.customers(id),
    total numeric(10,2),
    status public.order_status
);

COPY public.customers (id, "Full Name", signed_up) FROM stdin;
1	Ada Lovelace	2024-01-02
2	Bob\tTabs	\N
\.

COPY public.orders (id, customer_id, total, status) FROM stdin;
10	1	19.50	paid
11	2	\N	open
\.

ALTER TABLE ONLY public.customers ADD CONSTRAINT customers_pkey PRIMARY KEY (id);
GRANT ALL ON TABLE public.orders TO analyst;
"""

MYSQL_DUMP = r"""
/*!40101 SET NAMES utf8mb4 */;
DROP TABLE IF EXISTS `items`;
CREATE TABLE `items` (
  `id` int NOT NULL AUTO_INCREMENT,
  `label` varchar(40) DEFAULT NULL,
  `price` decimal(8,2),
  PRIMARY KEY (`id`),
  KEY `label_idx` (`label`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
LOCK TABLES `items` WRITE;
INSERT INTO `items` VALUES (1,'it\'s',2.50),(2,NULL,-3.00);
UNLOCK TABLES;
"""

SQLITE_DUMP = """
PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT, done BOOLEAN);
INSERT INTO notes VALUES(1,'first',TRUE);
INSERT INTO notes (body, id) VALUES ('second', 2);
COMMIT;
"""


def rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as f:
        return list(csv.reader(f))


def test_pg_dump_tables_types_and_copy_rows(tmp_path: Path) -> None:
    dump = read_dump(PG_DUMP, tmp_path)
    assert [t.name for t in dump.tables] == ["customers", "orders"]
    customers, orders = dump.tables
    assert customers.columns == [("id", "INT"), ("full_name", "TEXT"), ("signed_up", "DATE")]
    # serial and user-defined enum types fall back to types DuckDB has.
    assert orders.columns == [
        ("id", "INT"),
        ("customer_id", "INT"),
        ("total", "DECIMAL(10, 2)"),
        ("status", "VARCHAR"),
    ]
    assert rows(customers.csv) == [
        ["id", "full_name", "signed_up"],
        ["1", "Ada Lovelace", "2024-01-02"],
        ["2", "Bob\tTabs", r"\N"],
    ]
    assert customers.row_count == 2 and orders.row_count == 2


def test_keys_come_from_inline_references_and_alter_table(tmp_path: Path) -> None:
    dump = read_dump(PG_DUMP, tmp_path)
    assert dump.keys == {
        ("customers", "id"): "primary key",
        ("orders", "customer_id"): "references customers(id)",
    }


def test_everything_else_is_skipped_and_counted(tmp_path: Path) -> None:
    dump = read_dump(PG_DUMP, tmp_path)
    assert dump.skipped == {"SET": 1, "SELECT": 1, "CREATE FUNCTION": 1, "GRANT": 1}


def test_mysql_dump_with_backticks_and_backslash_escapes(tmp_path: Path) -> None:
    dump = read_dump(MYSQL_DUMP, tmp_path)
    [items] = dump.tables
    assert items.name == "items"
    assert items.columns == [("id", "INT"), ("label", "VARCHAR(40)"), ("price", "DECIMAL(8, 2)")]
    assert rows(items.csv)[1:] == [["1", "it's", "2.50"], ["2", r"\N", "-3.00"]]
    assert dump.keys == {("items", "id"): "primary key"}
    assert dump.skipped["DROP TABLE"] == 1


def test_sqlite_dump_with_and_without_column_lists(tmp_path: Path) -> None:
    dump = read_dump(SQLITE_DUMP, tmp_path)
    [notes] = dump.tables
    assert rows(notes.csv)[1:] == [["1", "first", "true"], ["2", "second", r"\N"]]
    assert dump.keys == {("notes", "id"): "primary key"}


def test_insert_with_a_query_instead_of_values_is_skipped(tmp_path: Path) -> None:
    text = "CREATE TABLE t (a int); INSERT INTO t SELECT * FROM read_csv('/etc/passwd');"
    dump = read_dump(text, tmp_path)
    assert dump.tables[0].row_count == 0
    assert dump.skipped == {"INSERT ... SELECT": 1}


def test_values_that_are_not_constants_are_refused_not_run(tmp_path: Path) -> None:
    text = "CREATE TABLE t (a text); INSERT INTO t VALUES ((SELECT current_setting('x')));"
    dump = read_dump(text, tmp_path)
    assert dump.tables[0].row_count == 0
    assert dump.skipped == {"INSERT with expressions": 1}


def test_rows_for_unknown_tables_are_skipped(tmp_path: Path) -> None:
    dump = read_dump("CREATE TABLE real (a int); INSERT INTO ghost VALUES (1);", tmp_path)
    assert dump.tables[0].row_count == 0
    assert dump.skipped == {"INSERT into a table the file never creates": 1}


def test_a_file_without_tables_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DumpError, match="no CREATE TABLE"):
        read_dump("SELECT 1; SET x = 1;", tmp_path)


def test_unreadable_sql_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DumpError, match="Could not read"):
        read_dump("CREATE TABLE t (a int); INSERT INTO t VALUES ('unterminated", tmp_path)


def test_table_and_column_names_become_safe_identifiers(tmp_path: Path) -> None:
    text = (
        'CREATE TABLE "Sales Data" ("Order ID" int, "order id" int);'
        'INSERT INTO "Sales Data" VALUES (1, 2);'
    )
    [table] = read_dump(text, tmp_path).tables
    assert table.name == "sales_data"
    assert [c for c, _ in table.columns] == ["order_id", "order_id_2"]


SCHEMAS_SQL = (Path(__file__).parent / "fixtures" / "schemas.sql").read_text()


def test_tables_nested_in_create_schema_are_read(tmp_path: Path) -> None:
    dump = read_dump(SCHEMAS_SQL, tmp_path)
    names = [t.name for t in dump.tables]
    # Two schemas each have a Customer table; the second keeps its schema in its name.
    assert names == ["businessentity", "person", "customer", "crm_customer"]
    person = dump.tables[1]
    assert person.columns == [
        ("businessentityid", "INT"),
        ("firstname", "VARCHAR(50)"),  # the "Name" domain
        ("active", "BOOLEAN"),
    ]
    assert dump.tables[2].columns[2] == ("note", "VARCHAR(20)")


def test_keys_added_later_resolve_across_schemas(tmp_path: Path) -> None:
    keys = read_dump(SCHEMAS_SQL, tmp_path).keys
    # Both facts about a column that is the key and a foreign key.
    assert keys[("person", "businessentityid")] == (
        "primary key; references businessentity(businessentityid)"
    )
    assert keys[("customer", "personid")] == "references person(businessentityid)"
    assert keys[("crm_customer", "salescustomerid")] == "references customer(customerid)"


def test_psql_copy_from_files_is_counted_so_the_user_knows_rows_are_missing(
    tmp_path: Path,
) -> None:
    dump = read_dump(SCHEMAS_SQL, tmp_path)
    assert all(t.row_count == 0 for t in dump.tables)
    assert dump.skipped[r"\copy from a CSV file not in this upload"] == 2


def test_an_explicit_null_is_not_recorded_as_not_null(tmp_path: Path) -> None:
    dump = read_dump(
        "CREATE TABLE t (id INT NOT NULL PRIMARY KEY, title VARCHAR(8) NULL);", tmp_path
    )
    assert dump.tables[0].not_null == ["id"]


def test_columns_added_and_dropped_before_rows_are_applied(tmp_path: Path) -> None:
    dump = read_dump(
        "CREATE TABLE doc (old INT, title TEXT, level INT);"
        "ALTER TABLE doc DROP COLUMN level;"
        "ALTER TABLE doc ADD node VARCHAR(10) DEFAULT '/';"
        "ALTER TABLE doc ADD CONSTRAINT pk PRIMARY KEY (node);"
        "INSERT INTO doc (old, title, node) VALUES (1, 'a', '/1/');",
        tmp_path,
    )
    [doc] = dump.tables
    assert [c for c, _ in doc.columns] == ["old", "title", "node"]
    assert doc.primary == ["node"] and doc.row_count == 1
    with doc.csv.open() as f:
        assert list(csv.reader(f)) == [["old", "title", "node"], ["1", "a", "/1/"]]


def test_a_column_change_after_rows_is_skipped_and_counted(tmp_path: Path) -> None:
    dump = read_dump(
        "CREATE TABLE t (a INT); INSERT INTO t VALUES (1); ALTER TABLE t ADD b INT;", tmp_path
    )
    assert [c for c, _ in dump.tables[0].columns] == ["a"]
    assert dump.skipped["ALTER TABLE column change after its rows"] == 1


def test_mysql_display_widths_keep_integer_types(tmp_path: Path) -> None:
    dump = read_dump(
        "CREATE TABLE `orders` (`id` int(11) NOT NULL AUTO_INCREMENT, `n` bigint(20) unsigned,"
        " `flag` tinyint(1), `total` decimal(10,2), PRIMARY KEY (`id`)) ENGINE=InnoDB;",
        tmp_path,
    )
    assert dump.tables[0].columns == [
        ("id", "INT"),
        ("n", "UBIGINT"),
        ("flag", "TINYINT"),
        ("total", "DECIMAL(10, 2)"),
    ]


def test_a_sql_server_script_loads(tmp_path: Path) -> None:
    dump = read_dump(
        "CREATE TABLE [dbo].[People] ([Id] INT NOT NULL PRIMARY KEY, [Name] NVARCHAR(50));\n"
        "GO\n"
        "INSERT INTO [dbo].[People] ([Id], [Name]) VALUES (1, N'Ada');\n"
        "GO\n",
        tmp_path,
    )
    [people] = dump.tables
    assert people.name == "people" and people.row_count == 1
    with people.csv.open() as f:
        assert list(csv.reader(f))[1] == ["1", "Ada"]


def test_a_table_dropped_and_created_again_keeps_its_name(tmp_path: Path) -> None:
    dump = read_dump(
        "DROP TABLE IF EXISTS t; CREATE TABLE t (a int); INSERT INTO t VALUES (1);"
        "DROP TABLE IF EXISTS t; CREATE TABLE t (a int, b int); INSERT INTO t VALUES (1, 2);",
        tmp_path,
    )
    [t] = dump.tables
    assert t.name == "t" and [c for c, _ in t.columns] == ["a", "b"] and t.row_count == 1
