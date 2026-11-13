"""Normalization engine, checked against textbook examples."""

import re

import pytest

from schemalab.normalize import (
    FD,
    NormalizeError,
    bcnf_decompose,
    candidate_keys,
    chase,
    closure,
    minimal_cover,
    normal_form,
    parse,
    preserved,
    synthesize_3nf,
)


def fds(text: str, attrs: str) -> list[FD]:
    return parse(f"R({attrs})", text)[1]


def s(text: str) -> frozenset[str]:
    return frozenset(text)


# Parsing


def test_parse_named_attributes_and_arrows() -> None:
    attrs, deps = parse(
        "Student(sid, name, major, advisor)", "sid -> name, major\nmajor, name → advisor"
    )
    assert attrs == ["sid", "name", "major", "advisor"]
    assert deps == [
        FD(frozenset({"sid"}), frozenset({"name", "major"})),
        FD(frozenset({"major", "name"}), frozenset({"advisor"})),
    ]


def test_parse_single_letter_shorthand() -> None:
    attrs, deps = parse("R(A, B, C, D)", "AB -> C; C -> D")
    assert attrs == ["A", "B", "C", "D"]
    assert deps == [FD(s("AB"), s("C")), FD(s("C"), s("D"))]


def test_parse_skips_blank_lines_and_comments() -> None:
    _, deps = parse("R(A, B)", "\n# keys\nA -> B\n\n")
    assert deps == [FD(s("A"), s("B"))]


@pytest.mark.parametrize(
    ("schema", "text", "message"),
    [
        ("R(A, B)", "A -> Z", "line 1: Z is not an attribute of R"),
        ("R(A, B)", "A B C", "line 1: write a dependency as X -> Y"),
        ("R(A, B)", "-> B", "line 1: the left side is empty"),
        ("R A B", "A -> B", "Write the relation as R(A, B, C)"),
        ("R(A, A)", "", "A appears twice"),
        ("R(" + ", ".join(f"a{i}" for i in range(17)) + ")", "", "at most 16 attributes"),
    ],
)
def test_parse_errors_say_what_to_fix(schema: str, text: str, message: str) -> None:
    with pytest.raises(NormalizeError, match=re.escape(message)):
        parse(schema, text)


# Closure and cover


def test_closure_silberschatz() -> None:
    # Silberschatz et al., Database System Concepts: R = (A, B, C, G, H, I).
    f = fds("A -> B; A -> C; CG -> H; CG -> I; B -> H", "A, B, C, G, H, I")
    assert closure(s("AG"), f) == s("ABCGHI")
    assert closure(s("B"), f) == s("BH")


def test_minimal_cover_removes_extraneous_and_redundant() -> None:
    # Silberschatz: F = {A -> BC, B -> C, A -> B, AB -> C} has canonical cover {A -> B, B -> C}.
    f = fds("A -> BC; B -> C; A -> B; AB -> C", "A, B, C")
    assert minimal_cover(f) == [FD(s("A"), s("B")), FD(s("B"), s("C"))]


def test_minimal_cover_keeps_equivalence() -> None:
    f = fds("A -> BC; CD -> E; B -> D; E -> A", "A, B, C, D, E")
    cover = minimal_cover(f)
    assert all(len(d.rhs) == 1 for d in cover)
    for d in f:
        assert d.rhs <= closure(d.lhs, cover)


# Keys and normal forms


def test_candidate_keys_classic() -> None:
    f = fds("A -> BC; CD -> E; B -> D; E -> A", "A, B, C, D, E")
    assert candidate_keys(list("ABCDE"), f) == [s("A"), s("E"), s("BC"), s("CD")]


def test_candidate_keys_with_overlapping_keys() -> None:
    f = fds("AB -> C; C -> D; D -> A", "A, B, C, D")
    assert candidate_keys(list("ABCD"), f) == [s("AB"), s("BC"), s("BD")]


def test_transitive_dependency_is_2nf_not_3nf() -> None:
    f = fds("A -> B; B -> C", "A, B, C")
    report = normal_form(list("ABC"), f)
    assert report.form == "2NF"
    [v] = report.violations
    assert v.form == "3NF" and v.fd == FD(s("B"), s("C"))
    assert "B is not a superkey and C is not part of any key" in v.reason


def test_partial_dependency_is_1nf() -> None:
    f = fds("AB -> C; B -> D", "A, B, C, D")
    report = normal_form(list("ABCD"), f)
    assert report.form == "1NF"
    assert any(v.form == "2NF" and v.fd == FD(s("B"), s("D")) for v in report.violations)
    assert "depends on B, part of the key AB" in report.violations[0].reason


def test_3nf_but_not_bcnf() -> None:
    f = fds("AB -> C; C -> D; D -> A", "A, B, C, D")
    report = normal_form(list("ABCD"), f)
    assert report.form == "3NF"
    assert {v.fd for v in report.violations} == {FD(s("C"), s("D")), FD(s("D"), s("A"))}
    assert all(v.form == "BCNF" for v in report.violations)


def test_bcnf() -> None:
    f = fds("A -> BC", "A, B, C")
    assert normal_form(list("ABC"), f).form == "BCNF"


