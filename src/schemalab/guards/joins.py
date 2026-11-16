"""The join guard: a third check on the analyst's SQL, after the parser guard.

The parser guard decides which tables a query may read; this decides how it may join them.
Every join must be an equality between two columns that the database declares as related
(foreign keys from uploads, joins written into the sample-dataset registry). The eval's worst
answers joined tables that have nothing to do with each other, such as insurance rows joined
to taxi zones on a number that happened to match; this refuses those before they run.

The check follows column references through CTEs and derived tables to the base table, and
through correlated subqueries to the outer query.

ponytail: each column pair of a join is checked on its own, so a join on half of a composite
key passes. Check the whole key set per table pair if uploads with composite foreign keys need it.
"""

from collections.abc import Collection, Mapping

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, find_all_in_scope, traverse_scope

from schemalab.guards.sql import UnsafeSQLError

Column = tuple[str, str]  # (table, column), lowercase
Edge = tuple[Column, Column]  # sorted, so a join reads the same either way round


class JoinError(UnsafeSQLError):
    """A join the database does not declare. The message lists the joins that exist."""


class UnplacedColumnError(JoinError):
    """A column the guard cannot place, in a query over several tables. The caller asks DuckDB
    to bind the query: a made-up column then gets DuckDB's own error, which names the columns
    that exist; a query that binds is refused, since its joins went unchecked."""


def edge(table_a: str, column_a: str, table_b: str, column_b: str) -> Edge:
    a, b = (table_a.lower(), column_a.lower()), (table_b.lower(), column_b.lower())
    return (a, b) if a <= b else (b, a)


def text(e: Edge) -> str:
    (ta, ca), (tb, cb) = e
    return f"{ta}.{ca} = {tb}.{cb}"


def check_joins(
    sql: str, schema: Mapping[str, Mapping[str, str]], graph: Collection[Edge]
) -> list[str]:
    """The joins `sql` uses, as text, if every one is in `graph`; otherwise JoinError.

    `schema` maps each table to its columns and types, so unqualified columns resolve.
    """
    lower: dict[str, object] = {
        t.lower(): {c.lower(): ty for c, ty in cols.items()} for t, cols in schema.items()
    }
    try:
        parsed = sqlglot.parse_one(sql, dialect="duckdb")
        tree = qualify(parsed.copy(), schema=lower, dialect="duckdb", validate_qualify_columns=True)
    except OptimizeError as e:
        # With one table there is no join to check, and DuckDB names the unknown column better.
        # With several, a column the guard cannot place could hide a join: refuse.
        if len(list(parsed.find_all(exp.Table))) <= 1:
            return []
        raise UnplacedColumnError(f"name each column with its table, like t.column ({e})") from None
    except SqlglotError as e:
        raise JoinError(f"could not read the joins: {e}") from None

    used: set[Edge] = set()
    for scope in traverse_scope(tree):
        used |= _scope_edges(scope)
    wrong = sorted(e for e in used if e not in graph)
    if wrong:
        raise JoinError(_refusal(wrong, graph))
    return sorted(text(e) for e in used)


