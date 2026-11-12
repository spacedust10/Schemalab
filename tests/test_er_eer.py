"""Weak entities, identifying relationships and specialization (ISA), as in Elmasri and
Navathe: read from DDL, written in the text format, and mapped back to tables."""

import sqlite3
from pathlib import Path

import pytest

from schemalab.er import ERError, Specialization, from_ddl, parse, to_ddl, to_text

USERS_SQL = (Path(__file__).parent / "fixtures" / "er_users.sql").read_text()

EER = """
entity Department { deptname varchar(20) key, deptaddr varchar(20) }
weak entity Unit { unitid varchar(20) key, unitaddr varchar(20) }
weak entity Member { memberid varchar(20) key, memberaddr varchar(20) }
entity VerifiedMember { pin varchar(20) }
entity UnverifiedMember { }
identifying relationship HasUnit: Department 1 -- N Unit!
identifying relationship HasMember: Unit 1 -- N Member!
isa Member: VerifiedMember, UnverifiedMember disjoint total
relationship Vouches: VerifiedMember 1 -- N UnverifiedMember!
"""


def entity(model, name):  # type: ignore[no-untyped-def]
    return next(e for e in model.entities if e.name == name)


def rel(model, name):  # type: ignore[no-untyped-def]
    return next(r for r in model.relationships if r.name == name)


# The text format


def test_parse_reads_weak_entities_identifying_relationships_and_isa() -> None:
    m = parse(EER)
    assert entity(m, "Unit").weak and not entity(m, "Department").weak
    assert rel(m, "HasUnit").identifying and not rel(m, "Vouches").identifying
    assert m.specializations == [
        Specialization("Member", ["VerifiedMember", "UnverifiedMember"], True, True)
    ]


def test_isa_flags_default_to_overlapping_and_partial() -> None:
    m = parse("entity A { id int key }\nentity B { x int }\nisa A: B\n")
    assert m.specializations == [Specialization("A", ["B"], False, False)]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("weak entity W { n int key }", "W is a weak entity"),
        (
            "entity A { id int key }\nentity B { id int key }\n"
            "identifying relationship R: A 1 -- N B!",
            "B is not a weak entity",
        ),
        (
            "entity A { id int key }\nweak entity W { n int key }\n"
            "identifying relationship R: A 1 -- N W",
            "every W",
        ),
        ("entity A { id int key }\nisa A: Nope", "Nope is not an entity"),
        ("entity A { id int key }\nentity B { x int }", "B has no key"),
    ],
)
def test_eer_errors_say_what_to_fix(text: str, message: str) -> None:
    with pytest.raises(ERError, match=message):
        parse(text)


def test_text_round_trip() -> None:
    m = parse(EER)
    assert parse(to_text(m)) == m
    named = parse(EER.replace("disjoint total", "disjoint total on kind"))
    assert named.specializations[0].discriminator == "kind"
    assert parse(to_text(named)) == named


# ER -> DDL


def test_weak_entities_and_subtypes_borrow_their_owners_keys() -> None:
    ddl = to_ddl(parse(EER), "sqlite")
    con = sqlite3.connect(":memory:")
    con.executescript("PRAGMA foreign_keys = ON;" + ddl)
    pk = {
        t: [
            r[1] for r in sorted(con.execute(f"PRAGMA table_info({t})"), key=lambda r: r[5]) if r[5]
        ]
        for t in ("unit", "member", "verified_member", "unverified_member")
    }
    assert pk["unit"] == ["deptname", "unitid"]
    assert pk["member"] == ["deptname", "unitid", "memberid"]
    assert pk["verified_member"] == pk["unverified_member"] == ["deptname", "unitid", "memberid"]
    con.executescript(
        "INSERT INTO department VALUES ('cs', 'a');"
        "INSERT INTO unit VALUES ('cs', 'u1', 'b');"
        "INSERT INTO member (deptname, unitid, memberid, memberaddr, member_type)"
        " VALUES ('cs', 'u1', 'm1', 'c', 'verified_member');"
        "INSERT INTO verified_member (deptname, unitid, memberid, pin)"
        " VALUES ('cs', 'u1', 'm1', '9');"
    )
    # Disjoint: a verified member cannot also be unverified.
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO unverified_member (deptname, unitid, memberid, verified_member_deptname,"
            " verified_member_unitid, verified_member_memberid)"
            " VALUES ('cs', 'u1', 'm1', 'cs', 'u1', 'm1')"
        )
    # Deleting the owner deletes what depends on it.
    con.execute("DELETE FROM department")
    assert con.execute("SELECT count(*) FROM verified_member").fetchone() == (0,)


# DDL -> ER


def test_from_ddl_reads_the_users_schema_as_eer() -> None:
    m = from_ddl(USERS_SQL)
    assert not entity(m, "Departments").weak
    units, users = entity(m, "Units"), entity(m, "Users")
    assert units.weak and [(a.name, a.key) for a in units.attributes] == [
        ("unitid", True),
        ("unitaddr", False),
    ]
    assert users.weak and [a.name for a in users.keys] == ["userid"]
    assert [a.name for a in users.attributes] == ["userid", "useraddr"]
    assert [a.name for a in entity(m, "VerifiedUsers").attributes] == ["pin"]
    assert entity(m, "UnverifiedUsers").attributes == []
    # user_type picks the subtype: an attribute-defined specialization, shown on the ISA.
    assert m.specializations == [
        Specialization("Users", ["VerifiedUsers", "UnverifiedUsers"], True, True, "user_type")
    ]
    assert "user_type" not in [a.name for a in users.attributes]
    got = {
        r.name: (
            r.left.entity,
            r.left.card,
            r.right.entity,
            r.right.card,
            r.right.total,
            r.identifying,
        )
        for r in m.relationships
    }
    assert got == {
        "DepartmentsUnits": ("Departments", "1", "Units", "N", True, True),
        "UnitsUsers": ("Units", "1", "Users", "N", True, True),
        "UnverifiedUsersR3": ("VerifiedUsers", "1", "UnverifiedUsers", "N", True, False),
    }


def test_a_foreign_key_holding_the_whole_key_is_a_subtype_not_one_to_many() -> None:
    m = from_ddl(
        "CREATE TABLE person (id INT PRIMARY KEY, name TEXT);"
        "CREATE TABLE student (id INT PRIMARY KEY REFERENCES person(id), gpa REAL);"
    )
    assert m.relationships == []
    assert m.specializations == [Specialization("Person", ["Student"], False, False)]


def test_eer_round_trips_through_ddl() -> None:
    m = parse(EER)
    again = from_ddl(to_ddl(m, "postgres"))
    assert [(e.name, e.weak, [a.name for a in e.attributes]) for e in again.entities] == [
        (e.name, e.weak, [a.name for a in e.attributes]) for e in m.entities
    ]
    assert again.specializations == m.specializations
    assert sorted(
        (r.left.entity, r.right.entity, r.kind, r.identifying) for r in again.relationships
    ) == sorted((r.left.entity, r.right.entity, r.kind, r.identifying) for r in m.relationships)