# Decomposition


def test_bcnf_decomposition_is_lossless_with_steps() -> None:
    f = fds("A -> B; B -> C", "A, B, C")
    result = bcnf_decompose(list("ABC"), f)
    assert [r.attrs for r in result.relations] == [["B", "C"], ["A", "B"]]
    assert result.steps[0].fd == FD(s("B"), s("C"))
    assert result.steps[0].split == (["B", "C"], ["A", "B"])
    assert result.lossless is True
    assert result.lost == []


def test_bcnf_decomposition_can_lose_a_dependency() -> None:
    # Classic: R(A, B, C) with AB -> C, C -> B has no dependency-preserving BCNF decomposition.
    f = fds("AB -> C; C -> B", "A, B, C")
    result = bcnf_decompose(list("ABC"), f)
    assert all(normal_form(r.attrs, r.fds).form == "BCNF" for r in result.relations)
    assert result.lossless is True
    assert result.lost == [FD(s("AB"), s("C"))]


def test_3nf_synthesis_preserves_dependencies_and_adds_a_key() -> None:
    f = fds("A -> B; C -> D", "A, B, C, D")
    result = synthesize_3nf(list("ABCD"), f)
    assert [r.attrs for r in result.relations] == [["A", "B"], ["C", "D"], ["A", "C"]]
    assert result.lossless is True and result.lost == []


def test_3nf_synthesis_drops_relations_contained_in_others() -> None:
    f = fds("AB -> C; C -> B", "A, B, C")
    result = synthesize_3nf(list("ABC"), f)
    assert [r.attrs for r in result.relations] == [["A", "B", "C"]]


def test_chase_detects_a_lossy_join() -> None:
    f = fds("A -> B", "A, B, C")
    lossless, table = chase(list("ABC"), f, [["A", "B"], ["B", "C"]])
    assert lossless is False
    assert table == [["a1", "a2", "b13"], ["b21", "a2", "a3"]]


def test_chase_proves_a_lossless_join() -> None:
    f = fds("A -> B", "A, B, C")
    lossless, table = chase(list("ABC"), f, [["A", "B"], ["A", "C"]])
    assert lossless is True
    assert ["a1", "a2", "a3"] in table


def test_preserved_reports_which_dependencies_survive() -> None:
    f = fds("AB -> C; C -> B", "A, B, C")
    kept, lost = preserved(f, [["B", "C"], ["A", "C"]])
    assert kept == [FD(s("C"), s("B"))] and lost == [FD(s("AB"), s("C"))]


def test_decompositions_hold_their_guarantees_on_random_schemas() -> None:
    import random

    rng = random.Random(545)  # noqa: S311  test data, not security
    for _ in range(300):
        attrs = list("ABCDEF"[: rng.randint(2, 6)])
        f = []
        for _ in range(rng.randint(1, 5)):
            lhs = frozenset(rng.sample(attrs, rng.randint(1, 2)))
            rhs = frozenset(rng.sample(attrs, 1)) - lhs
            if rhs:
                f.append(FD(lhs, rhs))
        bcnf = bcnf_decompose(attrs, f)
        assert bcnf.lossless, (attrs, f)
        assert all(normal_form(r.attrs, r.fds).form == "BCNF" for r in bcnf.relations), (attrs, f)
        third = synthesize_3nf(attrs, f)
        assert third.lossless and third.lost == [], (attrs, f)
        assert all(normal_form(r.attrs, r.fds).form in ("3NF", "BCNF") for r in third.relations)


def test_bcnf_of_a_long_chain_is_fast_and_gives_the_pairs() -> None:
    import time

    names = [f"A{i}" for i in range(16)]
    attrs, fds = parse(f"R({','.join(names)})", "\n".join(f"A{i} -> A{i + 1}" for i in range(15)))
    start = time.perf_counter()
    d = bcnf_decompose(attrs, fds)
    assert time.perf_counter() - start < 5
    assert sorted(r.attrs for r in d.relations) == sorted(
        [[f"A{i}", f"A{i + 1}"] for i in range(15)]
    )
    assert d.lossless and not d.lost


def test_a_cycle_of_keys_is_already_in_bcnf_and_answers_fast() -> None:
    import time

    names = [f"A{i}" for i in range(14)]
    attrs, fds = parse(
        f"R({','.join(names)})", "\n".join(f"A{i} -> A{(i + 1) % 14}" for i in range(14))
    )
    start = time.perf_counter()
    d = bcnf_decompose(attrs, fds)
    assert time.perf_counter() - start < 5
    assert [r.attrs for r in d.relations] == [names]


def test_a_wide_relation_with_few_dependencies_decomposes_fast() -> None:
    # Like an uploaded table: many columns, one dependency.
    import time

    names = [f"A{i}" for i in range(16)]  # the most parse allows
    attrs, fds = parse(f"R({','.join(names)})", "A0 -> A1")
    start = time.perf_counter()
    d = bcnf_decompose(attrs, fds)
    assert time.perf_counter() - start < 5
    assert sorted(len(r.attrs) for r in d.relations) == [2, 15]
