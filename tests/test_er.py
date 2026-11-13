"""ER designer: the text format, ER -> DDL in five dialects, and DDL -> ER."""

import re

import duckdb
import pytest
import sqlglot

from schemalab.er import DIALECTS, ERError, from_ddl, parse, to_ddl, to_text

UNIVERSITY = """
# A small university
entity Professor { pid int key, name text }
entity Student { sid int key, name text, gpa decimal(3,2) }
entity Course { cid varchar(10) key, title text, credits int }
entity Department { dname text key, building text }

relationship Advises: Professor 1 -- N Student
relationship Enrolls: Student M -- N Course! { grade text }
relationship Heads: Professor 1 -- 1 Department!
"""


def tables(sql: str) -> dict[str, str]:
    """CREATE TABLE statements by table name."""
    return {m[1]: m[0] for m in re.finditer(r"CREATE TABLE (\w+) \((?:[^;])*\)", sql)}


# The text format


def test_parse_entities_keys_and_relationships() -> None:
    model = parse(UNIVERSITY)
    assert [e.name for e in model.entities] == ["Professor", "Student", "Course", "Department"]
    student = model.entities[1]
    assert [(a.name, a.type, a.key) for a in student.attributes] == [
        ("sid", "INT", True),
        ("name", "TEXT", False),
        ("gpa", "DECIMAL(3, 2)", False),
    ]
    advises, enrolls, heads = model.relationships
    assert (advises.kind, enrolls.kind, heads.kind) == ("1:N", "M:N", "1:1")
    assert enrolls.right.total and not enrolls.left.total
    assert [a.name for a in enrolls.attributes] == ["grade"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("entity A { x int }", "line 1: A has no key attribute; mark one with key"),
        ("entity A { x int key }\nentity A { y int key }", "line 2: A is declared twice"),
        ("entity A { x money key }", "line 1: money is not a supported type"),
        ("entity A { x int key }\nrelationship R: A 1 -- N B", "line 2: B is not an entity"),
        ("entity A { x int key }\nrelationship R: A 2 -- N A", "line 2: write a relationship as"),
        ("table A { x int key }", "line 1: start a line with entity, relationship or isa"),
        ("", "Describe at least one entity"),
    ],
)
def test_parse_errors_say_what_to_fix(text: str, message: str) -> None:
    with pytest.raises(ERError, match=re.escape(message)):
        parse(text)


# ER -> DDL


def test_one_to_many_puts_the_foreign_key_on_the_many_side() -> None:
    student = tables(to_ddl(parse(UNIVERSITY), "postgres"))["student"]
    assert "professor_pid INT" in student
    assert "FOREIGN KEY (professor_pid) REFERENCES professor (pid)" in student
    assert "professor_pid INT NOT NULL" not in student  # a student may have no advisor


def test_many_to_many_becomes_a_junction_table() -> None:
    enrolls = tables(to_ddl(parse(UNIVERSITY), "postgres"))["enrolls"]
    assert "student_sid INT NOT NULL" in enrolls
    assert "course_cid VARCHAR(10) NOT NULL" in enrolls
    assert "grade TEXT" in enrolls
    assert "PRIMARY KEY (student_sid, course_cid)" in enrolls


def test_one_to_one_puts_a_unique_foreign_key_on_the_total_side() -> None:
    department = tables(to_ddl(parse(UNIVERSITY), "postgres"))["department"]
    # Text keys become VARCHAR(255): MySQL cannot index an unbounded TEXT key.
    assert "dname VARCHAR(255) NOT NULL" in department
    assert "professor_pid INT NOT NULL UNIQUE" in department
    assert "FOREIGN KEY (professor_pid) REFERENCES professor (pid)" in department


def test_tables_come_before_the_tables_that_reference_them() -> None:
    sql = to_ddl(parse(UNIVERSITY), "postgres")
    order = list(tables(sql))
    assert order.index("professor") < order.index("student") < order.index("enrolls")
    assert order.index("course") < order.index("enrolls")


