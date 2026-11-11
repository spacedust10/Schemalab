"""Relational algebra: a parser, RA -> SQL, SQL -> RA, and a tree for drawing.

Expressions use the textbook symbols or ASCII words:

    π[name](σ[gpa > 3.5](student))          project[name](select[gpa > 3.5](student))
    student ⋈ enrolls                       student join enrolls          (natural join)
    student ⋈[student.sid = e.sid] ρ[e](enrolls)
    R × S, R ∪ S, R − S, R ∩ S              cross, union, minus, intersect
    γ[cid; count(*) AS n](enrolls)          group[...]  (grouping and aggregates)
    τ[n DESC](...)                          sort[...]

Conditions are SQL boolean expressions; ∧ ∨ ¬ ≠ ≤ ≥ are accepted. Projection follows set
semantics (SELECT DISTINCT), as in the algebra. Joins and selections over plain relations
become one SELECT; anything else nests as a subquery, one per operator.
"""

import re
from dataclasses import dataclass, field
from itertools import count
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


class RAError(ValueError):
    """The expression or query cannot be translated. The message says where and why."""


@dataclass(frozen=True)
class Node:
    op: str
    arg: str | None = None
    children: tuple["Node", ...] = ()


UNARY = {"σ": "select", "π": "project", "ρ": "rename", "γ": "group", "τ": "sort"}
JOINS = {"⋈": "join", "⟕": "left", "⟖": "right", "⟗": "full"}
SETS = {"∪": "union", "−": "difference", "∩": "intersect"}
SYMBOL = {v: k for k, v in {**UNARY, **JOINS, **SETS, "×": "cross"}.items()}
WORDS = {
    "select": "σ", "sigma": "σ", "project": "π", "pi": "π", "rename": "ρ", "rho": "ρ",
    "group": "γ", "gamma": "γ", "sort": "τ", "tau": "τ", "join": "⋈", "leftjoin": "⟕",
    "rightjoin": "⟖", "fulljoin": "⟗", "cross": "×", "times": "×", "union": "∪",
    "minus": "−", "except": "−", "difference": "−", "intersect": "∩",
}  # fmt: skip
_CONDITION = {"∧": " AND ", "∨": " OR ", "¬": " NOT ", "≠": "<>", "≤": "<=", "≥": ">="}
_RENAME = re.compile(r"\s*([A-Za-z_]\w*)\s*(?:\(\s*([\w\s,]+)\))?\s*")


# Parsing


@dataclass
class _Token:
    kind: str  # "op", "arg", "name", "(", ")", "end"
    text: str
    pos: int  # 1-based


def _tokens(text: str) -> list[_Token]:
    out: list[_Token] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "[":
            depth, j = 1, i + 1
            while j < len(text) and depth:
                depth += {"[": 1, "]": -1}.get(text[j], 0)
                j += 1
            if depth:
                raise RAError(f"the [ at position {i + 1} is never closed")
            out.append(_Token("arg", text[i + 1 : j - 1], i + 1))
            i = j
        elif ch in "()":
            out.append(_Token(ch, ch, i + 1))
            i += 1
        elif ch in UNARY or ch in JOINS or ch in SETS or ch == "×":
            out.append(_Token("op", ch, i + 1))
            i += 1
        elif ch == "-":
            out.append(_Token("op", "−", i + 1))
            i += 1
        elif ch.isalpha() or ch == "_":
            m = re.match(r"[A-Za-z_][\w.]*", text[i:])
            word = m[0] if m else ch
            symbol = WORDS.get(word.lower())
            out.append(_Token("op", symbol, i + 1) if symbol else _Token("name", word, i + 1))
            i += len(word)
        else:
            raise RAError(f"unexpected {ch} at position {i + 1}")
    out.append(_Token("end", "", len(text) + 1))
    return out


