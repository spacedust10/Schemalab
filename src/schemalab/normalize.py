"""Normalization engine: functional dependencies, keys, normal forms and decompositions.

The algorithms are the textbook ones (Silberschatz et al.; Elmasri and Navathe): attribute
closure, canonical cover, candidate keys by core attributes plus subset search, 2NF/3NF/BCNF
tests that say which dependency fails and why, BCNF decomposition, 3NF synthesis, the chase
test for lossless joins, and the restricted-closure test for dependency preservation.

Key search and projection are exponential in the number of attributes, so relations are capped
at MAX_ATTRIBUTES; that is plenty for teaching and for one table at a time.
"""

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Literal

MAX_ATTRIBUTES = 16
MAX_DEPENDENCIES = 100

Form = Literal["1NF", "2NF", "3NF", "BCNF"]
Attrs = frozenset[str]

_RELATION = re.compile(r"\s*([A-Za-z_]\w*)\s*\((.*)\)\s*", re.S)
_NAME = re.compile(r"[A-Za-z_]\w*")
_ARROW = re.compile(r"->|→")


class NormalizeError(ValueError):
    """The input cannot be read. The message says what to fix."""


@dataclass(frozen=True)
class FD:
    lhs: Attrs
    rhs: Attrs


@dataclass(frozen=True)
class Violation:
    form: Form  # the normal form this dependency breaks
    fd: FD
    reason: str


@dataclass(frozen=True)
class Report:
    form: Form  # the highest normal form the relation is in
    violations: list[Violation]  # what keeps it from the next form up


@dataclass(frozen=True)
class Relation:
    attrs: list[str]
    fds: list[FD]  # a canonical cover of the dependencies that hold on these attributes
    keys: list[Attrs]


@dataclass(frozen=True)
class Step:
    relation: list[str]
    fd: FD
    split: tuple[list[str], list[str]]


@dataclass(frozen=True)
class Decomposition:
    relations: list[Relation]
    lossless: bool
    chase_table: list[list[str]]
    kept: list[FD]
    lost: list[FD]
    steps: list[Step] = field(default_factory=list)


# Parsing


def parse(schema: str, dependencies: str) -> tuple[list[str], list[FD]]:
    """Read "R(A, B, C)" and one dependency per line ("A, B -> C" or "AB -> C")."""
    match = _RELATION.fullmatch(schema)
    if not match:
        raise NormalizeError("Write the relation as R(A, B, C).")
    name = match[1]
    attrs = [a.strip() for a in match[2].split(",") if a.strip()]
    if not attrs:
        raise NormalizeError("The relation needs at least one attribute.")
    for a in attrs:
        if not _NAME.fullmatch(a):
            raise NormalizeError(f"{a!r} is not a valid attribute name.")
    seen: set[str] = set()
    for a in attrs:
        if a in seen:
            raise NormalizeError(f"{a} appears twice in the relation.")
        seen.add(a)
    if len(attrs) > MAX_ATTRIBUTES:
        raise NormalizeError(f"Use at most {MAX_ATTRIBUTES} attributes, not {len(attrs)}.")

    fds: list[FD] = []
    for number, line in enumerate(dependencies.splitlines(), start=1):
        for part in line.split("#", 1)[0].split(";"):
            if not part.strip():
                continue
            sides = _ARROW.split(part)
            if len(sides) != 2:
                raise NormalizeError(f"line {number}: write a dependency as X -> Y.")
            lhs, rhs = (read_attrs(text, attrs, name, number) for text in sides)
            if not lhs:
                raise NormalizeError(f"line {number}: the left side is empty.")
            if not rhs:
                raise NormalizeError(f"line {number}: the right side is empty.")
            fds.append(FD(lhs, rhs))
    if len(fds) > MAX_DEPENDENCIES:
        raise NormalizeError(f"Use at most {MAX_DEPENDENCIES} dependencies.")
    return attrs, fds


def read_attrs(text: str, attrs: list[str], relation: str, line: int = 1) -> Attrs:
    """Attribute names separated by commas or spaces, or one-letter names run together."""
    out: set[str] = set()
    for token in re.split(r"[\s,]+", text.strip()):
        if not token:
            continue
        if token in attrs:
            out.add(token)
        elif all(ch in attrs for ch in token):  # textbook shorthand: AB means A, B
            out.update(token)
        else:
            missing = next((ch for ch in token if ch not in attrs), token)
            bad = missing if len(token) > 1 and all(len(a) == 1 for a in attrs) else token
            raise NormalizeError(f"line {line}: {bad} is not an attribute of {relation}.")
    return frozenset(out)


