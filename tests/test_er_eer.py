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


def test_from_ddl_reads_tables_nested_in_create_schema() -> None:
    sql = (Path(__file__).parent / "fixtures" / "schemas.sql").read_text()
    m = from_ddl(sql)
    assert [e.name for e in m.entities] == ["BusinessEntity", "Person", "Customer", "CrmCustomer"]
    assert [a.type for a in entity(m, "Person").attributes if a.name == "FirstName"] == [
        "VARCHAR(50)"
    ]
    assert m.specializations == [Specialization("BusinessEntity", ["Person"])]
    got = {(r.left.entity, r.right.entity) for r in m.relationships}
    assert got == {("Person", "Customer"), ("Customer", "CrmCustomer")}


def test_a_bracket_in_postgres_sql_does_not_mean_sql_server() -> None:
    m = from_ddl(
        "CREATE TABLE a (id INT PRIMARY KEY, tags TEXT[] DEFAULT ARRAY['x']);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id));"
    )
    assert [e.name for e in m.entities] == ["A", "B"] and len(m.relationships) == 1


def test_reserved_words_work_as_attribute_names_and_are_quoted_in_ddl() -> None:
    # Real tables have columns like "group" (AdventureWorks' SalesTerritory).
    m = parse('entity Territory { id int key, "group" text, order int }'.replace('"', ""))
    assert [a.name for a in m.entities[0].attributes] == ["id", "group", "order"]
    for dialect in ("postgres", "mysql", "sqlite", "tsql", "oracle"):
        ddl = to_ddl(m, dialect)
        assert "group" in ddl.lower()
    con = sqlite3.connect(":memory:")
    con.executescript(to_ddl(m, "sqlite"))
    assert [r[1] for r in con.execute("PRAGMA table_info(territory)")] == ["id", "group", "order"]
    assert parse(to_text(m)) == m


ORDERS = """
CREATE TABLE offer (offerid INT PRIMARY KEY);
CREATE TABLE product (productid INT PRIMARY KEY);
CREATE TABLE offerproduct (offerid INT REFERENCES offer, productid INT REFERENCES product,
  PRIMARY KEY (offerid, productid));
CREATE TABLE orderline (lineid INT PRIMARY KEY, offerid INT NOT NULL, productid INT NOT NULL,
  FOREIGN KEY (offerid, productid) REFERENCES offerproduct (offerid, productid));
"""


def test_a_link_table_that_others_reference_is_an_associative_entity() -> None:
    m = from_ddl(ORDERS)
    link = entity(m, "Offerproduct")
    assert link.weak and link.attributes == []  # identified by its two owners alone
    kinds = {r.name: (r.identifying, r.kind) for r in m.relationships}
    assert kinds == {
        "OfferOfferproduct": (True, "1:N"),
        "ProductOfferproduct": (True, "1:N"),
        "OfferproductOrderline": (False, "1:N"),
    }
    assert parse(to_text(m)) == m


def test_a_weak_entity_with_two_owners_takes_both_keys() -> None:
    m = from_ddl(ORDERS)
    con = sqlite3.connect(":memory:")
    con.executescript(to_ddl(m, "sqlite"))
    pk = [
        r[1]
        for r in sorted(con.execute("PRAGMA table_info(offerproduct)"), key=lambda r: r[5])
        if r[5]
    ]
    assert pk == ["offerid", "productid"]


def test_columns_added_and_dropped_by_alter_table_count() -> None:
    m = from_ddl(
        "CREATE TABLE doc (old_id INT, title TEXT, level INT);"
        "ALTER TABLE doc DROP COLUMN level;"
        "ALTER TABLE doc ADD node VARCHAR DEFAULT '/';"
        "ALTER TABLE doc ADD CONSTRAINT pk_doc PRIMARY KEY (node);"
    )
    assert [(a.name, a.key) for a in m.entities[0].attributes] == [
        ("old_id", False),
        ("title", False),
        ("node", True),
    ]


def test_a_design_can_hold_every_table_an_upload_can() -> None:
    from schemalab.er import MAX_ENTITIES
    from schemalab.uploads import MAX_TABLES

    assert MAX_ENTITIES >= MAX_TABLES
    text = "\n".join(f"entity E{i} {{ id int key }}" for i in range(MAX_TABLES))
    assert len(parse(text).entities) == MAX_TABLES


def test_a_table_level_check_defines_a_total_specialization() -> None:
    m = from_ddl(
        "CREATE TABLE p (id INT PRIMARY KEY, kind TEXT NOT NULL, UNIQUE (id, kind),"
        "  CHECK (kind IN ('a', 'b')));"
        "CREATE TABLE a (id INT PRIMARY KEY, kind TEXT NOT NULL,"
        "  FOREIGN KEY (id, kind) REFERENCES p (id, kind));"
        "CREATE TABLE b (id INT PRIMARY KEY, kind TEXT NOT NULL,"
        "  FOREIGN KEY (id, kind) REFERENCES p (id, kind));"
    )
    assert m.specializations == [Specialization("P", ["A", "B"], True, True, "kind")]


def test_an_explicit_null_column_is_optional_not_required() -> None:
    m = from_ddl(
        "CREATE TABLE t (id INT PRIMARY KEY);"
        "CREATE TABLE u (id INT PRIMARY KEY, t_id INT NULL REFERENCES t(id));"
    )
    [r] = m.relationships
    assert r.right.total is False  # NULL means a u need not have a t