def parse_ra(text: str) -> Node:
    if not text.strip():
        raise RAError("Write a relational algebra expression, like π[name](student).")
    tokens = _tokens(text)
    parser = _Parser(tokens)
    node = parser.expr()
    leftover = parser.peek()
    if leftover.kind != "end":
        raise RAError(f"unexpected {leftover.text} at position {leftover.pos}")
    return node


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> _Token:
        return self.tokens[self.i]

    def take(self) -> _Token:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def expect(self, kind: str) -> _Token:
        token = self.peek()
        if token.kind != kind:
            raise RAError(f"expected {kind} at position {token.pos}")
        return self.take()

    def expr(self) -> Node:
        node = self.term()
        while self.peek().kind == "op" and self.peek().text in SETS:
            op = SETS[self.take().text]
            node = Node(op, None, (node, self.term()))
        return node

    def term(self) -> Node:
        node = self.unary()
        while self.peek().kind == "op" and (self.peek().text in JOINS or self.peek().text == "×"):
            symbol = self.take().text
            if symbol == "×":
                node = Node("cross", None, (node, self.unary()))
                continue
            arg = None
            if self.peek().kind == "arg":
                arg = _condition(self.take().text)
            elif symbol != "⋈":
                raise RAError(f"{symbol} needs a join condition in [ ]")
            node = Node(JOINS[symbol], arg, (node, self.unary()))
        return node

    def unary(self) -> Node:
        token = self.peek()
        if token.kind == "op" and token.text in UNARY:
            self.take()
            if self.peek().kind != "arg":
                raise RAError(f"{token.text} at position {token.pos} needs [ ] after it")
            op = UNARY[token.text]
            arg = _argument(op, self.take().text)
            self.expect("(")
            child = self.expr()
            self.expect(")")
            return Node(op, arg, (child,))
        if token.kind == "(":
            self.take()
            node = self.expr()
            self.expect(")")
            return node
        if token.kind == "name":
            return Node("relation", self.take().text)
        raise RAError(f"expected a relation or an operator at position {token.pos}")


def _validate(sql: str, what: str) -> None:
    """Refuse text that is not valid SQL there, or that hides a subquery."""
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except SqlglotError:
        raise RAError(f"could not read the {what}") from None
    if any(n is not tree for n in tree.find_all(exp.Select, exp.Subquery)):
        raise RAError(f"{what}s cannot contain subqueries")


def _condition(text: str) -> str:
    for symbol, sql in _CONDITION.items():
        text = text.replace(symbol, sql)
    cond = re.sub(r"\s+", " ", text).strip()
    if not cond:
        raise RAError("the condition is empty")
    _validate(f"SELECT 1 WHERE {cond}", "condition")
    return cond


def _argument(op: str, text: str) -> str:
    if op == "select":
        return _condition(text)
    arg = re.sub(r"\s+", " ", text).strip()
    if not arg:
        raise RAError(f"{SYMBOL[op]} needs something inside [ ]")
    if op == "rename":
        if not _RENAME.fullmatch(arg):
            raise RAError("write a rename as ρ[name](R) or ρ[name(a, b)](R)")
    elif op == "group":
        groups, _, aggs = arg.partition(";")
        listed = ", ".join(p for p in (groups.strip(), aggs.strip()) if p)
        _validate(f"SELECT {listed}", "grouping")
    elif op == "sort":
        _validate(f"SELECT 1 ORDER BY {arg}", "sort order")
    else:
        _validate(f"SELECT {arg}", "projection")
    return arg


# RA -> SQL


@dataclass
class _Frame:
    """One SELECT under construction; `raw` holds a finished set-operation query."""

    source: str = ""
    where: list[str] = field(default_factory=list)
    select: str = "*"
    distinct: bool = False
    group: str | None = None
    order: str | None = None
    raw: str | None = None

    @property
    def open(self) -> bool:  # can take another WHERE, join or projection without nesting
        return self.raw is None and self.select == "*" and not self.group and not self.order

    def sql(self) -> str:
        if self.raw is not None:
            return self.raw
        # Built from the user's own algebra; it only runs through guards.run.run_query.
        distinct = "DISTINCT " if self.distinct else ""
        parts = [f"SELECT {distinct}{self.select} FROM {self.source}"]  # noqa: S608
        if self.where:
            parts.append(
                "WHERE " + " AND ".join(f"({w})" if " OR " in w.upper() else w for w in self.where)
            )
        if self.group:
            parts.append(f"GROUP BY {self.group}")
        if self.order:
            parts.append(f"ORDER BY {self.order}")
        return " ".join(parts)