# Closure, cover, keys


def closure(attrs: Attrs, fds: list[FD]) -> Attrs:
    result = set(attrs)
    changed = True
    while changed:
        changed = False
        for fd in fds:
            if fd.lhs <= result and not fd.rhs <= result:
                result |= fd.rhs
                changed = True
    return frozenset(result)


def minimal_cover(fds: list[FD]) -> list[FD]:
    """Single attributes on the right, no extraneous left attributes, no redundant FDs."""
    cover: list[FD] = []
    for fd in fds:
        for a in sorted(fd.rhs - fd.lhs):
            single = FD(fd.lhs, frozenset({a}))
            if single not in cover:
                cover.append(single)
    reduced: list[FD] = []
    for fd in cover:
        lhs = set(fd.lhs)
        for a in sorted(fd.lhs):
            if len(lhs) > 1 and fd.rhs <= closure(frozenset(lhs - {a}), cover):
                lhs.discard(a)
        smaller = FD(frozenset(lhs), fd.rhs)
        if smaller not in reduced:
            reduced.append(smaller)
    result = list(reduced)
    for fd in reduced:
        rest = [g for g in result if g != fd]
        if fd.rhs <= closure(fd.lhs, rest):
            result = rest
    return result


def _order(attrs: list[str]) -> dict[str, int]:
    return {a: i for i, a in enumerate(attrs)}


def _sorted(sets: list[Attrs], attrs: list[str]) -> list[Attrs]:
    pos = _order(attrs)
    return sorted(sets, key=lambda s: (len(s), sorted(pos[a] for a in s)))


def candidate_keys(attrs: list[str], fds: list[FD]) -> list[Attrs]:
    everything = frozenset(attrs)
    on_right = set().union(*(fd.rhs - fd.lhs for fd in fds)) if fds else set()
    on_left = set().union(*(fd.lhs for fd in fds)) if fds else set()
    core = frozenset(a for a in attrs if a not in on_right)  # in every key
    if closure(core, fds) == everything:
        return [core]
    middle = [a for a in attrs if a in on_right and a in on_left]  # may be in a key
    keys: list[Attrs] = []
    for size in range(1, len(middle) + 1):
        for extra in combinations(middle, size):
            candidate = core | frozenset(extra)
            if any(k <= candidate for k in keys):
                continue
            if closure(candidate, fds) == everything:
                keys.append(candidate)
    return _sorted(keys, attrs)


# Normal forms


def show(attrs: Attrs, order: list[str]) -> str:
    """Attributes in schema order: "AB" for one-letter names, "sid, name" otherwise."""
    names = [a for a in order if a in attrs]
    return ("" if all(len(a) == 1 for a in order) else ", ").join(names)


def normal_form(attrs: list[str], fds: list[FD]) -> Report:
    everything = frozenset(attrs)
    cover = minimal_cover(fds)
    keys = candidate_keys(attrs, cover)
    prime = frozenset().union(*keys)

    partial: list[Violation] = []
    for key in keys:
        for size in range(1, len(key)):
            for part in combinations(sorted(key), size):
                sub = frozenset(part)
                dependents = (closure(sub, cover) - sub) - prime
                if dependents:
                    fd = FD(sub, dependents)
                    if all(v.fd != fd for v in partial):
                        reason = (
                            f"{show(dependents, attrs)} depends on {show(sub, attrs)}, "
                            f"part of the key {show(key, attrs)}, not on the whole key."
                        )
                        partial.append(Violation("2NF", fd, reason))

    third: list[Violation] = []
    bcnf: list[Violation] = []
    for fd in cover:
        if closure(fd.lhs, cover) == everything:
            continue
        x, a = show(fd.lhs, attrs), show(fd.rhs, attrs)
        bcnf.append(Violation("BCNF", fd, f"{x} -> {a}: {x} is not a superkey."))
        if not fd.rhs <= prime:
            reason = f"{x} -> {a}: {x} is not a superkey and {a} is not part of any key."
            third.append(Violation("3NF", fd, reason))

    if partial:
        return Report("1NF", partial)
    if third:
        return Report("2NF", third)
    if bcnf:
        return Report("3NF", bcnf)
    return Report("BCNF", [])


# Decompositions


