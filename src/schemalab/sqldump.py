"""Read a .sql dump (pg_dump, mysqldump, SQLite .dump) without running any of it.

sqlglot parses the file. CREATE TABLE gives names and column types, rebuilt from the parse tree
with constraints, defaults and sequences dropped. INSERT ... VALUES rows (constants only) and
pg_dump COPY ... FROM stdin blocks are written to one CSV file per table, which `uploads`
loads. Before parsing, `prepare` splits PostgreSQL's CREATE SCHEMA s CREATE TABLE t ... form
into one statement per table (s.t), replaces CREATE DOMAIN types with their base types, and
drops psql meta-commands such as \\copy, counting them.
Primary and foreign keys are recorded so the overview and the model can see how tables
join. Every other statement is skipped and counted, so the user sees what was left out.
"""

import csv
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

NULL = r"\N"  # how a missing value is written to the CSV files, as in pg_dump

_DT = exp.DataType.Type
_SERIAL = {_DT.SERIAL: "INT", _DT.BIGSERIAL: "BIGINT", _DT.SMALLSERIAL: "SMALLINT"}
_SAFE_TYPE = re.compile(r"[A-Z][A-Z0-9_ ]*(\(\d+(, ?\d+)?\))?(\[\])?")
_COPY = re.compile(r"COPY\s+(.+?)\s*(?:\((.*)\))?\s+FROM\s+stdin;?\s*", re.IGNORECASE)
_MODIFIERS = {"OR", "REPLACE", "TEMP", "TEMPORARY", "UNIQUE", "IF", "NOT", "EXISTS"}
_PG_ESCAPE = re.compile(r"\\(.)")
_PG_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "b": "\b", "f": "\f", "v": "\v"}


class DumpError(ValueError):
    """The file cannot be read as SQL. The message is safe to show the user."""


class _NotConstant(Exception):
    pass


@dataclass
class DumpTable:
    name: str
    columns: list[tuple[str, str]]
    csv: Path
    row_count: int = 0
    source_columns: dict[str, int] = field(default_factory=dict, repr=False)


@dataclass
class Dump:
    tables: list[DumpTable]
    keys: dict[tuple[str, str], str]  # (table, column) -> "primary key" / "references t(c)"
    skipped: Counter[str]


def ident(text: str, taken: set[str] | None = None) -> str:
    """A lowercase [a-z0-9_] identifier, unique among `taken` when given."""
    name = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:50] or "x"
    if name[0].isdigit():
        name = f"t_{name}"
    if taken is None:
        return name
    unique, n = name, 2
    while unique in taken:
        unique, n = f"{name}_{n}", n + 1
    return unique


def read_dump(text: str, work: Path) -> Dump:
    sql, copies, meta = prepare(text)
    dialect = "mysql" if "`" in sql else "postgres"
    try:
        statements = sqlglot.parse(sql, read=dialect, error_level=ErrorLevel.IGNORE)
    except SqlglotError as e:
        first = str(e).splitlines()[0] if str(e) else type(e).__name__
        raise DumpError(f"Could not read the SQL: {first}") from None

    reader = _Reader(work)
    reader.skipped.update(meta)
    try:
        for statement in statements:
            if statement is not None:
                reader.statement(statement)
        for header, lines in copies:
            reader.copy(header, lines)
    finally:
        reader.close()
    if not reader.tables:
        raise DumpError("The file has no CREATE TABLE statements to load.")
    return Dump(list(reader.tables.values()), reader.keys, reader.skipped)


_LEADING_COMMENTS = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*", re.S)
_META = re.compile(r"^\s*\\(\w+)")
_SCHEMA_HEAD = re.compile(
    r"CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?(\"[^\"]+\"|\w+)(?:\s+AUTHORIZATION\s+\S+)?",
    re.IGNORECASE,
)
_ELEMENT_NAME = re.compile(
    r"(CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNLOGGED\s+)?(?:TABLE|VIEW|SEQUENCE)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?)(\"[^\"]+\"|\w+)(?!\s*\.)",
    re.IGNORECASE,
)
_DOMAIN = re.compile(
    r"CREATE\s+DOMAIN\s+(?:\w+\.)?(\"[^\"]+\"|\w+)\s+(?:AS\s+)?"
    r"(\w+(?:\s+(?:precision|varying))?(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)",
    re.IGNORECASE,
)