def test_generated_ddl_runs() -> None:
    con = duckdb.connect()
    for statement in sqlglot.transpile(
        to_ddl(parse(UNIVERSITY), "postgres"), read="postgres", write="duckdb"
    ):
        con.execute(statement)
    assert len(con.execute("SHOW TABLES").fetchall()) == 5


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [
        ("mysql", "dname VARCHAR(255) NOT NULL"),
        ("sqlite", "sid INTEGER"),
        ("tsql", "name VARCHAR(MAX)"),
        ("oracle", "cid VARCHAR2(10)"),
        ("postgres", "gpa DECIMAL(3, 2)"),
    ],
)
def test_five_dialects_parse_back(dialect: str, expected: str) -> None:
    assert set(DIALECTS) == {"postgres", "mysql", "sqlite", "tsql", "oracle"}
    sql = to_ddl(parse(UNIVERSITY), dialect)
    assert expected in sql
    assert len(sqlglot.parse(sql, read=dialect)) == 5


def test_self_relationship_gets_distinct_column_names() -> None:
    text = (
        "entity Employee { eid int key, name text }\nrelationship Manages: Employee 1 -- N Employee"
    )
    employee = tables(to_ddl(parse(text), "postgres"))["employee"]
    assert "manages_eid INT" in employee
    assert "FOREIGN KEY (manages_eid) REFERENCES employee (eid)" in employee


# DDL -> ER


DDL = """
CREATE TABLE professor (pid INT PRIMARY KEY, name TEXT);
CREATE TABLE department (dname VARCHAR(255) PRIMARY KEY,
  head INT NOT NULL UNIQUE REFERENCES professor(pid));
CREATE TABLE student (sid INT PRIMARY KEY, name TEXT,
  advisor INT REFERENCES professor(pid));
CREATE TABLE course (cid VARCHAR(10) PRIMARY KEY, title TEXT);
CREATE TABLE enrolls (sid INT REFERENCES student(sid), cid VARCHAR(10) REFERENCES course(cid),
  grade TEXT, PRIMARY KEY (sid, cid));
"""


def test_from_ddl_finds_entities_and_hides_foreign_key_columns() -> None:
    model = from_ddl(DDL)
    assert [e.name for e in model.entities] == ["Professor", "Department", "Student", "Course"]
    student = next(e for e in model.entities if e.name == "Student")
    assert [a.name for a in student.attributes] == ["sid", "name"]


def test_from_ddl_reads_relationship_kinds() -> None:
    by_name = {r.name: r for r in from_ddl(DDL).relationships}
    head = by_name["DepartmentHead"]
    assert head.kind == "1:1" and head.right.entity == "Department" and head.right.total
    advisor = by_name["StudentAdvisor"]
    assert advisor.kind == "1:N" and advisor.left.entity == "Professor"
    assert advisor.right.entity == "Student" and not advisor.right.total
    enrolls = by_name["Enrolls"]
    assert enrolls.kind == "M:N" and [a.name for a in enrolls.attributes] == ["grade"]


def test_from_ddl_refuses_text_without_tables() -> None:
    with pytest.raises(ERError, match="no CREATE TABLE"):
        from_ddl("SELECT 1")


def test_round_trip_through_ddl_keeps_the_design() -> None:
    model = parse(UNIVERSITY)
    again = from_ddl(to_ddl(model, "postgres"))
    assert [e.name for e in again.entities] == [e.name for e in model.entities] + []
    assert sorted((r.kind, r.left.entity, r.right.entity) for r in again.relationships) == sorted(
        (r.kind, r.left.entity, r.right.entity) for r in model.relationships
    )
    assert parse(to_text(again)) == again  # the text form reads back to the same model


def test_relationship_names_from_foreign_keys_drop_the_key_suffix() -> None:
    names = [r.name for r in from_ddl(to_ddl(parse(UNIVERSITY), "postgres")).relationships]
    assert "StudentProfessor" in names and "DepartmentProfessor" in names


