"""Reading PostgreSQL's EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) and advising on indexes.

PostgreSQL reports a node's rows and times per loop; a node run in 2 parallel processes or once
per outer row has Actual Loops > 1. Everything here is multiplied out, so rows, removed rows and
times are totals. A node's own time is its total minus its children's.

The advisor is a few explainable rules, each with the evidence from the plan:
- a sequential scan whose filter throws away most of what it reads: index the filtered columns
  (equality columns first, then one range column), and append the ORDER BY columns when a Sort
  under a Limit sits above the scan, so the index can return rows already in order;
- a nested loop comparing pairs through a join filter: index the join column on the larger side,
  so each outer row can look its matches up instead.
"""

import re
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MISESTIMATE = 10.0  # estimated and actual rows this far apart deserve a look
MIN_SCANNED = 10_000  # below this, a sequential scan is cheap enough
MAX_KEPT = 0.05  # a filter keeping more than this share of rows gains little from an index


def summarize(raw: list[dict[str, Any]]) -> dict[str, Any]:
    top = raw[0]
    nodes: list[dict[str, Any]] = []

    def walk(p: dict[str, Any], parent: int | None, depth: int) -> dict[str, Any]:
        loops = int(p.get("Actual Loops", 1)) or 1
        me: dict[str, Any] = {
            "id": len(nodes),
            "parent": parent,
            "depth": depth,
            "type": p["Node Type"],
            "relation": p.get("Relation Name"),
            "alias": p.get("Alias"),
            "index": p.get("Index Name"),
            "loops": loops,
            "rows": round(p.get("Actual Rows", 0) * loops),
            "est_rows": round(p.get("Plan Rows", 0) * loops),
            "total_ms": round(p.get("Actual Total Time", 0.0) * loops, 3),
            "removed": round(p.get("Rows Removed by Filter", 0) * loops),
            "filter": p.get("Filter"),
            "index_cond": p.get("Index Cond"),
            "join_filter": p.get("Join Filter"),
            "hash_cond": p.get("Hash Cond"),
            "sort_key": p.get("Sort Key"),
            "hit_blocks": p.get("Shared Hit Blocks", 0),
            "read_blocks": p.get("Shared Read Blocks", 0),
        }
        per_loop_est, per_loop_rows = p.get("Plan Rows", 0), p.get("Actual Rows", 0)
        low, high = sorted((max(per_loop_est, 1), max(per_loop_rows, 1)))
        me["misestimate"] = round(high / low, 1)
        me["label"] = _label(me)
        nodes.append(me)
        children = [walk(c, me["id"], depth + 1) for c in p.get("Plans", [])]
        me["self_ms"] = round(max(0.0, me["total_ms"] - sum(c["total_ms"] for c in children)), 3)
        me["children"] = [c["id"] for c in children]
        return me

    walk(top["Plan"], None, 0)
    nodes.sort(key=lambda n: n["id"])

    def rows(count: float) -> str:
        whole = round(count)
        return f"{whole} row" if whole == 1 else f"{whole} rows"

    warnings = [
        f"{n['label']}: estimated {rows(n['est_rows'] / n['loops'])}, got "
        f"{round(n['rows'] / n['loops'])} per loop ({n['misestimate']:g}x). The planner's "
        "statistics may be stale; ANALYZE the table."
        for n in nodes
        if n["misestimate"] >= MISESTIMATE
    ]

    def tree(i: int) -> dict[str, Any]:
        n = nodes[i]
        return {"id": i, "label": n["label"], "children": [tree(c) for c in n["children"]]}

    return {
        "planning_ms": top.get("Planning Time"),
        "execution_ms": top.get("Execution Time"),
        "nodes": nodes,
        "tree": tree(0),
        "hotspot": max(nodes, key=lambda n: n["self_ms"])["id"],
        "warnings": warnings,
    }


def _label(n: dict[str, Any]) -> str:
    t = n["type"]
    if n["relation"]:
        using = f" using {n['index']}" if n["index"] else ""
        return f"{t}{using} on {n['relation']}"
    if t == "Sort" and n["sort_key"]:
        return f"Sort by {', '.join(n['sort_key'])}"
    return str(t)


# Index advice