def prepare(text: str) -> tuple[str, list[tuple[re.Match[str], list[str]]], Counter[str]]:
    """The SQL as sqlglot can read it, the COPY data blocks, and what was dropped."""
    sql, copies = split_copy_blocks(text)
    dropped: Counter[str] = Counter()
    kept = []
    for line in sql.splitlines():
        meta = _META.match(line)
        if not meta:
            kept.append(line)
        elif meta[1].lower() == "copy":
            dropped["\\copy from a CSV file not in this upload"] += 1
        else:
            dropped[f"psql \\{meta[1]}"] += 1
    statements = []
    for statement in _statements("\n".join(kept)):
        statement = _LEADING_COMMENTS.sub("", statement)
        head = _SCHEMA_HEAD.match(statement)
        if head and _elements(statement[head.end() :]):
            statements.append(head[0])
            for element in _elements(statement[head.end() :]):
                statements.append(_ELEMENT_NAME.sub(rf"\1{head[1]}.\2", element, count=1))
        else:
            statements.append(statement)
    domains = {}
    for statement in statements:
        if found := _DOMAIN.match(statement):
            domains[found[1]] = found[2]
    out = []
    for statement in statements:
        if not _DOMAIN.match(statement):
            for name, base in domains.items():
                # Only where a type goes: after a column name that starts a line or follows ( or ,
                pattern = rf'((?:^|[(,])\s*"?\w+"?\s+){re.escape(name)}(?=[\s,)]|$)'
                flags = re.MULTILINE | (0 if name.startswith('"') else re.IGNORECASE)
                statement = re.sub(pattern, rf"\g<1>{base}", statement, flags=flags)
        out.append(statement)
    return ";\n".join(out) + (";" if out else ""), copies, dropped


def _scan(sql: str) -> Iterator[tuple[int, str, int]]:
    """(index, character, parenthesis depth) for characters outside quotes, dollar quotes and
    comments."""
    i, depth, n = 0, 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            i = sql.find("\n", i)
            i = n if i < 0 else i
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch in "'\"":
            end = i + 1
            while end < n:
                if sql[end] == ch and sql.startswith(ch * 2, end):
                    end += 2
                elif sql[end] == ch:
                    break
                else:
                    end += 1
            i = end + 1
            continue
        if ch == "$" and (tag := re.match(r"\$\w*\$", sql[i:])):
            end = sql.find(tag[0], i + len(tag[0]))
            i = n if end < 0 else end + len(tag[0])
            continue
        depth += {"(": 1, ")": -1}.get(ch, 0)
        yield i, ch, depth
        i += 1


def _statements(sql: str) -> list[str]:
    out, start = [], 0
    for i, ch, _ in _scan(sql):
        if ch == ";":
            out.append(sql[start:i])
            start = i + 1
    out.append(sql[start:])
    return [s.strip() for s in out if s.strip()]


def _elements(body: str) -> list[str]:
    """The CREATE and GRANT statements nested in a CREATE SCHEMA, split where each begins."""
    starts = [
        i
        for i, _, depth in _scan(body)
        if depth == 0
        and (i == 0 or not (body[i - 1].isalnum() or body[i - 1] == "_"))
        and re.match(r"(CREATE|GRANT)\s", body[i:], re.IGNORECASE)
    ]
    return [body[a:b].strip() for a, b in zip(starts, [*starts[1:], len(body)], strict=True)]


def split_copy_blocks(text: str) -> tuple[str, list[tuple[re.Match[str], list[str]]]]:
    """Take pg_dump `COPY ... FROM stdin` data out of the SQL, which sqlglot cannot parse."""
    sql: list[str] = []
    copies: list[tuple[re.Match[str], list[str]]] = []
    lines = iter(text.splitlines())
    for line in lines:
        header = _COPY.fullmatch(line.strip())
        if not header:
            sql.append(line)
            continue
        data = []
        for row in lines:
            if row == r"\.":
                break
            data.append(row)
        copies.append((header, data))
    return "\n".join(sql), copies