def test_a_table_without_a_key_linking_two_tables_is_many_to_many() -> None:
    ddl = """
    CREATE TABLE students (id INT PRIMARY KEY, name TEXT);
    CREATE TABLE courses (code VARCHAR(10) PRIMARY KEY, title TEXT);
    CREATE TABLE enrollments (student_id INT REFERENCES students(id),
      course_code VARCHAR(10) REFERENCES courses(code), grade DECIMAL(3, 1));
    """
    model = from_ddl(ddl)
    assert [e.name for e in model.entities] == ["Students", "Courses"]
    [rel] = model.relationships
    assert rel.kind == "M:N" and rel.name == "Enrollments"
    assert [a.name for a in rel.attributes] == ["grade"]


def test_a_table_without_a_key_uses_its_first_own_column() -> None:
    ddl = """
    CREATE TABLE teams (id INT PRIMARY KEY);
    CREATE TABLE players (team_id INT REFERENCES teams(id), name TEXT, age INT);
    """
    players = from_ddl(ddl).entities[1]
    assert [(a.name, a.key) for a in players.attributes] == [("name", True), ("age", False)]


PG_DUMP = r"""
SET statement_timeout = 0;
CREATE FUNCTION public.touch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated = now(); RETURN NEW; END; $$;
CREATE TABLE public.customers (id integer NOT NULL, name text);
CREATE TABLE public.orders (id integer NOT NULL, customer_id integer NOT NULL, total numeric(8,2));
COPY public.customers (id, name) FROM stdin;
1	Ada
\.
ALTER TABLE ONLY public.customers ADD CONSTRAINT customers_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.orders ADD CONSTRAINT orders_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES public.customers(id);
"""


def test_from_ddl_reads_a_pg_dump_with_keys_added_later() -> None:
    model = from_ddl(PG_DUMP)
    customers, orders = model.entities
    assert [(a.name, a.key) for a in customers.attributes] == [("id", True), ("name", False)]
    assert [a.name for a in orders.attributes] == ["id", "total"]  # the FK column is a link
    [rel] = model.relationships
    assert (rel.kind, rel.left.entity, rel.right.entity, rel.right.total) == (
        "1:N",
        "Customers",
        "Orders",
        True,
    )


def test_from_ddl_reads_a_mysqldump() -> None:
    dump = """
    DROP TABLE IF EXISTS `items`;
    CREATE TABLE `items` (
      `id` int NOT NULL AUTO_INCREMENT,
      `shelf_id` int DEFAULT NULL,
      `label` varchar(40) DEFAULT NULL,
      PRIMARY KEY (`id`),
      KEY `shelf_idx` (`shelf_id`),
      CONSTRAINT `items_shelf` FOREIGN KEY (`shelf_id`) REFERENCES `shelves` (`id`)
    ) ENGINE=InnoDB;
    CREATE TABLE `shelves` (`id` int NOT NULL, PRIMARY KEY (`id`)) ENGINE=InnoDB;
    LOCK TABLES `items` WRITE;
    INSERT INTO `items` VALUES (1,NULL,'it\\'s');
    UNLOCK TABLES;
    """
    model = from_ddl(dump)
    assert [e.name for e in model.entities] == ["Items", "Shelves"]
    [rel] = model.relationships
    assert (rel.kind, rel.left.entity, rel.right.entity) == ("1:N", "Shelves", "Items")


def test_a_reserved_word_as_an_entity_is_quoted_in_every_dialect() -> None:
    model = parse(
        "entity Order { id int key }\nentity Item { id int key }\n"
        "relationship Has: Order 1 -- N Item"
    )
    for dialect in DIALECTS:
        ddl = to_ddl(model, dialect)
        assert sqlglot.parse(ddl, read=dialect)  # parses back in its own dialect