def to_sql(node: Node) -> str:
    names = count(1)

    def wrap(f: _Frame) -> str:
        return f"({f.sql()}) AS t{next(names)}"

    def item(f: _Frame) -> tuple[str, list[str]]:
        """A FROM item and the conditions that can move to the outer WHERE."""
        if f.open:
            joined = " JOIN " in f.source
            return (f"({f.source})" if joined else f.source), f.where
        return wrap(f), []

    def build(n: Node) -> _Frame:
        if n.op == "relation":
            return _Frame(source=str(n.arg))
        if n.op in SETS.values():
            keyword = {"union": "UNION", "difference": "EXCEPT", "intersect": "INTERSECT"}[n.op]
            first, second = (build(c).sql() for c in n.children)
            return _Frame(raw=f"{first} {keyword} {second}")
        if n.op in ("join", "left", "right", "full", "cross"):
            left, right = (build(c) for c in n.children)
            outer = n.op in ("left", "right", "full")
            if outer and (left.where or right.where):
                left_item, right_item, moved = wrap(left), wrap(right), []
            else:
                (left_item, lw), (right_item, rw) = item(left), item(right)
                left_item = left.source if left.open else left_item  # chains stay flat
                moved = lw + rw
            keyword = {
                "join": "JOIN" if n.arg else "NATURAL JOIN",
                "left": "LEFT JOIN",
                "right": "RIGHT JOIN",
                "full": "FULL JOIN",
                "cross": "CROSS JOIN",
            }[n.op]
            on = f" ON {n.arg}" if n.arg else ""
            return _Frame(source=f"{left_item} {keyword} {right_item}{on}", where=moved)
        child = build(n.children[0])
        if n.op == "rename":
            alias = _alias(str(n.arg))
            if child.raw is None and child.open and not child.where and " " not in child.source:
                return _Frame(source=f"{child.source} AS {alias}")
            return _Frame(source=f"({child.sql()}) AS {alias}")
        if not child.open:
            child = _Frame(source=wrap(child))
        if n.op == "select":
            return _Frame(source=child.source, where=[*child.where, str(n.arg)])
        if n.op == "project":
            return _Frame(source=child.source, where=child.where, select=str(n.arg), distinct=True)
        if n.op == "group":
            groups, _, aggs = str(n.arg).partition(";")
            groups, aggs = groups.strip(), aggs.strip()
            select = ", ".join(p for p in (groups, aggs) if p)
            return _Frame(
                source=child.source, where=child.where, select=select, group=groups or None
            )
        if n.op == "sort":
            return _Frame(source=child.source, where=child.where, order=str(n.arg))
        raise RAError(f"unknown operator {n.op}")

    return build(node).sql()


def _alias(arg: str) -> str:
    """ρ[s] -> s, ρ[s(a, b)] -> s(a, b): a table alias with optional column names."""
    m = _RENAME.fullmatch(arg)
    if not m:
        return arg
    columns = f"({', '.join(c.strip() for c in m[2].split(','))})" if m[2] else ""
    return m[1] + columns


# SQL -> RA


@dataclass
class Translation:
    node: Node
    notes: list[str]


def from_sql(sql: str) -> Translation:
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except SqlglotError as e:
        raise RAError(f"Could not read the SQL: {str(e).splitlines()[0]}") from None
    if len(statements) != 1:
        raise RAError("Give exactly one query.")
    notes: list[str] = []
    return Translation(_query(cast(exp.Expression, statements[0]), notes), notes)


