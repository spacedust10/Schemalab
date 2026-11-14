"""Grading SQL answers on the visible data and on hidden databases, with partial credit."""

from pathlib import Path

import pytest

from schemalab.assignments import Assignment, grade

COURSES = Assignment(
    title="Courses per student",
    prompt="For every student, show their name and how many courses they have taken (0 if none).",
    schema="CREATE TABLE students (id INT PRIMARY KEY, name TEXT);"
    "CREATE TABLE grades (student_id INT, course TEXT, grade INT);",
    data="INSERT INTO students VALUES (1, 'Ada'), (2, 'Alan');"
    "INSERT INTO grades VALUES (1, 'DB', 90), (1, 'AI', 85), (2, 'DB', 70);",
    reference="SELECT s.name, count(g.course) AS courses FROM students s "
    "LEFT JOIN grades g ON g.student_id = s.id GROUP BY s.id, s.name",
    hidden=[
        (
            "A student with no courses",
            "INSERT INTO students VALUES (1, 'Ada'), (3, 'Grace');"
            "INSERT INTO grades VALUES (1, 'DB', 90);",
        ),
        (
            "Two students with the same name",
            "INSERT INTO students VALUES (1, 'Sam'), (2, 'Sam');"
            "INSERT INTO grades VALUES (1, 'DB', 90), (2, 'AI', 80), (2, 'DB', 75);",
        ),
    ],
)

RIGHT = COURSES.reference
INNER_JOIN = (
    "SELECT s.name, count(*) AS courses FROM students s JOIN grades g ON g.student_id = s.id "
    "GROUP BY s.id, s.name"
)
COUNT_STAR = (
    "SELECT s.name, count(*) AS courses FROM students s LEFT JOIN grades g "
    "ON g.student_id = s.id GROUP BY s.id, s.name"
)
BY_NAME = (
    "SELECT s.name, count(g.course) AS courses FROM students s LEFT JOIN grades g "
    "ON g.student_id = s.id GROUP BY s.name"
)


def test_the_right_answer_scores_full_marks(tmp_path: Path) -> None:
    got = grade(COURSES, RIGHT, "tutor", tmp_path)
    assert got["score"] == 100 and all(t["passed"] for t in got["tests"])
    assert [t["name"] for t in got["tests"]] == [
        "Sample data",
        "A student with no courses",
        "Two students with the same name",
    ]


@pytest.mark.parametrize("sql", [INNER_JOIN, COUNT_STAR])
def test_missing_the_student_without_courses_loses_one_hidden_test(
    tmp_path: Path, sql: str
) -> None:
    got = grade(COURSES, sql, "tutor", tmp_path)
    assert {t["name"]: t["passed"] for t in got["tests"]} == {
        "Sample data": True,
        "A student with no courses": False,
        "Two students with the same name": True,
    }
    assert got["score"] == round(10 + 20 + 70 * 2 / 3)


def test_grouping_by_name_merges_two_students(tmp_path: Path) -> None:
    got = grade(COURSES, BY_NAME, "tutor", tmp_path)
    failed = [t["name"] for t in got["tests"] if not t["passed"]]
    assert failed == ["Two students with the same name"]


def test_tutor_mode_shows_a_hint_and_a_counterexample(tmp_path: Path) -> None:
    got = grade(COURSES, INNER_JOIN, "tutor", tmp_path)
    failing = next(t for t in got["tests"] if not t["passed"])
    assert "fewer rows" in failing["hint"]
    assert failing["missing"] == [["Grace", 0]]


def test_exam_mode_names_failed_tests_but_shows_no_hidden_rows(tmp_path: Path) -> None:
    got = grade(COURSES, INNER_JOIN, "exam", tmp_path)
    failing = next(t for t in got["tests"] if not t["passed"])
    assert failing == {"name": "A student with no courses", "passed": False}
    assert "Grace" not in str(got)


def test_wrong_columns_get_partial_credit(tmp_path: Path) -> None:
    got = grade(COURSES, "SELECT s.name FROM students s", "tutor", tmp_path)
    assert got["parts"]["columns"] == 0 and got["parts"]["runs"] == 10
    assert got["score"] == 10


def test_a_query_that_fails_scores_zero_and_is_explained(tmp_path: Path) -> None:
    got = grade(COURSES, "SELECT nme FROM students", "tutor", tmp_path)
    assert got["score"] == 0 and got["error"]["title"].startswith("No such column")
    assert 'did you mean "name"' in got["error"]["fix"].lower()


def test_only_a_select_is_graded(tmp_path: Path) -> None:
    got = grade(COURSES, "DELETE FROM students", "tutor", tmp_path)
    assert got["score"] == 0 and "only queries" in got["error"]["title"].lower()


def test_order_counts_only_when_the_assignment_asks_for_it(tmp_path: Path) -> None:
    ordered = COURSES.with_order("SELECT name FROM students ORDER BY name DESC")
    got = grade(ordered, "SELECT name FROM students ORDER BY name", "tutor", tmp_path)
    assert got["tests"][0]["passed"] is False and "order" in got["tests"][0]["hint"]
    unordered = grade(COURSES, "SELECT s.name, 1 FROM students s", "tutor", tmp_path)
    assert unordered["parts"]["runs"] == 10
