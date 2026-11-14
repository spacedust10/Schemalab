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


# The builder's checks


def test_a_sound_assignment_has_no_errors(tmp_path: Path) -> None:
    from schemalab.assignments import check

    assert check(COURSES, tmp_path) == {"errors": [], "warnings": []}


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"schema": COURSES.schema + "DROP TABLE grades;"}, "Only CREATE TABLE and INSERT"),
        ({"schema": "CREATE TABLE Students (ID INT, name TEXT);"}, "lowercase"),
        ({"reference": "SELECT nme FROM students"}, "reference query fails"),
        ({"reference": "DELETE FROM students"}, "reference query fails"),
        ({"ordered": True}, "ORDER BY"),
    ],
)
def test_a_broken_assignment_is_refused_with_the_reason(
    tmp_path: Path, change: dict[str, object], error: str
) -> None:
    from dataclasses import replace

    from schemalab.assignments import check

    got = check(replace(COURSES, **change), tmp_path)  # type: ignore[arg-type]
    assert any(error in e for e in got["errors"]), got


def test_weak_hidden_tests_get_warnings(tmp_path: Path) -> None:
    from dataclasses import replace

    from schemalab.assignments import check

    assert any("hidden" in w for w in check(replace(COURSES, hidden=[]), tmp_path)["warnings"])
    empty = replace(COURSES, hidden=[("Nobody", "INSERT INTO grades VALUES (9, 'DB', 1);")])
    assert any("no rows" in w for w in check(empty, tmp_path)["warnings"])
    unordered = replace(COURSES, reference=COURSES.reference + " ORDER BY courses")
    assert any("order" in w.lower() for w in check(unordered, tmp_path)["warnings"])


def test_the_sample_assignments_are_sound(tmp_path: Path) -> None:
    from schemalab.assignments import SAMPLES, check

    assert len(SAMPLES) >= 3
    for a in SAMPLES.values():
        assert check(a, tmp_path)["errors"] == [], a.title
        assert grade(a, a.reference, "exam", tmp_path)["score"] == 100


@pytest.mark.parametrize(
    ("assignment", "wrong"),
    [
        ("courses-per-student", INNER_JOIN),
        # LIMIT 1 per department drops a tie.
        (
            "top-paid",
            "SELECT dept, name, salary FROM (SELECT *, row_number() OVER "
            "(PARTITION BY dept ORDER BY salary DESC) AS r FROM employees) t WHERE r = 1",
        ),
        # count(email) never counts a NULL; "= NULL" is never true.
        (
            "missing-emails",
            "SELECT count(*) - count(email) AS no_email FROM customers WHERE id < 4",
        ),
    ],
)
def test_each_sample_catches_a_typical_wrong_answer_only_in_hidden_tests(
    tmp_path: Path, assignment: str, wrong: str
) -> None:
    from schemalab.assignments import SAMPLES

    got = grade(SAMPLES[assignment], wrong, "exam", tmp_path)
    assert got["tests"][0]["passed"] is True  # it looks right on the sample
    assert not all(t["passed"] for t in got["tests"][1:])  # a hidden test catches it


@pytest.mark.parametrize("mode", ["Exam", "", "teacher"])
def test_an_unknown_mode_is_refused_rather_than_shown_hidden_rows(
    tmp_path: Path, mode: str
) -> None:
    with pytest.raises(ValueError, match="mode"):
        grade(COURSES, INNER_JOIN, mode, tmp_path)  # type: ignore[arg-type]