def _query(q: exp.Expression, notes: list[str]) -> Node:
    if isinstance(q, exp.Union | exp.Except | exp.Intersect):
        op = {exp.Union: "union", exp.Except: "difference", exp.Intersect: "intersect"}[type(q)]
        if not q.args.get("distinct", True):
            notes.append(
                f"{type(q).__name__.upper()} ALL keeps duplicates; the algebra works on sets."
            )
        return Node(op, None, (_query(q.this, notes), _query(q.expression, notes)))
    if isinstance(q, exp.Subquery):
        return _query(q.this, notes)
    if not isinstance(q, exp.Select):
        raise RAError("Only SELECT queries and set operations translate to the algebra.")
    if q.args.get("with"):
        raise RAError("WITH is not supported; inline the subquery.")

    source = q.args.get("from_")  # sqlglot names it from_
    if source is None:
        raise RAError("A query without FROM has no relational algebra form.")
    node = _from_item(source.this, notes)
    for join in q.args.get("joins") or []:
        right = _from_item(join.this, notes)
        if join.args.get("using"):
            raise RAError("JOIN ... USING is not supported; write the condition with ON.")
        on = join.args.get("on")
        side = (join.args.get("side") or "").upper()
        if join.args.get("method", "").upper() == "NATURAL":
            node = Node("join", None, (node, right))
        elif side in ("LEFT", "RIGHT", "FULL"):
            node = Node(side.lower(), on.sql(dialect="duckdb") if on else None, (node, right))
        elif on is not None:
            node = Node("join", on.sql(dialect="duckdb"), (node, right))
        else:
            node = Node("cross", None, (node, right))

    where = q.args.get("where")
    if where is not None:
        if where.find(exp.Select, exp.Subquery):
            raise RAError("A subquery in WHERE has no direct form here; rewrite it as a join.")
        node = Node("select", where.this.sql(dialect="duckdb"), (node,))

    columns = [e.sql(dialect="duckdb") for e in q.expressions]
    star = len(q.expressions) == 1 and isinstance(q.expressions[0], exp.Star)
    group = q.args.get("group")
    has_agg = any(e.find(exp.AggFunc) for e in q.expressions)
    if group is not None or has_agg:
        groups = [g.sql(dialect="duckdb") for g in group.expressions] if group else []
        aggs = [c for c, e in zip(columns, q.expressions, strict=True) if e.find(exp.AggFunc)]
        node = Node("group", f"{', '.join(groups)}; {', '.join(aggs)}".strip(), (node,))
        having = q.args.get("having")
        if having is not None:
            node = Node("select", having.this.sql(dialect="duckdb"), (node,))
        plain = [c for c, e in zip(columns, q.expressions, strict=True) if not e.find(exp.AggFunc)]
        if plain != groups:
            names = [e.alias_or_name for e in q.expressions]
            node = Node("project", ", ".join(names), (node,))
    elif not star:
        node = Node("project", ", ".join(columns), (node,))
        if not q.args.get("distinct"):
            notes.append(
                "SQL keeps duplicate rows unless you write SELECT DISTINCT; π removes duplicates."
            )

    order = q.args.get("order")
    if order is not None:
        node = Node("sort", ", ".join(o.sql(dialect="duckdb") for o in order.expressions), (node,))
    if q.args.get("limit") is not None:
        notes.append("LIMIT has no relational algebra form; it is left out.")
    return node


def _from_item(item: exp.Expression, notes: list[str]) -> Node:
    alias = item.alias
    if isinstance(item, exp.Table):
        node = Node("relation", item.name)
        return Node("rename", alias, (node,)) if alias and alias != item.name else node
    if isinstance(item, exp.Subquery):
        node = _query(item.this, notes)
        return Node("rename", alias, (node,)) if alias else node
    raise RAError(f"{item.sql()} cannot appear in FROM here.")


# Output


_PRECEDENCE = {
    "union": 1,
    "difference": 1,
    "intersect": 1,
    "join": 2,
    "left": 2,
    "right": 2,
    "full": 2,
    "cross": 2,
}


def to_ra(node: Node) -> str:
    if node.op == "relation":
        return str(node.arg)
    if node.op in UNARY.values():
        return f"{SYMBOL[node.op]}[{node.arg}]({to_ra(node.children[0])})"
    left, right = node.children
    mine = _PRECEDENCE[node.op]

    def side(child: Node, is_right: bool) -> str:
        theirs = _PRECEDENCE.get(child.op)
        text = to_ra(child)
        if theirs is not None and (theirs < mine or (is_right and theirs == mine)):
            return f"({text})"
        return text

    symbol = SYMBOL[node.op] + (f"[{node.arg}]" if node.arg else "")
    return f"{side(left, False)} {symbol} {side(right, True)}"


def tree(node: Node) -> dict[str, object]:
    label = (
        str(node.arg)
        if node.op == "relation"
        else SYMBOL[node.op] + (f" {node.arg}" if node.arg else "")
    )
    return {"op": node.op, "label": label, "children": [tree(c) for c in node.children]}