def _scope_edges(scope: Scope) -> set[Edge]:
    select = scope.expression
    if not isinstance(select, exp.Select):
        return set()
    joins = select.args.get("joins") or []
    if any(j.method == "NATURAL" for j in joins):
        raise JoinError("NATURAL JOIN is not allowed: name the key columns with ON.")
    conditions = [j.args["on"] for j in joins if j.args.get("on")]
    if where := select.args.get("where"):
        conditions.append(where.this)

    edges: set[Edge] = set()
    linked: set[str] = set()
    for condition in conditions:
        for member in find_all_in_scope(condition, exp.In):
            if member.args.get("query") is None:
                continue  # IN (1, 2, 3) is a filter: it links nothing
            if found := _semi_join(scope, member):  # refuses anything but key IN (SELECT key)
                edges.add(found)
            linked.add(member.this.table)  # _semi_join proved this is a column
        for eq in find_all_in_scope(condition, exp.EQ):
            left, right = eq.left, eq.right
            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue
            if left.table == right.table:
                continue  # a filter within one source, not a join
            if eq.find_ancestor(exp.Or, exp.Not):
                raise JoinError(f"a join condition under OR or NOT proves nothing: {eq.sql()}")
            a, b = _base(scope, left), _base(scope, right)
            if a is None or b is None:
                raise JoinError(
                    f"{eq.sql()} joins on a computed value, not a key column. Join on the key."
                )
            linked |= {left.table, right.table}
            # A self-join on the same column (a.id = b.id) needs no declared edge.
            if a != b:
                edges.add(edge(*a, *b))

    # A subquery that reads the outer query's columns is a join too: it needs a key equality.
    outer = {c.table for c in scope.external_columns} - linked
    if outer:
        raise JoinError(
            f"a subquery refers to {', '.join(sorted(outer))} without a key equality. "
            "Link it with WHERE inner.key = outer.key."
        )

    if joins:
        sources = [select.args["from_"].this, *(j.this for j in joins)]
        unlinked = [s.alias_or_name for s in sources if s.alias_or_name not in linked]
        if unlinked:
            raise JoinError(
                f"{', '.join(unlinked)} {'is' if len(unlinked) == 1 else 'are'} joined without "
                "a key equality (a cross join, or a join on a computed or compared value). "
                "Join with ON a.key = b.key."
            )
    return edges


def _semi_join(scope: Scope, member: exp.In) -> Edge | None:
    """`col IN (SELECT key FROM t)` joins col's table to t: the same rule as an ON."""
    query = member.args.get("query")
    if query is None:
        return None  # IN (1, 2, 3): a filter
    if member.find_ancestor(exp.Or, exp.Not):
        raise JoinError(f"an IN (SELECT ...) under OR or NOT proves nothing: {member.sql()}")
    inner = next((s for s in scope.subquery_scopes if s.expression is query.unnest()), None)
    left = member.this
    selects = inner.expression.selects if inner and isinstance(inner.expression, exp.Select) else []
    target = selects[0].unalias() if len(selects) == 1 else None
    a = _base(scope, left) if isinstance(left, exp.Column) else None
    b = _base(inner, target) if inner and isinstance(target, exp.Column) else None
    if a is None or b is None:
        raise JoinError(f"{member.sql()} matches a computed value, not a key column.")
    return edge(*a, *b) if a != b else None


def _base(scope: Scope | None, column: exp.Column) -> Column | None:
    """The base table and column behind a qualified column, through CTEs, derived tables and
    outer queries; None for a computed value."""
    while scope is not None and column.table not in scope.sources:
        scope = scope.parent  # a correlated reference to an outer query
    if scope is None:
        return None
    source = scope.sources.get(column.table)
    if isinstance(source, exp.Table):
        return (source.name.lower(), column.name.lower())
    if isinstance(source, Scope) and isinstance(source.expression, exp.SetOperation):
        return None  # a UNION's column could come from either branch: not a key
    if isinstance(source, Scope) and isinstance(source.expression, exp.Query):
        # A CTE or derived table: follow a plain column projection through.
        for projection in source.expression.selects:
            inner = projection.unalias()
            if projection.alias_or_name == column.name and isinstance(inner, exp.Column):
                return _base(source, inner)
    return None


def _refusal(wrong: list[Edge], graph: Collection[Edge]) -> str:
    tables = sorted({t for e in wrong for t, _ in e})
    lines = [f"{text(e)} is not a known join." for e in wrong]
    for table in tables:
        mine = sorted(text(e) for e in graph if table in (e[0][0], e[1][0]))
        if mine:
            lines.append(f"{table} joins only as: " + "; ".join(mine) + ".")
        else:
            lines.append(f"{table} has no joins: answer from it alone.")
    return " ".join(lines)
