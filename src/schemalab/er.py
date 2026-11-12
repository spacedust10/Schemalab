"""ER designs: a small text format, ER -> DDL in five dialects, and DDL -> ER.

    entity Student { sid int key, name text, gpa decimal(3,2) }
    entity Course { cid varchar(10) key, title text }
    relationship Advises: Professor 1 -- N Student
    relationship Enrolls: Student M -- N Course! { grade text }

`1`, `N` and `M` sit next to the entity they count, as in Chen notation: one professor advises
N students. `!` after an entity marks total participation (every Course has an enrollment).

The enhanced ER constructs follow Elmasri and Navathe:

    weak entity Unit { unitid varchar(20) key }          # key: the partial key
    identifying relationship HasUnit: Department 1 -- N Unit!
    isa Member: VerifiedMember, UnverifiedMember disjoint total on member_type

A weak entity is identified by its owner's key plus its partial key; a subtype (subclass) has
the key of its supertype. `disjoint` or `overlapping`, `total` or `partial`, and an optional
defining attribute (`on`) describe the specialization.

The mapping is the textbook one: each entity is a table with its key as primary key; a 1:N
relationship adds a foreign key on the N side; 1:1 adds a unique foreign key on the side with
total participation; M:N becomes a junction table keyed by both foreign keys. DDL is written
once and translated by sqlglot, so every dialect gets the same design.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

from schemalab.sqldump import prepare

DIALECTS = {
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "tsql": "SQL Server",
    "oracle": "Oracle",
}
RESERVED = {
    "all", "and", "as", "asc", "check", "column", "default", "desc", "foreign", "from",
    "grant", "group", "having", "index", "join", "key", "limit", "not", "null", "offset",
    "or", "order", "primary", "references", "select", "table", "union", "user", "values",
    "where",
}  # fmt: skip
MAX_ENTITIES = 40

Card = Literal["1", "N"]

_NAME = r"[A-Za-z_]\w*"
_TYPES = {
    "int": "INT",
    "integer": "INT",
    "bigint": "BIGINT",
    "smallint": "SMALLINT",
    "text": "TEXT",
    "string": "TEXT",
    "real": "REAL",
    "float": "REAL",
    "double": "DOUBLE PRECISION",
    "date": "DATE",
    "time": "TIME",
    "timestamp": "TIMESTAMP",
    "datetime": "TIMESTAMP",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
}
_SIZED = re.compile(r"(varchar|char|decimal|numeric)\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)")
_RELATIONSHIP = re.compile(
    rf"relationship\s+({_NAME})\s*:\s*({_NAME})(!?)\s+([1NM])\s*--\s*([1NM])\s+({_NAME})(!?)"
    r"\s*(?:\{(.*)\})?\s*",
    re.S,
)
_ENTITY = re.compile(rf"(weak\s+)?entity\s+({_NAME})\s*\{{(.*)\}}\s*", re.S)
_ISA = re.compile(
    rf"isa\s+({_NAME})\s*:\s*({_NAME}(?:\s*,\s*{_NAME})*)"
    rf"((?:\s+(?:disjoint|overlapping|total|partial))*)(?:\s+on\s+({_NAME}))?\s*"
)


class ERError(ValueError):
    """The design cannot be read. The message names the line and what to fix."""


@dataclass(frozen=True)
class Attribute:
    name: str
    type: str  # canonical SQL, such as INT or DECIMAL(3, 2)
    key: bool = False


@dataclass(frozen=True)
class Entity:
    name: str
    attributes: list[Attribute]
    weak: bool = False  # identified through an owner; its keys are the partial key

    @property
    def keys(self) -> list[Attribute]:
        return [a for a in self.attributes if a.key]


@dataclass(frozen=True)
class End:
    entity: str
    card: Card
    total: bool = False


@dataclass(frozen=True)
class Relationship:
    name: str
    left: End
    right: End
    attributes: list[Attribute] = field(default_factory=list)
    identifying: bool = False  # links a weak entity to its owner

    @property
    def kind(self) -> str:
        if self.left.card == "N" and self.right.card == "N":
            return "M:N"
        return f"{self.left.card}:{self.right.card}"


@dataclass(frozen=True)
class Specialization:
    """A supertype and its subtypes (ISA). Disjoint: an entity is in at most one subtype.
    Total: every entity of the supertype is in some subtype."""

    parent: str
    children: list[str]
    disjoint: bool = False
    total: bool = False
    discriminator: str | None = None  # the defining attribute, when there is one


@dataclass(frozen=True)
class Model:
    entities: list[Entity]
    relationships: list[Relationship]
    specializations: list[Specialization] = field(default_factory=list)


# Text format


def parse(text: str) -> Model:
    entities: list[Entity] = []
    relationships: list[Relationship] = []
    specializations: list[Specialization] = []
    lines: dict[str, int] = {}

    def known(name: str, line: int) -> str:
        if not any(e.name == name for e in entities):
            raise ERError(f"line {line}: {name} is not an entity declared above.")
        return name

    for line, statement in _statements(text):
        if re.match(r"(weak\s+)?entity\b", statement):
            match = _ENTITY.fullmatch(statement)
            if not match:
                raise ERError(f"line {line}: write an entity as entity Name {{ id int key, ... }}.")
            name = _check_name(match[2], line)
            if any(e.name == name for e in entities):
                raise ERError(f"line {line}: {name} is declared twice.")
            entities.append(Entity(name, _attributes(match[3], line), bool(match[1])))
            lines[name] = line
        elif re.match(r"(identifying\s+)?relationship\b", statement):
            identifying = statement.startswith("identifying")
            match = _RELATIONSHIP.fullmatch(re.sub(r"^identifying\s+", "", statement))
            if not match:
                raise ERError(
                    f"line {line}: write a relationship as "
                    "relationship Name: EntityA 1 -- N EntityB { attr type }."
                )
            name, a, a_total, a_card, b_card, b, b_total, body = match.groups()
            known(a, line), known(b, line)
            r = Relationship(
                _check_name(name, line),
                End(a, _card(a_card), bool(a_total)),
                End(b, _card(b_card), bool(b_total)),
                _attributes(body or "", line, keys=False),
                identifying,
            )
            if identifying:
                _check_identifying(r, {e.name: e for e in entities}, line)
            relationships.append(r)
        elif statement.startswith("isa"):
            match = _ISA.fullmatch(statement)
            if not match:
                raise ERError(
                    f"line {line}: write a specialization as "
                    "isa Super: SubA, SubB disjoint total on kind."
                )
            parent = known(match[1], line)
            children = [known(c.strip(), line) for c in match[2].split(",")]
            flags = match[3].split()
            for child in children:
                if child == parent or any(child in sp.children for sp in specializations):
                    raise ERError(f"line {line}: {child} already has a supertype.")
            specializations.append(
                Specialization(parent, children, "disjoint" in flags, "total" in flags, match[4])
            )
        else:
            raise ERError(f"line {line}: start a line with entity, relationship or isa.")
    subtypes = {c for sp in specializations for c in sp.children}
    for e in entities:
        if not e.keys and e.name not in subtypes:
            raise ERError(
                f"line {lines[e.name]}: {e.name} has no key attribute; mark one with key."
            )
        if e.weak and not any(r.identifying and e.name in _ends(r) for r in relationships):
            raise ERError(
                f"line {lines[e.name]}: {e.name} is a weak entity; give it an identifying "
                f"relationship to its owner, like identifying relationship Has: Owner 1 -- N "
                f"{e.name}!."
            )
    if not entities:
        raise ERError("Describe at least one entity, like entity Student { sid int key }.")
    if len(entities) > MAX_ENTITIES:
        raise ERError(f"Use at most {MAX_ENTITIES} entities.")
    return Model(entities, relationships, specializations)


def _ends(r: Relationship) -> tuple[str, str]:
    return r.left.entity, r.right.entity


def _weak_end(r: Relationship, by_name: dict[str, Entity]) -> tuple[End, End]:
    """(owner end, weak end) of an identifying relationship."""
    left_weak = by_name[r.left.entity].weak and not by_name[r.right.entity].weak
    return (r.right, r.left) if left_weak else (r.left, r.right)


def _check_identifying(r: Relationship, by_name: dict[str, Entity], line: int) -> None:
    owner, weak = _weak_end(r, by_name)
    if not by_name[weak.entity].weak:
        raise ERError(
            f"line {line}: {weak.entity} is not a weak entity; declare it weak entity "
            f"{weak.entity}, or drop identifying."
        )
    if owner.card != "1":
        raise ERError(f"line {line}: a weak entity has one owner; write {owner.entity} 1 -- ...")
    if not weak.total:
        raise ERError(
            f"line {line}: every {weak.entity} depends on its owner; mark it total with "
            f"{weak.entity}!."
        )


def _statements(text: str) -> list[tuple[int, str]]:
    """Statements with their first line number; a { ... } block may span lines."""
    out: list[tuple[int, str]] = []
    pending: list[str] = []
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not pending:
            start = number
        pending.append(line)
        joined = " ".join(pending)
        if joined.count("{") <= joined.count("}"):
            out.append((start, joined))
            pending = []
    if pending:
        raise ERError(f"line {start}: a {{ is never closed.")
    return out


def _card(symbol: str) -> Card:
    return "1" if symbol == "1" else "N"


def _check_name(name: str, line: int) -> str:
    if name.lower() in RESERVED:
        raise ERError(f"line {line}: {name} is a reserved word in SQL; choose another name.")
    return name


def _attributes(body: str, line: int, keys: bool = True) -> list[Attribute]:
    attrs: list[Attribute] = []
    for item in _split(body):
        words = item.split()
        is_key = keys and words[-1].lower() == "key"
        if is_key:
            words = words[:-1]
        if len(words) < 2 or not re.fullmatch(_NAME, words[0]):
            raise ERError(f"line {line}: write an attribute as name type, like sid int.")
        name = _check_name(words[0], line)
        if any(a.name == name for a in attrs):
            raise ERError(f"line {line}: {name} appears twice.")
        attrs.append(Attribute(name, _type(" ".join(words[1:]), line), is_key))
    return attrs


def _split(body: str) -> list[str]:
    """Items separated by commas or newlines, ignoring commas inside parentheses."""
    items, depth, current = [], 0, ""
    for ch in body:
        if ch in ",\n" and depth == 0:
            items.append(current)
            current = ""
            continue
        depth += {"(": 1, ")": -1}.get(ch, 0)
        current += ch
    items.append(current)
    return [i.strip() for i in items if i.strip()]


def _type(text: str, line: int) -> str:
    word = text.lower().strip()
    if word in _TYPES:
        return _TYPES[word]
    sized = _SIZED.fullmatch(word)
    if sized:
        kind, size, scale = sized.groups()
        if kind in ("decimal", "numeric"):
            return f"DECIMAL({size}, {scale or 0})"
        return f"{kind.upper()}({size})"
    raise ERError(
        f"line {line}: {text} is not a supported type. Use int, bigint, text, varchar(n), "
        "decimal(p,s), real, double, date, time, timestamp or boolean."
    )


def to_text(model: Model) -> str:
    def attr(a: Attribute, keys: bool = True) -> str:
        kind = "double" if a.type == "DOUBLE PRECISION" else a.type.lower().replace(", ", ",")
        return f"{a.name} {kind}" + (" key" if keys and a.key else "")

    lines = [
        f"{'weak ' if e.weak else ''}entity {e.name} "
        + (f"{{ {', '.join(attr(a) for a in e.attributes)} }}" if e.attributes else "{ }")
        for e in model.entities
    ]
    if model.relationships or model.specializations:
        lines.append("")
    for r in model.relationships:
        left = "M" if r.kind == "M:N" else r.left.card
        body = f" {{ {', '.join(attr(a, False) for a in r.attributes)} }}" if r.attributes else ""
        lines.append(
            f"{'identifying ' if r.identifying else ''}relationship {r.name}: "
            f"{r.left.entity}{'!' if r.left.total else ''} {left} -- "
            f"{r.right.card} {r.right.entity}{'!' if r.right.total else ''}{body}"
        )
    for sp in model.specializations:
        flags = ("disjoint" if sp.disjoint else "overlapping") + (" total" if sp.total else "")
        on = f" on {sp.discriminator}" if sp.discriminator else ""
        lines.append(f"isa {sp.parent}: {', '.join(sp.children)} {flags}{on}")
    return "\n".join(lines) + "\n"


# ER -> DDL


def snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _column_type(a: Attribute, keyish: bool) -> str:
    # MySQL cannot index an unbounded TEXT column, so keys and foreign keys get a length.
    return "VARCHAR(255)" if keyish and a.type == "TEXT" else a.type


@dataclass
class _Table:
    name: str
    columns: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    depends: set[str] = field(default_factory=set)

    def add(self, name: str, definition: str) -> str:
        unique, n = name, 2
        while unique in self.names:
            unique, n = f"{name}_{n}", n + 1
        self.names.append(unique)
        self.columns.append(f"{unique} {definition}")
        return unique

    def sql(self) -> str:
        return f"CREATE TABLE {self.name} ({', '.join(self.columns + self.constraints)})"


def to_ddl(model: Model, dialect: str = "postgres") -> str:
    if dialect not in DIALECTS:
        raise ERError(f"Choose one of: {', '.join(DIALECTS)}.")
    by_name = {e.name: e for e in model.entities}
    supertype = {c: sp for sp in model.specializations for c in sp.children}
    owners = {
        weak.entity: (r, owner.entity)
        for r in model.relationships
        if r.identifying
        for owner, weak in [_weak_end(r, by_name)]
    }
    tables: dict[str, _Table] = {}
    # Each entity's full key: (column name in its table, attribute), its owner's or
    # supertype's key first.
    key_cols: dict[str, list[tuple[str, Attribute]]] = {}

    def discriminator(sp: Specialization) -> str:
        return sp.discriminator or f"{snake(sp.parent)}_type"

    def build(name: str, seen: frozenset[str] = frozenset()) -> None:
        if name in tables:
            return
        if name in seen:
            raise ERError(f"{name} depends on itself through weak entities or subtypes.")
        e = by_name[name]
        t = _Table(snake(name))
        borrowed: list[tuple[str, Attribute]] = []
        refs: list[tuple[list[str], str, list[str], str]] = []  # cols, table, ref cols, extra
        sp = supertype.get(name)
        if sp:
            build(sp.parent, seen | {name})
            parent_keys = key_cols[sp.parent]
            cols = [t.add(c, _column_type(a, True) + " NOT NULL") for c, a in parent_keys]
            borrowed = [(c, a) for c, (_, a) in zip(cols, parent_keys, strict=True)]
            ref = [c for c, _ in parent_keys]
            if sp.disjoint:
                kind = discriminator(sp)
                value = snake(name)
                cols.append(
                    t.add(
                        kind, f"VARCHAR(30) NOT NULL DEFAULT '{value}' CHECK ({kind} = '{value}')"
                    )
                )
                ref.append(kind)
            refs.append((cols, sp.parent, ref, " ON DELETE CASCADE"))
        elif name in owners:
            _, owner = owners[name]
            build(owner, seen | {name})
            owner_keys = key_cols[owner]
            cols = [t.add(c, _column_type(a, True) + " NOT NULL") for c, a in owner_keys]
            borrowed = [(c, a) for c, (_, a) in zip(cols, owner_keys, strict=True)]
            refs.append((cols, owner, [c for c, _ in owner_keys], " ON DELETE CASCADE"))
        own = [
            (t.add(a.name, _column_type(a, a.key) + (" NOT NULL" if a.key else "")), a)
            for a in e.attributes
        ]
        if name in owners:
            for a in owners[name][0].attributes:
                t.add(a.name, a.type)
        key_cols[name] = borrowed + [(c, a) for c, a in own if a.key and not sp]
        if not sp and not key_cols[name]:
            raise ERError(f"{name} has no key attribute; mark one with key.")
        t.constraints.append(f"PRIMARY KEY ({', '.join(c for c, _ in key_cols[name])})")
        for cols, target, ref, tail in refs:
            t.constraints.append(
                f"FOREIGN KEY ({', '.join(cols)}) REFERENCES {snake(target)} "
                f"({', '.join(ref)}){tail}"
            )
            t.depends.add(snake(target))
        tables[name] = t

    for e in model.entities:
        build(e.name)
    for sp in model.specializations:
        if sp.disjoint:
            t = tables[sp.parent]
            kind = discriminator(sp)
            values = ", ".join(f"'{snake(c)}'" for c in sp.children)
            t.add(
                kind, f"VARCHAR(30){' NOT NULL' if sp.total else ''} CHECK ({kind} IN ({values}))"
            )
            keys = [c for c, _ in key_cols[sp.parent]]
            t.constraints.append(f"UNIQUE ({', '.join([*keys, kind])})")

    def foreign_key(t: _Table, target: Entity, prefix: str, total: bool, unique: bool) -> list[str]:
        keys = key_cols[target.name]
        cols = [
            t.add(
                f"{prefix}_{c}",
                _column_type(a, True)
                + (" NOT NULL" if total else "")
                + (" UNIQUE" if unique else ""),
            )
            for c, a in keys
        ]
        target_table = tables[target.name].name
        t.constraints.append(
            f"FOREIGN KEY ({', '.join(cols)}) REFERENCES {target_table} "
            f"({', '.join(c for c, _ in keys)})"
        )
        if target_table != t.name:
            t.depends.add(target_table)
        return cols

    junctions: list[_Table] = []
    for r in model.relationships:
        if r.identifying:
            continue  # the weak entity's table already holds its owner's key
        left, right = by_name[r.left.entity], by_name[r.right.entity]
        if r.kind == "M:N":
            j = _Table(snake(r.name))
            cols = foreign_key(j, left, snake(left.name), True, False)
            cols += foreign_key(j, right, snake(right.name), True, False)
            j.constraints.insert(0, f"PRIMARY KEY ({', '.join(cols)})")
            for a in r.attributes:
                j.add(a.name, a.type)
            junctions.append(j)
            continue
        if r.kind == "1:N":
            holder, target, end = right, left, r.right
        elif r.kind == "N:1":
            holder, target, end = left, right, r.left
        else:  # 1:1: the side that must take part holds the foreign key
            holder, target, end = (
                (left, right, r.left)
                if r.left.total and not r.right.total
                else (right, left, r.right)
            )
        t = tables[holder.name]
        prefix = snake(r.name) if holder is target else snake(target.name)
        single = len(key_cols[target.name]) == 1
        cols = foreign_key(t, target, prefix, end.total, unique=r.kind == "1:1" and single)
        if r.kind == "1:1" and not single:
            t.constraints.append(f"UNIQUE ({', '.join(cols)})")
        for a in r.attributes:
            t.add(a.name, a.type)

    ordered = _dependency_order(list(tables.values()) + junctions)
    statements = sqlglot.transpile(
        ";\n".join(t.sql() for t in ordered), read="postgres", write=dialect
    )
    return ";\n\n".join(_layout(s) for s in statements) + ";\n"


def _layout(statement: str) -> str:
    """One column or constraint per line. sqlglot's pretty mode also breaks short lists such
    as REFERENCES t (id) across lines, which reads worse."""
    head, _, rest = statement.partition("(")
    body = rest[: rest.rfind(")")]
    return f"{head}(\n  " + ",\n  ".join(_split(body)) + "\n)"


def _dependency_order(tables: list[_Table]) -> list[_Table]:
    """Referenced tables first; a cycle keeps declaration order for what is left."""
    done: list[_Table] = []
    remaining = list(tables)
    while remaining:
        names = {t.name for t in done}
        ready = next((t for t in remaining if t.depends <= names), remaining[0])
        done.append(ready)
        remaining.remove(ready)
    return done


# Either form


_CREATE_TABLE = re.compile(r"\bcreate\s+(?:temp(?:orary)?\s+)?table\b", re.IGNORECASE)


def read_any(text: str) -> tuple[Model, str]:
    """A design from either the ER text format or SQL; says which ("design" or "sql").

    SQL is recognized by a CREATE TABLE outside comments: the design format has no such words,
    so pasted DDL and uploaded dump files need no separate button.
    """
    code = "\n".join(re.split(r"--|#", line, maxsplit=1)[0] for line in text.splitlines())
    if _CREATE_TABLE.search(code):
        return from_ddl(text), "sql"
    return parse(text), "design"


# DDL -> ER


def camel(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"_+", name) if part)


@dataclass
class _Parsed:
    name: str  # lowercased schema.table, or table
    label: str  # the table name as written
    schema: str
    columns: list[tuple[str, str]]
    not_null: set[str]
    primary: list[str]
    unique: list[set[str]]
    foreign: list[tuple[list[str], str, list[str]]]  # (columns, table, referenced columns)
    checks: dict[str, set[str]] = field(default_factory=dict)  # col IN ('a', 'b') values


def from_ddl(text: str) -> Model:
    # Whole dumps work too: COPY data blocks are cut out, statements sqlglot cannot model
    # (functions, SET, GRANT) are ignored, and keys added later by ALTER TABLE are applied.
    sql, _, _ = prepare(text)
    # Bracketed names like [dbo].[t] mean SQL Server; a bracket alone (ARRAY[1]) does not.
    tsql = re.search(r"\[[A-Za-z_][\w ]*\]\s*[.(]", sql)
    dialect = "mysql" if "`" in sql else "tsql" if tsql else "postgres"
    try:
        statements = sqlglot.parse(sql, read=dialect, error_level=ErrorLevel.IGNORE)
    except SqlglotError as e:
        raise ERError(f"Could not read the SQL: {str(e).splitlines()[0]}") from None
    parsed = [
        _read_table(s)
        for s in statements
        if isinstance(s, exp.Create)
        and s.args.get("kind") == "TABLE"
        and isinstance(s.this, exp.Schema)
    ]
    by_name = {p.name: p for p in parsed}

    def resolve(key: str) -> str:
        """schema.table as written, or a bare name that only one table has."""
        if key in by_name or "." in key:
            return key
        matches = [k for k in by_name if k.rsplit(".", 1)[-1] == key]
        return matches[0] if len(matches) == 1 else key

    for s in statements:
        if isinstance(s, exp.Alter) and (altered := by_name.get(resolve(_key(s.this)))):
            for pk in s.find_all(exp.PrimaryKey):
                altered.primary.extend(i.name for i in pk.expressions)
            for fk in s.find_all(exp.ForeignKey):
                cols = [i.name for i in fk.expressions]
                altered.foreign.append((cols, *_ref(fk.args["reference"])))
    if not parsed:
        raise ERError("The SQL has no CREATE TABLE statements.")
    for p in parsed:
        p.foreign = [(cols, resolve(t), ref) for cols, t, ref in p.foreign]
    # Column names match case-insensitively, as unquoted names do in SQL.
    canon = {p.name: {c.lower(): c for c, _ in p.columns} for p in parsed}
    for p in parsed:
        fix = canon[p.name]
        p.primary = [fix.get(c.lower(), c) for c in p.primary]
        p.unique = [{fix.get(c.lower(), c) for c in u} for u in p.unique]
        p.foreign = [
            (
                [fix.get(c.lower(), c) for c in cols],
                t,
                [canon.get(t, {}).get(r.lower(), r) for r in ref],
            )
            for cols, t, ref in p.foreign
        ]
    names = {p.name for p in parsed}
    by_table = {p.name: p for p in parsed}
    # Entity names: the table's own name, with its schema when an earlier table has it.
    display: dict[str, str] = {}
    for p in parsed:
        plain = camel(p.label)
        display[p.name] = plain if plain not in display.values() else camel(p.schema) + plain

    def is_junction(p: _Parsed) -> bool:
        fk_cols = {c for cols, _, _ in p.foreign for c in cols}
        # Keyed by its two foreign keys, or with no key at all: an association table.
        return len(p.foreign) == 2 and (not p.primary or set(p.primary) == fk_cols)

    def ref_cols(target: str, ref: list[str]) -> list[str]:
        return ref or by_table[target].primary

    # A foreign key holding the table's whole key makes it a subtype of the table it
    # references; one holding part of the key makes it a weak entity owned by that table.
    isa: dict[str, tuple[str, list[str]]] = {}  # child -> (parent, discriminator columns)
    owned: dict[str, list[list[str]]] = {}  # weak table -> its identifying foreign keys
    for p in parsed:
        if is_junction(p):
            continue
        for cols, target, ref in p.foreign:
            if target not in names or target == p.name or not p.primary:
                continue
            if set(p.primary) <= set(cols) and p.name not in isa:
                parent_cols = ref_cols(target, ref)
                extra = [r for c, r in zip(cols, parent_cols, strict=False) if c not in p.primary]
                isa[p.name] = (target, extra)
            elif set(cols) < set(p.primary):
                owned.setdefault(p.name, []).append(cols)
    discriminators = {(parent, d) for parent, extra in isa.values() for d in extra}

    entities: list[Entity] = []
    relationships: list[Relationship] = []
    for p in parsed:
        if is_junction(p):
            continue
        fk_cols = {c for cols, target, _ in p.foreign if target in names for c in cols}
        own = [c for c, _ in p.columns if c not in fk_cols]
        if p.name in isa:
            keys: set[str] = set()
            hidden = fk_cols | set(p.primary)
        elif p.name in owned:
            borrowed = {c for cols in owned[p.name] for c in cols}
            keys = set(p.primary) - borrowed
            hidden = fk_cols - keys
        else:
            keys = set(p.primary) or {own[0] if own else p.columns[0][0]}
            hidden = fk_cols - keys
        hidden |= {d for parent, d in discriminators if parent == p.name}
        attrs = [
            Attribute(c, _type_from_sql(t), c in keys) for c, t in p.columns if c not in hidden
        ]
        entities.append(Entity(display[p.name], attrs, p.name in owned))
    for p in parsed:
        if is_junction(p):
            (_, a, _), (_, b, _) = p.foreign
            fk_cols = {c for cols, _, _ in p.foreign for c in cols}
            attrs = [Attribute(c, _type_from_sql(t)) for c, t in p.columns if c not in fk_cols]
            relationships.append(
                Relationship(display[p.name], End(display[a], "N"), End(display[b], "N"), attrs)
            )
            continue
        for cols, target, ref in p.foreign:
            if target not in names or (p.name in isa and isa[p.name][0] == target):
                continue
            keys_of_p = [set(p.primary), *p.unique]
            one_to_one = any(k and k <= set(cols) for k in keys_of_p)
            total = all(c in p.not_null or c in p.primary for c in cols)
            target_cols = ref_cols(target, ref)
            role = (
                _role(cols[0], target_cols) if len(cols) == 1 else _common_role(cols, target_cols)
            )
            same = cols == target_cols or role in target_cols or not role
            relationships.append(
                Relationship(
                    display[target] + display[p.name] if same else display[p.name] + camel(role),
                    End(display[target], "1"),
                    End(display[p.name], "1" if one_to_one else "N", total),
                    identifying=cols in owned.get(p.name, []),
                )
            )

    specializations: list[Specialization] = []
    for child, (parent, extra) in isa.items():
        sp = next((x for x in specializations if x.parent == display[parent]), None)
        if sp is None:
            disc = extra[0] if len(extra) == 1 else None
            values = by_table[parent].checks.get(disc or "", set())
            children = [c for c, (p2, _) in isa.items() if p2 == parent]
            sp = Specialization(
                display[parent],
                [],
                disjoint=bool(extra),
                total=bool(disc)
                and disc in by_table[parent].not_null
                and len(values) == len(children),
                discriminator=None if disc == f"{by_table[parent].label}_type" else disc,
            )
            specializations.append(sp)
        sp.children.append(display[child])
    return Model(entities, relationships, specializations)


def _common_role(cols: list[str], target_cols: list[str]) -> str:
    """r3_deptname, r3_unitid -> r3: the prefix every column puts before its target column."""
    prefixes = {
        c[: -len(t)].rstrip("_") for c, t in zip(cols, target_cols, strict=False) if c.endswith(t)
    }
    return prefixes.pop() if len(prefixes) == 1 and len(cols) == len(target_cols) else ""


def _read_table(create: exp.Create) -> _Parsed:
    schema = create.this
    table = schema.this
    db = table.args.get("db")
    p = _Parsed(_key(table), table.name, db.name if db else "", [], set(), [], [], [])
    for item in schema.expressions:
        if isinstance(item, exp.ColumnDef):
            kind = item.args.get("kind")
            p.columns.append((item.name, kind.sql() if kind else "TEXT"))
            for c in item.constraints:
                if isinstance(c.kind, exp.NotNullColumnConstraint):
                    p.not_null.add(item.name)
                elif isinstance(c.kind, exp.PrimaryKeyColumnConstraint):
                    p.primary.append(item.name)
                elif isinstance(c.kind, exp.UniqueColumnConstraint):
                    p.unique.append({item.name})
                elif isinstance(c.kind, exp.Reference):
                    p.foreign.append(([item.name], *_ref(c.kind)))
                elif isinstance(c.kind, exp.CheckColumnConstraint):
                    for found in c.kind.find_all(exp.In):
                        if found.this.name == item.name:
                            p.checks[item.name] = {v.name for v in found.expressions}
        elif isinstance(item, exp.PrimaryKey):
            p.primary.extend(i.name for i in item.expressions)
        elif isinstance(item, exp.UniqueColumnConstraint) and isinstance(item.this, exp.Schema):
            p.unique.append({i.name for i in item.this.expressions})
        elif isinstance(item, exp.ForeignKey):
            p.foreign.append(([i.name for i in item.expressions], *_ref(item.args["reference"])))
        else:
            for pk in item.find_all(exp.PrimaryKey):
                p.primary.extend(i.name for i in pk.expressions)
            for fk in item.find_all(exp.ForeignKey):
                p.foreign.append(([i.name for i in fk.expressions], *_ref(fk.args["reference"])))
    return p


def _key(table: exp.Expr) -> str:
    """schema.table or table, lowercased, as unquoted names are."""
    db = table.args.get("db")
    return f"{db.name}.{table.name}".lower() if db else str(table.name).lower()


def _ref(ref: exp.Reference) -> tuple[str, list[str]]:
    """The referenced table and columns (empty: its primary key)."""
    target = ref.this
    if isinstance(target, exp.Schema):
        return _key(target.this), [i.name for i in target.expressions]
    return _key(target), []


def _role(column: str, target_keys: list[str]) -> str:
    """professor_pid -> professor, advisor -> advisor: the name a foreign key gives its target."""
    for key in target_keys:
        if column.endswith(f"_{key}"):
            return column[: -len(key) - 1]
    return column


def _type_from_sql(sql: str) -> str:
    """The closest supported type, so a design read from DDL can be edited as text."""
    word = sql.lower()
    aliases = {
        "serial": "int",
        "bigserial": "bigint",
        "double precision": "double",
        "float": "real",
    }
    try:
        return _type(aliases.get(word, word), 0)
    except ERError:
        base = word.split("(")[0]
        return _TYPES.get(base, "TEXT")
