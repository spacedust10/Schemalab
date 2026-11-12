"""Schedule checker: serializability, recoverability and two-phase locking.

A schedule is a list of operations such as r1(A) w2(A) c1 a2: read and write of an item by a
transaction, commit and abort. Serializability is judged on the transactions that did not abort.

Two-phase locking: whether some placement of locks lets 2PL (basic, strict or rigorous) produce
the schedule. The check is the textbook argument, run greedily. A transaction takes a shared
lock to read and an exclusive one to write, upgrading when it must. When T2 needs a lock that T1
holds, T1 may release it only if T1 never uses that item again and can take every lock it still
needs right now (its lock point; after that it may not lock anything new). Strict 2PL also keeps
exclusive locks until commit; rigorous 2PL keeps every lock until commit.
"""

import re
from dataclasses import dataclass
from itertools import permutations
from typing import Any, Literal

MAX_TRANSACTIONS = 10
MAX_OPERATIONS = 60
MAX_VIEW_CHECK = 8  # view serializability is checked by trying every serial order

_OP = re.compile(r"([rRwW])(\d+)\s*[\(\[]\s*([A-Za-z]\w*)\s*[\)\]]|([cCaA])(\d+)")

Kind = Literal["r", "w", "c", "a"]
Mode = Literal["S", "X"]


class ScheduleError(ValueError):
    """The schedule cannot be read. The message says where."""


@dataclass(frozen=True)
class Op:
    txn: int
    kind: Kind
    item: str | None
    index: int  # position in the schedule, from 0

    def __str__(self) -> str:
        return f"{self.kind}{self.txn}({self.item})" if self.item else f"{self.kind}{self.txn}"


def parse_schedule(text: str) -> list[Op]:
    tokens = [t for t in re.split(r"[\s,;]+", text.strip()) if t]
    if not tokens:
        raise ScheduleError("Write a schedule, like r1(A) w2(A) c1 c2.")
    ops: list[Op] = []
    ended: set[int] = set()
    for n, token in enumerate(tokens, start=1):
        m = _OP.fullmatch(token)
        if not m:
            raise ScheduleError(
                f"{token} at position {n} is not an operation; write r1(A), w1(A), c1 or a1."
            )
        if m[1]:
            txn, kind, item = int(m[2]), m[1].lower(), m[3]
        else:
            txn, kind, item = int(m[5]), m[4].lower(), None
        if txn in ended:
            raise ScheduleError(f"T{txn} acts after it ended, at position {n}.")
        if kind in "ca":
            ended.add(txn)
        ops.append(Op(txn, kind, item, n - 1))  # type: ignore[arg-type]
    if len({o.txn for o in ops}) > MAX_TRANSACTIONS:
        raise ScheduleError(f"Use at most {MAX_TRANSACTIONS} transactions.")
    if len(ops) > MAX_OPERATIONS:
        raise ScheduleError(f"Use at most {MAX_OPERATIONS} operations.")
    return ops


def _conflicts(a: Op, b: Op) -> bool:
    return a.txn != b.txn and a.item is not None and a.item == b.item and "w" in (a.kind, b.kind)


# Serializability


def _graph(ops: list[Op]) -> dict[tuple[int, int], list[str]]:
    edges: dict[tuple[int, int], list[str]] = {}
    for i, a in enumerate(ops):
        for b in ops[i + 1 :]:
            if a.kind in "rw" and b.kind in "rw" and _conflicts(a, b):
                items = edges.setdefault((a.txn, b.txn), [])
                if a.item not in items:
                    items.append(str(a.item))
    return edges


def _cycle(txns: list[int], edges: dict[tuple[int, int], list[str]]) -> list[int] | None:
    out = {t: sorted(b for (a, b) in edges if a == t) for t in txns}
    state: dict[int, int] = {}  # 1 visiting, 2 done
    path: list[int] = []

    def visit(t: int) -> list[int] | None:
        state[t] = 1
        path.append(t)
        for n in out[t]:
            if state.get(n) == 1:
                return [*path[path.index(n) :], n]
            if n not in state and (found := visit(n)):
                return found
        state[t] = 2
        path.pop()
        return None

    for t in txns:
        if t not in state and (found := visit(t)):
            return found
    return None