def _predicates(text: str) -> tuple[list[str], list[str]] | None:
    """Equality and range columns compared with constants, or None if the filter has an OR."""
    try:
        cond = sqlglot.parse_one(text, read="postgres")
    except SqlglotError:
        return None
    cond = cond.unnest()  # PostgreSQL wraps the whole filter in parentheses
    if cond.find(exp.Or):
        return None
    eq: list[str] = []
    ranged: list[str] = []
    for part in cond.flatten() if isinstance(cond, exp.And) else [cond]:
        part = part.unnest()
        if isinstance(part, exp.Paren):
            part = part.this
        if not isinstance(
            part, exp.EQ | exp.GT | exp.GTE | exp.LT | exp.LTE | exp.In | exp.Between | exp.Like
        ):
            continue
        columns = list(part.find_all(exp.Column))
        if len(columns) != 1:
            continue  # comparing two columns, or nothing indexable
        name = columns[0].name
        bucket = eq if isinstance(part, exp.EQ | exp.In) else ranged
        if name not in eq and name not in ranged:
            bucket.append(name)
    return eq, ranged


def _index_sql(table: str, columns: list[str]) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", f"{table}_{'_'.join(columns)}_idx".lower())[:63]
    return f"CREATE INDEX {name} ON {table} ({', '.join(columns)});"


def _covered(existing: dict[str, list[list[str]]], table: str, columns: list[str]) -> bool:
    return any(index[: len(columns)] == columns for index in existing.get(table, []))


def advise(summary: dict[str, Any], existing: dict[str, list[list[str]]]) -> list[dict[str, Any]]:
    """`existing` maps each table to the column lists of its current indexes."""
    nodes: list[dict[str, Any]] = summary["nodes"]
    by_id = {n["id"]: n for n in nodes}
    out: list[dict[str, Any]] = []

    def ancestors(n: dict[str, Any]) -> list[dict[str, Any]]:
        chain = []
        while n["parent"] is not None:
            n = by_id[n["parent"]]
            chain.append(n)
        return chain

    def add(table: str, columns: list[str], reason: str, node_id: int) -> None:
        if columns and not _covered(existing, table, columns):
            sql = _index_sql(table, columns)
            if all(a["sql"] != sql for a in out):
                out.append(
                    {
                        "table": table,
                        "columns": columns,
                        "sql": sql,
                        "reason": reason,
                        "node": node_id,
                    }
                )

    for n in nodes:
        if n["type"] == "Seq Scan" and n["filter"] and n["relation"]:
            scanned = n["rows"] + n["removed"]
            if scanned < MIN_SCANNED or n["rows"] > MAX_KEPT * scanned:
                continue
            found = _predicates(n["filter"])
            if not found:
                continue
            eq, ranged = found
            columns = eq + ranged[:1]
            reason = (
                f"The sequential scan on {n['relation']} read {scanned:,} rows to return "
                f"{n['rows']:,}; an index on the filtered columns finds them directly."
            )
            chain = ancestors(n)
            sort = next((a for a in chain if a["type"] == "Sort"), None)
            limited = sort is not None and any(
                a["type"] == "Limit" for a in chain[chain.index(sort) + 1 :]
            )
            if sort and limited and not ranged:
                keys = [
                    re.sub(r"\s+(ASC|DESC)$", "", k, flags=re.IGNORECASE).split(".")[-1]
                    for k in sort["sort_key"]
                ]
                if all(re.fullmatch(r"\w+", k) for k in keys):
                    columns += [k for k in keys if k not in columns]
                    reason += (
                        f" Adding ORDER BY {', '.join(keys)} lets it return rows in order, "
                        "so the Sort under the LIMIT goes away."
                    )
            add(n["relation"], columns, reason, n["id"])

        if n["type"] == "Nested Loop" and n["join_filter"]:
            sides = [by_id[c] for c in n["children"]]
            scans = {
                (s["alias"] or s["relation"]): s
                for side in sides
                for s in [side, *[by_id[c] for c in side["children"]]]
                if s["relation"]
            }
            try:
                cond = sqlglot.parse_one(n["join_filter"], read="postgres").unnest()
            except SqlglotError:
                continue
            if isinstance(cond, exp.Paren):
                cond = cond.this
            if not isinstance(cond, exp.EQ):
                continue
            cols = [c for c in cond.find_all(exp.Column) if c.table in scans]
            if len(cols) != 2:
                continue
            bigger = max(cols, key=lambda c: scans[c.table]["rows"])
            target = scans[bigger.table]
            pairs = sides[0]["rows"] * (sides[1]["rows"] // max(sides[1]["loops"], 1))
            add(
                target["relation"],
                [bigger.name],
                f"The nested loop compared {pairs:,} row pairs through its join filter "
                f"({n['join_filter']}). With an index on {target['relation']}.{bigger.name}, each "
                "row from the other side looks its matches up instead.",
                n["id"],
            )
    return out
