"""Relational algebra: parse, translate to SQL and back, and draw as a tree."""

import re

import duckdb
import pytest

from schemalab.ra import RAError, from_sql, parse_ra, to_ra, to_sql, tree


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    c.execute("CREATE TABLE student (sid INT, name TEXT, gpa DOUBLE)")
    c.execute("INSERT INTO student VALUES (1, 'Ada', 3.9), (2, 'Bob', 3.1), (3, 'Cy', 3.6)")
    c.execute("CREATE TABLE enrolls (sid INT, cid TEXT, grade TEXT)")
    c.execute(
        "INSERT INTO enrolls VALUES (1, 'CIS545', 'A'), (1, 'CIS501', 'B'), (3, 'CIS545', 'A')"
    )
    c.execute("CREATE TABLE course (cid TEXT, title TEXT)")
    c.execute("INSERT INTO course VALUES ('CIS545', 'Security'), ('CIS501', 'Databases')")
    return c


def run(con: duckdb.DuckDBPyConnection, ra: str) -> set[tuple[object, ...]]:
    return set(con.execute(to_sql(parse_ra(ra))).fetchall())


# Parsing


def test_unicode_and_ascii_forms_parse_the_same() -> None:
    unicode = parse_ra("π[name](σ[gpa > 3.5](student))")
    ascii_ = parse_ra("project[name](select[gpa > 3.5](student))")
    assert unicode == ascii_
    assert unicode.op == "project" and unicode.children[0].op == "select"


def test_joins_bind_tighter_than_set_operators() -> None:
    node = parse_ra("student ⋈ enrolls ∪ student ⋈ enrolls")
    assert node.op == "union"
    assert [c.op for c in node.children] == ["join", "join"]


def test_condition_symbols_become_sql() -> None:
    node = parse_ra("σ[gpa ≥ 3.5 ∧ name ≠ 'Bob' ∨ ¬(sid ≤ 1)](student)")
    assert node.arg == "gpa >= 3.5 AND name <> 'Bob' OR NOT (sid <= 1)"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("π[name](student", "expected ) at position 16"),
        ("σ[](student)", "the condition is empty"),
        ("σ[gpa > (SELECT 1)](student)", "conditions cannot contain subqueries"),
        ("student ⋈", "expected a relation or an operator at position 10"),
        ("student @ enrolls", "unexpected @ at position 9"),
        ("", "Write a relational algebra expression"),
    ],
)
def test_parse_errors_point_at_the_problem(text: str, message: str) -> None:
    with pytest.raises(RAError, match=re.escape(message)):
        parse_ra(text)


# RA -> SQL, checked by running it


def test_select_project(con: duckdb.DuckDBPyConnection) -> None:
    assert run(con, "π[name](σ[gpa > 3.5](student))") == {("Ada",), ("Cy",)}


def test_projection_removes_duplicates(con: duckdb.DuckDBPyConnection) -> None:
    assert run(con, "π[grade](enrolls)") == {("A",), ("B",)}
    assert len(con.execute(to_sql(parse_ra("π[grade](enrolls)"))).fetchall()) == 2


def test_natural_join_and_theta_join(con: duckdb.DuckDBPyConnection) -> None:
    natural = run(con, "π[name, title](student ⋈ enrolls ⋈ course)")
    assert natural == {("Ada", "Security"), ("Ada", "Databases"), ("Cy", "Security")}
    theta = run(con, "π[name](student ⋈[student.sid = enrolls.sid ∧ grade = 'B'] enrolls)")
    assert theta == {("Ada",)}


def test_select_over_a_join_is_one_query() -> None:
    sql = to_sql(parse_ra("σ[grade = 'A'](student ⋈[student.sid = enrolls.sid] enrolls)"))
    assert sql.count("SELECT") == 1 and "WHERE" in sql


def test_set_operators(con: duckdb.DuckDBPyConnection) -> None:
    all_ids = "π[sid](student)"
    enrolled = "π[sid](enrolls)"
    assert run(con, f"{all_ids} − {enrolled}") == {(2,)}
    assert run(con, f"{all_ids} ∩ {enrolled}") == {(1,), (3,)}
    assert run(con, f"{enrolled} ∪ π[sid](σ[sid = 2](student))") == {(1,), (2,), (3,)}


def test_rename_and_cross_product(con: duckdb.DuckDBPyConnection) -> None:
    pairs = run(con, "π[a.name, b.name](σ[a.gpa > b.gpa](ρ[a](student) × ρ[b](student)))")
    assert ("Ada", "Bob") in pairs and len(pairs) == 3


def test_rename_with_column_names(con: duckdb.DuckDBPyConnection) -> None:
    assert run(con, "π[who](ρ[s(id, who, score)](student))") == {("Ada",), ("Bob",), ("Cy",)}


def test_grouping(con: duckdb.DuckDBPyConnection) -> None:
    assert run(con, "γ[cid; count(*) AS n](enrolls)") == {("CIS545", 2), ("CIS501", 1)}


# SQL -> RA


def test_sql_to_ra_select_project() -> None:
    result = from_sql("SELECT DISTINCT name FROM student WHERE gpa > 3.5")
    assert to_ra(result.node) == "π[name](σ[gpa > 3.5](student))"
    assert result.notes == []


def test_sql_to_ra_notes_duplicate_handling() -> None:
    result = from_sql("SELECT name FROM student")
    assert any("duplicates" in n for n in result.notes)


def test_sql_to_ra_joins_group_and_order() -> None:
    sql = (
        "SELECT c.title, count(*) AS n FROM enrolls e JOIN course c ON e.cid = c.cid "
        "GROUP BY c.title ORDER BY n DESC LIMIT 5"
    )
    result = from_sql(sql)
    assert to_ra(result.node) == (
        "τ[n DESC](γ[c.title; COUNT(*) AS n](ρ[e](enrolls) ⋈[e.cid = c.cid] ρ[c](course)))"
    )
    assert any("LIMIT" in n for n in result.notes)


def test_sql_to_ra_set_operations_and_cross_joins() -> None:
    assert to_ra(from_sql("SELECT sid FROM student EXCEPT SELECT sid FROM enrolls").node) == (
        "π[sid](student) − π[sid](enrolls)"
    )
    assert to_ra(from_sql("SELECT * FROM student, course").node) == "student × course"


def test_sql_with_a_subquery_in_where_is_refused() -> None:
    with pytest.raises(RAError, match="subquery in WHERE"):
        from_sql("SELECT name FROM student WHERE sid IN (SELECT sid FROM enrolls)")


def test_sql_and_ra_round_trip_to_the_same_rows(con: duckdb.DuckDBPyConnection) -> None:
    sql = (
        "SELECT DISTINCT s.name FROM student s JOIN enrolls e ON s.sid = e.sid WHERE e.grade = 'A'"
    )
    ra = to_ra(from_sql(sql).node)
    assert set(con.execute(sql).fetchall()) == run(con, ra)


# The tree view


def test_tree_labels_operators_and_leaves() -> None:
    t = tree(parse_ra("π[name](σ[gpa > 3.5](student))"))
    assert t == {
        "op": "project",
        "label": "π name",
        "children": [
            {
                "op": "select",
                "label": "σ gpa > 3.5",
                "children": [{"op": "relation", "label": "student", "children": []}],
            }
        ],
    }