def _orders(
    txns: list[int], edges: dict[tuple[int, int], list[str]], limit: int = 5
) -> list[list[int]]:
    """Serial orders consistent with the graph (topological sorts), first `limit` of them."""
    found: list[list[int]] = []

    def extend(order: list[int], left: list[int]) -> None:
        if len(found) >= limit:
            return
        if not left:
            found.append(order)
            return
        for t in left:
            if not any((other, t) in edges for other in left if other != t):
                extend([*order, t], [x for x in left if x != t])

    extend([], sorted(txns))
    return found


def _view(ops: list[Op]) -> tuple[list[tuple[int, str, int | None]], dict[str, int]]:
    """Who each read reads from (None: the initial value), and the final writer of each item."""
    last: dict[str, int] = {}
    reads: list[tuple[int, str, int | None]] = []
    for o in ops:
        if o.kind == "r" and o.item:
            reads.append((o.txn, o.item, last.get(o.item)))
        elif o.kind == "w" and o.item:
            last[o.item] = o.txn
    return reads, last


def _view_order(txns: list[int], ops: list[Op]) -> list[int] | None:
    target_reads, target_final = _view(ops)
    by_txn = {t: [o for o in ops if o.txn == t] for t in txns}
    for order in permutations(sorted(txns)):
        serial = [o for t in order for o in by_txn[t]]
        reads, final = _view(serial)
        if sorted(reads, key=str) == sorted(target_reads, key=str) and final == target_final:
            return list(order)
    return None


# Recoverability


def _recoverability(ops: list[Op]) -> dict[str, dict[str, Any]]:
    commit_at = {o.txn: o.index for o in ops if o.kind == "c"}
    end_at = {o.txn: o.index for o in ops if o.kind in "ca"}
    aborted = {o.txn for o in ops if o.kind == "a"}
    result: dict[str, dict[str, Any]] = {
        "recoverable": {"ok": True, "reason": None},
        "cascadeless": {"ok": True, "reason": None},
        "strict": {"ok": True, "reason": None},
    }

    def fail(name: str, reason: str) -> None:
        if result[name]["ok"]:
            result[name] = {"ok": False, "reason": reason}

    last_writer: dict[str, Op] = {}
    for o in ops:
        if o.kind not in "rw" or not o.item:
            continue
        w = last_writer.get(o.item)
        open_writer = w is not None and w.txn != o.txn and end_at.get(w.txn, len(ops)) > o.index
        if open_writer and w is not None:
            fail(
                "strict", f"{o} touches {o.item} after T{w.txn} wrote it and before T{w.txn} ended."
            )
            if o.kind == "r" and w.txn not in aborted:
                fail(
                    "cascadeless",
                    f"T{o.txn} read {o.item} from T{w.txn} before T{w.txn} committed.",
                )
                reader_commit = commit_at.get(o.txn)
                writer_commit = commit_at.get(w.txn)
                if reader_commit is not None and (
                    writer_commit is None or writer_commit > reader_commit
                ):
                    fail(
                        "recoverable",
                        f"T{o.txn} read {o.item} from T{w.txn} and committed before T{w.txn} did.",
                    )
        if o.kind == "w":
            last_writer[o.item] = o
    return result


# Two-phase locking