def project(fds: list[FD], attrs: list[str]) -> list[FD]:
    """A canonical cover of the dependencies that hold on `attrs`."""
    sub = frozenset(attrs)
    # Only attributes on some left side can determine anything; the rest never help.
    deciders = [a for a in attrs if any(a in fd.lhs for fd in fds)]
    found: list[FD] = []
    for size in range(1, len(deciders) + 1):
        for part in combinations(deciders, size):
            x = frozenset(part)
            # A left side with an attribute its other attributes already determine gives only
            # dependencies the smaller left side gives too; skipping it keeps the list small.
            if any(a in closure(x - {a}, fds) for a in x if size > 1):
                continue
            y = (closure(x, fds) & sub) - x
            if y:
                found.append(FD(x, y))
    return minimal_cover(found)


def _relation(attrs: list[str], fds: list[FD]) -> Relation:
    projected = project(fds, attrs)
    return Relation(attrs, projected, candidate_keys(attrs, projected))


def preserved(fds: list[FD], relations: list[list[str]]) -> tuple[list[FD], list[FD]]:
    """Split `fds` into those the decomposition preserves and those it loses."""
    kept: list[FD] = []
    lost: list[FD] = []
    for fd in fds:
        z = set(fd.lhs)
        changed = True
        while changed:
            changed = False
            for r in relations:
                part = frozenset(r)
                gained = (closure(frozenset(z & part), fds) & part) - z
                if gained:
                    z |= gained
                    changed = True
        (kept if fd.rhs <= z else lost).append(fd)
    return kept, lost


def chase(
    attrs: list[str], fds: list[FD], relations: list[list[str]]
) -> tuple[bool, list[list[str]]]:
    """The tableau test: lossless if some row ends up all distinguished (a) symbols."""
    col = _order(attrs)
    table = [
        [f"a{j + 1}" if a in r else f"b{i + 1}{j + 1}" for j, a in enumerate(attrs)]
        for i, r in enumerate(relations)
    ]
    cover = minimal_cover(fds)
    changed = True
    while changed:
        changed = False
        for fd in cover:
            lhs = [col[a] for a in fd.lhs]
            for i, j in combinations(range(len(table)), 2):
                if all(table[i][c] == table[j][c] for c in lhs):
                    for a in fd.rhs:
                        c = col[a]
                        x, y = table[i][c], table[j][c]
                        if x != y:
                            keep, drop = (x, y) if (x[0], x) < (y[0], y) else (y, x)
                            for row in table:
                                if row[c] == drop:
                                    row[c] = keep
                            changed = True
    lossless = any(all(v.startswith("a") for v in row) for row in table)
    return lossless, table


def _finish(
    attrs: list[str], fds: list[FD], parts: list[list[str]], steps: list[Step]
) -> Decomposition:
    lossless, table = chase(attrs, fds, parts)
    kept, lost = preserved(fds, parts)
    relations = [_relation(p, fds) for p in parts]
    return Decomposition(relations, lossless, table, kept, lost, steps)


def bcnf_decompose(attrs: list[str], fds: list[FD]) -> Decomposition:
    steps: list[Step] = []

    def split(r: list[str]) -> list[list[str]]:
        cover = project(fds, r)
        whole = frozenset(r)
        bad = next((fd for fd in cover if closure(fd.lhs, cover) != whole), None)
        if bad is None:  # every left side is a superkey: r is in BCNF
            return [r]
        x_plus = closure(bad.lhs, cover)
        r1 = [a for a in r if a in x_plus]
        r2 = [a for a in r if a not in x_plus - bad.lhs]
        steps.append(Step(r, bad, (r1, r2)))
        return split(r1) + split(r2)

    return _finish(attrs, fds, split(list(attrs)), steps)


def synthesize_3nf(attrs: list[str], fds: list[FD]) -> Decomposition:
    cover = minimal_cover(fds)
    groups: dict[Attrs, set[str]] = {}
    for fd in cover:
        groups.setdefault(fd.lhs, set(fd.lhs)).update(fd.rhs)
    parts = [[a for a in attrs if a in g] for g in groups.values()]
    keys = candidate_keys(attrs, cover)
    if not any(k <= frozenset(p) for k in keys for p in parts):
        parts.append([a for a in attrs if a in keys[0]])
    parts = [
        p
        for i, p in enumerate(parts)
        if not any(
            set(p) < set(q) or (set(p) == set(q) and j < i) for j, q in enumerate(parts) if j != i
        )
    ]
    return _finish(attrs, fds, parts, [])