class _Reader:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.tables: dict[str, DumpTable] = {}  # by lowercased schema.name or name
        self.keys: dict[tuple[str, str], str] = {}
        self.skipped: Counter[str] = Counter()
        self._files: dict[str, tuple[IO[str], Any]] = {}

    def close(self) -> None:
        for f, _ in self._files.values():
            f.close()

    def statement(self, node: exp.Expr) -> None:
        if isinstance(node, exp.Create) and node.args.get("kind") == "TABLE":
            if isinstance(node.this, exp.Schema) and not node.expression:
                return self.create(node.this)
            self.skipped["CREATE TABLE AS"] += 1
        elif isinstance(node, exp.Insert):
            self.insert(node)
        elif isinstance(node, exp.Alter) and self.alter_keys(node):
            return
        else:
            self.skipped[_kind(node)] += 1

    def find(self, node: exp.Expr) -> DumpTable | None:
        """The table a name refers to: schema.table exactly, or a bare name if only one table
        has it (as the search path would find it)."""
        table = node.this if isinstance(node, exp.Schema) else node
        name, db = table.name.lower(), table.args.get("db")
        if db:
            return self.tables.get(f"{db.name.lower()}.{name}")
        if name in self.tables:
            return self.tables[name]
        matches = [t for k, t in self.tables.items() if k.rsplit(".", 1)[-1] == name]
        return matches[0] if len(matches) == 1 else None

    def create(self, schema: exp.Schema) -> None:
        source = schema.this.name
        db = schema.this.args.get("db")
        table_names = {t.name for t in self.tables.values()}
        clash = ident(source) in table_names and db
        name = ident(f"{db.name}_{source}" if clash else source, table_names)
        columns: list[tuple[str, str]] = []
        source_columns: dict[str, int] = {}
        for item in schema.expressions:
            if isinstance(item, exp.ColumnDef):
                col = ident(item.name, {c for c, _ in columns})
                source_columns[item.name.lower()] = len(columns)
                columns.append((col, _type(item.args.get("kind"))))
        table = DumpTable(name, columns, self.work / f"{name}.csv", source_columns=source_columns)
        self.tables[f"{db.name.lower()}.{source.lower()}" if db else source.lower()] = table
        f = table.csv.open("w", newline="")
        writer = csv.writer(f)
        writer.writerow([c for c, _ in columns])
        self._files[name] = (f, writer)
        self.add_keys(table, schema)

    def add_keys(self, table: DumpTable, node: exp.Expr) -> bool:
        found = False
        for pk in node.find_all(exp.PrimaryKey):
            cols = [self.column(table, i.name) for i in pk.expressions]
            note = "primary key" if len(cols) == 1 else "part of the primary key"
            for name in filter(None, cols):
                self.keys[(table.name, name)] = note
            found = True
        for column in node.find_all(exp.ColumnDef):
            own = self.column(table, column.name)
            for c in column.constraints:
                if own and isinstance(c.kind, exp.PrimaryKeyColumnConstraint):
                    self.keys[(table.name, own)] = "primary key"
                elif own and isinstance(c.kind, exp.Reference):
                    self.keys[(table.name, own)] = self.reference(c.kind)
        for fk in node.find_all(exp.ForeignKey):
            note = self.reference(fk.args["reference"])
            for i in fk.expressions:
                if col := self.column(table, i.name):
                    self.keys[(table.name, col)] = note
            found = True
        return found

    def alter_keys(self, node: exp.Alter) -> bool:
        table = self.find(node.this)
        return table is not None and self.add_keys(table, node)

    def reference(self, ref: exp.Reference) -> str:
        target = ref.this
        table_node = target.this if isinstance(target, exp.Schema) else target
        other = self.find(table_node)
        table = other.name if other else ident(table_node.name)
        cols = target.expressions if isinstance(target, exp.Schema) else []
        if not cols:
            return f"references {table}"
        col = (other and self.column(other, cols[0].name)) or ident(cols[0].name)
        return f"references {table}({col})"

    def column(self, table: DumpTable, source: str) -> str | None:
        i = table.source_columns.get(source.lower())
        return None if i is None else table.columns[i][0]

    def insert(self, node: exp.Insert) -> None:
        target = node.this
        table = self.find(target)
        if table is None:
            self.skipped["INSERT into a table the file never creates"] += 1
            return
        if not isinstance(node.expression, exp.Values):
            self.skipped["INSERT ... SELECT"] += 1
            return
        names = [i.name for i in target.expressions] if isinstance(target, exp.Schema) else None
        try:
            rows = [[_const(v) for v in t.expressions] for t in node.expression.expressions]
        except _NotConstant:
            self.skipped["INSERT with expressions"] += 1
            return
        if not self.write(table, names, rows):
            self.skipped["INSERT that does not match its table"] += 1

    def copy(self, header: re.Match[str], lines: list[str]) -> None:
        table = self.find(exp.to_table(header.group(1), dialect="postgres"))
        if table is None:
            self.skipped["COPY into a table the file never creates"] += 1
            return
        names = None
        if header.group(2):
            select = sqlglot.parse_one(f"SELECT {header.group(2)}", read="postgres")
            names = [c.name for c in select.expressions]
        rows = (
            [None if v == NULL else _pg_unescape(v) for v in line.split("\t")] for line in lines
        )
        if not self.write(table, names, rows):
            self.skipped["COPY that does not match its table"] += 1

    def write(
        self, table: DumpTable, names: list[str] | None, rows: Iterator[list[Any]] | list[list[Any]]
    ) -> bool:
        width = len(table.columns)
        positions: list[int | None] = (
            list(range(width))
            if names is None
            else [table.source_columns.get(n.lower()) for n in names]
        )
        if any(p is None for p in positions):
            return False
        _, writer = self._files[table.name]
        for row in rows:
            if len(row) != len(positions):
                return False
            out: list[Any] = [NULL] * width
            for p, v in zip(positions, row, strict=True):
                out[p] = NULL if v is None else v  # type: ignore[index]
            writer.writerow(out)
            table.row_count += 1
        return True