def _locking(ops: list[Op], variant: str) -> dict[str, Any]:
    held: dict[str, dict[int, Mode]] = {}
    locked_out: set[int] = set()  # transactions past their lock point
    trace: list[str] = []

    def future(t: int, after: int) -> dict[str, Mode]:
        needs: dict[str, Mode] = {}
        for o in ops[after + 1 :]:
            if o.txn == t and o.item:
                needs[o.item] = "X" if o.kind == "w" or needs.get(o.item) == "X" else "S"
        return needs

    def grant(t: int, item: str, mode: Mode) -> None:
        held.setdefault(item, {})[t] = mode
        trace.append(f"{'x' if mode == 'X' else 's'}l{t}({item})")

    def blockers(t: int, item: str, mode: Mode) -> list[int]:
        return [h for h, m in held.get(item, {}).items() if h != t and (mode == "X" or m == "X")]

    def release(h: int, item: str, step: int) -> str | None:
        """Make h give up its lock on item before `step`; the reason if 2PL forbids it."""
        mode = held[item][h]
        if variant == "rigorous 2PL":
            return f"T{h} holds a lock on {item} and rigorous 2PL holds every lock until commit."
        if variant == "strict 2PL" and mode == "X":
            return f"T{h} wrote {item} and strict 2PL holds exclusive locks until commit."
        if item in future(h, step):
            later = next(o.index + 1 for o in ops[step + 1 :] if o.txn == h and o.item == item)
            return f"T{h} still needs {item} (step {later}), so it cannot release it yet."
        if h not in locked_out:
            # Lock point: take every lock h will still need, now.
            for other, want in future(h, step).items():
                if held.get(other, {}).get(h) in (want, "X"):
                    continue
                if blocking := blockers(h, other, want):
                    return (
                        f"T{h} would first have to lock {other}, which T{blocking[0]} holds, "
                        f"before it could release {item}."
                    )
                grant(h, other, want)
            locked_out.add(h)
        del held[item][h]
        trace.append(f"u{h}({item})")
        return None

    for step, o in enumerate(ops):
        if o.kind in "ca":
            for item in sorted(i for i, holders in held.items() if o.txn in holders):
                del held[item][o.txn]
                trace.append(f"u{o.txn}({item})")
            trace.append(str(o))
            continue
        if o.item is None:  # parse_schedule gives every read and write an item
            continue
        mode: Mode = "X" if o.kind == "w" else "S"
        have = held.get(o.item, {}).get(o.txn)
        if have != "X" and have != mode:
            if o.txn in locked_out:
                return {
                    "ok": False,
                    "step": step + 1,
                    "reason": f"T{o.txn} already released a lock, so 2PL forbids it "
                    f"to lock {o.item}.",
                    "schedule": trace,
                }
            for h in blockers(o.txn, o.item, mode):
                reason = release(h, o.item, step)
                if reason:
                    return {
                        "ok": False,
                        "step": step + 1,
                        "reason": f"At {o}, T{o.txn} needs {o.item}, but {reason}",
                        "schedule": trace,
                    }
            grant(o.txn, o.item, mode)
        trace.append(str(o))
    return {"ok": True, "step": None, "reason": None, "schedule": trace}


# Everything together


def analyze(text: str) -> dict[str, Any]:
    ops = parse_schedule(text)
    aborted = {o.txn for o in ops if o.kind == "a"}
    kept = [o for o in ops if o.txn not in aborted]
    txns = sorted({o.txn for o in kept})
    edges = _graph(kept)
    cycle = _cycle(txns, edges)
    orders = [] if cycle else _orders(txns, edges)
    if cycle is None:
        view_ok: bool | None = True
        view_order: list[int] | None = orders[0] if orders else txns
    elif len(txns) <= MAX_VIEW_CHECK:
        view_order = _view_order(txns, kept)
        view_ok = view_order is not None
    else:
        view_ok, view_order = None, None
    return {
        "operations": [str(o) for o in ops],
        "transactions": sorted({o.txn for o in ops}),
        "aborted": sorted(aborted),
        "edges": [{"from": a, "to": b, "items": items} for (a, b), items in sorted(edges.items())],
        "cycle": cycle,
        "conflict_serializable": cycle is None,
        "serial_orders": orders,
        "view_serializable": view_ok,
        "view_order": view_order,
        **_recoverability(ops),
        "locking": {v: _locking(ops, v) for v in ("2PL", "strict 2PL", "rigorous 2PL")},
    }
