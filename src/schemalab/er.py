"""ER designs: a small text format, ER -> DDL in five dialects, and DDL -> ER.

    entity Student { sid int key, name text, gpa decimal(3,2) }
    entity Course { cid varchar(10) key, title text }
    relationship Advises: Professor 1 -- N Student
    relationship Enrolls: Student M -- N Course! { grade text }

`1`, `N` and `M` sit next to the entity they count, as in Chen notation: one professor advises
N students. `!` after an entity marks total participation (every Course has an enrollment).

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

from schemalab.sqldump import split_copy_blocks

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
_ENTITY = re.compile(rf"entity\s+({_NAME})\s*\{{(.*)\}}\s*", re.S)


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

    @property
    def kind(self) -> str:
        if self.left.card == "N" and self.right.card == "N":
            return "M:N"
        return f"{self.left.card}:{self.right.card}"


@dataclass(frozen=True)
class Model:
    entities: list[Entity]
    relationships: list[Relationship]


# Text format


def parse(text: str) -> Model:
    entities: list[Entity] = []
    relationships: list[Relationship] = []
    for line, statement in _statements(text):
        if statement.startswith("entity"):
            match = _ENTITY.fullmatch(statement)
            if not match:
                raise ERError(f"line {line}: write an entity as entity Name {{ id int key, ... }}.")
            name = _check_name(match[1], line)
            if any(e.name == name for e in entities):
                raise ERError(f"line {line}: {name} is declared twice.")
            attrs = _attributes(match[2], line)
            if not any(a.key for a in attrs):
                raise ERError(f"line {line}: {name} has no key attribute; mark one with key.")
            entities.append(Entity(name, attrs))
        elif statement.startswith("relationship"):
            match = _RELATIONSHIP.fullmatch(statement)
            if not match:
                raise ERError(
                    f"line {line}: write a relationship as "
                    "relationship Name: EntityA 1 -- N EntityB { attr type }."
                )
            name, a, a_total, a_card, b_card, b, b_total, body = match.groups()
            for entity in (a, b):
                if not any(e.name == entity for e in entities):
                    raise ERError(f"line {line}: {entity} is not an entity declared above.")
            relationships.append(
                Relationship(
                    _check_name(name, line),
                    End(a, _card(a_card), bool(a_total)),
                    End(b, _card(b_card), bool(b_total)),
                    _attributes(body or "", line, keys=False),
                )
            )
        else:
            raise ERError(f"line {line}: start a line with entity or relationship.")
    if not entities:
        raise ERError("Describe at least one entity, like entity Student { sid int key }.")
    if len(entities) > MAX_ENTITIES:
        raise ERError(f"Use at most {MAX_ENTITIES} entities.")
    return Model(entities, relationships)


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
        f"entity {e.name} {{ {', '.join(attr(a) for a in e.attributes)} }}" for e in model.entities
    ]
    if model.relationships:
        lines.append("")
    for r in model.relationships:
        left = "M" if r.kind == "M:N" else r.left.card
        body = f" {{ {', '.join(attr(a, False) for a in r.attributes)} }}" if r.attributes else ""
        lines.append(
            f"relationship {r.name}: {r.left.entity}{'!' if r.left.total else ''} {left} -- "
            f"{r.right.card} {r.right.entity}{'!' if r.right.total else ''}{body}"
        )
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
    tables: dict[str, _Table] = {}
    for e in model.entities:
        t = _Table(snake(e.name))
        for a in e.attributes:
            t.add(a.name, _column_type(a, a.key) + (" NOT NULL" if a.key else ""))
        t.constraints.append(f"PRIMARY KEY ({', '.join(a.name for a in e.keys)})")
        tables[e.name] = t

    def foreign_key(t: _Table, target: Entity, prefix: str, total: bool, unique: bool) -> list[str]:
        cols = [
            t.add(
                f"{prefix}_{k.name}",
                _column_type(k, True)
                + (" NOT NULL" if total else "")
                + (" UNIQUE" if unique else ""),
            )
            for k in target.keys
        ]
        target_table = tables[target.name].name
        t.constraints.append(
            f"FOREIGN KEY ({', '.join(cols)}) REFERENCES {target_table} "
            f"({', '.join(k.name for k in target.keys)})"
        )
        if target_table != t.name:
            t.depends.add(target_table)
        return cols

    junctions: list[_Table] = []
    for r in model.relationships:
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
        single = len(target.keys) == 1
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
    name: str
    columns: list[tuple[str, str]]
    not_null: set[str]
    primary: list[str]
    unique: list[set[str]]
    foreign: list[tuple[list[str], str]]  # (columns, referenced table)


def from_ddl(text: str) -> Model:
    # Whole dumps work too: COPY data blocks are cut out, statements sqlglot cannot model
    # (functions, SET, GRANT) are ignored, and keys added later by ALTER TABLE are applied.
    sql, _ = split_copy_blocks(text)
    dialect = "mysql" if "`" in sql else "tsql" if "[" in sql else "postgres"
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
    for s in statements:
        if isinstance(s, exp.Alter) and (altered := by_name.get(s.this.name)):
            for pk in s.find_all(exp.PrimaryKey):
                altered.primary.extend(i.name for i in pk.expressions)
            for fk in s.find_all(exp.ForeignKey):
                cols = [i.name for i in fk.expressions]
                altered.foreign.append((cols, _ref_table(fk.args["reference"])))
    if not parsed:
        raise ERError("The SQL has no CREATE TABLE statements.")
    names = {p.name for p in parsed}
    by_table = {p.name: p for p in parsed}

    def is_junction(p: _Parsed) -> bool:
        fk_cols = {c for cols, _ in p.foreign for c in cols}
        # Keyed by its two foreign keys, or with no key at all: an association table.
        return len(p.foreign) == 2 and (not p.primary or set(p.primary) == fk_cols)

    entities: list[Entity] = []
    relationships: list[Relationship] = []
    for p in parsed:
        if is_junction(p):
            continue
        fk_cols = {c for cols, target in p.foreign if target in names for c in cols}
        own = [c for c, _ in p.columns if c not in fk_cols]
        keys = set(p.primary) or {own[0] if own else p.columns[0][0]}
        attrs = [
            Attribute(c, _type_from_sql(t), c in keys)
            for c, t in p.columns
            if c not in fk_cols or c in keys
        ]
        entities.append(Entity(camel(p.name), attrs))
    for p in parsed:
        if is_junction(p):
            (_, a), (_, b) = p.foreign
            fk_cols = {c for cols, _ in p.foreign for c in cols}
            attrs = [Attribute(c, _type_from_sql(t)) for c, t in p.columns if c not in fk_cols]
            relationships.append(
                Relationship(camel(p.name), End(camel(a), "N"), End(camel(b), "N"), attrs)
            )
            continue
        for cols, target in p.foreign:
            if target not in names:
                continue
            one_to_one = set(cols) in p.unique or set(cols) == set(p.primary)
            total = all(c in p.not_null or c in p.primary for c in cols)
            relationships.append(
                Relationship(
                    camel(p.name) + camel(_role(cols[0], by_table[target].primary)),
                    End(camel(target), "1"),
                    End(camel(p.name), "1" if one_to_one else "N", total),
                )
            )
    return Model(entities, relationships)


def _read_table(create: exp.Create) -> _Parsed:
    schema = create.this
    p = _Parsed(schema.this.name, [], set(), [], [], [])
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
                    p.foreign.append(([item.name], _ref_table(c.kind)))
        elif isinstance(item, exp.PrimaryKey):
            p.primary.extend(i.name for i in item.expressions)
        elif isinstance(item, exp.UniqueColumnConstraint) and isinstance(item.this, exp.Schema):
            p.unique.append({i.name for i in item.this.expressions})
        elif isinstance(item, exp.ForeignKey):
            p.foreign.append(
                ([i.name for i in item.expressions], _ref_table(item.args["reference"]))
            )
        else:
            for pk in item.find_all(exp.PrimaryKey):
                p.primary.extend(i.name for i in pk.expressions)
            for fk in item.find_all(exp.ForeignKey):
                p.foreign.append(
                    ([i.name for i in fk.expressions], _ref_table(fk.args["reference"]))
                )
    return p


def _ref_table(ref: exp.Reference) -> str:
    target = ref.this
    table = target.this if isinstance(target, exp.Schema) else target
    return str(table.name)


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