def _type(kind: exp.DataType | None) -> str:
    """A type DuckDB has: SERIAL becomes INT, enums and unknown types VARCHAR."""
    if kind is None:
        return "VARCHAR"
    if kind.this in _SERIAL:
        return _SERIAL[kind.this]
    if kind.this == _DT.USERDEFINED:
        return "VARCHAR"
    sql = kind.sql()  # generic names (VARCHAR(40), DECIMAL(10, 2)); `uploads` checks them
    return sql if _SAFE_TYPE.fullmatch(sql) else "VARCHAR"


def _const(node: exp.Expr) -> Any:
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Boolean):
        return "true" if node.this else "false"
    if isinstance(node, exp.Literal):
        return node.this
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
        return f"-{node.this.this}"
    if isinstance(node, exp.Cast):
        return _const(node.this)
    raise _NotConstant


def _pg_unescape(value: str) -> str:
    return _PG_ESCAPE.sub(lambda m: _PG_ESCAPES.get(m.group(1), m.group(1)), value)


def _kind(node: exp.Expr) -> str:
    if isinstance(node, exp.Command):
        # sqlglot keeps statements it does not model, like CREATE FUNCTION, as raw commands.
        words = f"{node.this} {node.expression or ''}".upper().split()
        if words[0] in {"CREATE", "DROP", "ALTER"}:
            noun = next((w for w in words[1:] if w not in _MODIFIERS), "")
            return f"{words[0]} {noun}".strip()
        return words[0]
    if isinstance(node, exp.Create | exp.Drop):
        return f"{node.key.upper()} {node.args.get('kind') or ''}".strip()
    return node.key.upper()
