"""ARIES recovery, simulated over a small write-ahead log.

    T1 begin                (optional)
    T1 write A 10 20        (or: T1 update A 10 -> 20; old value, then new)
    checkpoint              (fuzzy: records the transaction and dirty page tables)
    flush A                 (the buffer manager writes page A to disk; not logged)
    T1 commit | T1 abort
    crash

Each item is its own page. LSNs count 10, 20, 30 as in textbooks. The log itself is on stable
storage up to the crash (the write-ahead rule), so recovery reads all of it. Recovery runs the
three ARIES passes: analysis from the last checkpoint rebuilds the transaction and dirty page
tables; redo repeats history from the smallest recLSN, skipping changes the disk page already
has; undo rolls back the losers newest first, writing a compensation record (CLR) for each undone
change, whose undoNext points to the next change to undo.
"""

import re
from dataclasses import dataclass, field
from typing import Any

STEP = 10
MAX_LINES = 80

_WRITE = re.compile(r"T(\d+)\s+(?:write|update)\s+(\w+)\s+(\S+)\s+(?:->\s+)?(\S+)", re.IGNORECASE)
_TXN = re.compile(r"T(\d+)\s+(begin|commit|abort)", re.IGNORECASE)
_FLUSH = re.compile(r"flush\s+(\w+)", re.IGNORECASE)


class RecoveryError(ValueError):
    """The log cannot be read. The message names the line."""


@dataclass
class _Record:
    lsn: int
    kind: str  # update, commit, abort, end, CLR, checkpoint
    txn: int | None = None
    page: str | None = None
    before: str | None = None
    after: str | None = None
    prev: int | None = None
    undo_next: int | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)

    def json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "lsn": self.lsn,
            "kind": self.kind,
            "txn": self.txn,
            "page": self.page,
            "before": self.before,
            "after": self.after,
            "prev": self.prev,
            "undo_next": self.undo_next,
        }
        if self.snapshot:
            out["snapshot"] = self.snapshot
        return out


class _Log:
    def __init__(self) -> None:
        self.records: list[_Record] = []

    def append(self, **fields: Any) -> _Record:
        record = _Record(lsn=(len(self.records) + 1) * STEP, **fields)
        self.records.append(record)
        return record

    def at(self, lsn: int) -> _Record:
        return self.records[lsn // STEP - 1]


def simulate(text: str) -> dict[str, Any]:
    lines = [(n, raw.split("#", 1)[0].strip()) for n, raw in enumerate(text.splitlines(), start=1)]
    lines = [(n, line) for n, line in lines if line]
    if not lines:
        raise RecoveryError("Write a log, one action per line, like T1 write A 10 20.")
    if len(lines) > MAX_LINES:
        raise RecoveryError(f"Use at most {MAX_LINES} lines.")

    log = _Log()
    buffer: dict[str, tuple[str, int]] = {}  # page -> (value, pageLSN) in memory
    disk: dict[str, tuple[str, int]] = {}
    dirty: dict[str, int] = {}  # page -> recLSN, as the buffer manager knows it
    last: dict[int, int | None] = {}  # running transaction -> lastLSN
    ended: set[int] = set()
    crashed = False

    def rollback(txn: int, record_kind: str) -> None:
        """Undo txn during normal operation: CLRs, then an end record."""
        log.append(kind=record_kind, txn=txn, prev=last.get(txn))
        lsn = last.get(txn)
        while lsn is not None:
            r = log.at(lsn)
            if r.kind == "update" and r.page is not None and r.before is not None:
                clr = log.append(
                    kind="CLR", txn=txn, page=r.page, after=r.before, prev=lsn, undo_next=r.prev
                )
                buffer[r.page] = (r.before, clr.lsn)
                dirty.setdefault(r.page, clr.lsn)
            lsn = r.prev
        log.append(kind="end", txn=txn)

    for n, line in lines:
        if crashed:
            raise RecoveryError(f"line {n}: nothing can happen after the crash.")
        if line.lower() == "crash":
            crashed = True
            continue
        if line.lower() == "checkpoint":
            log.append(
                kind="checkpoint",
                snapshot={
                    "transactions": {str(t): lsn for t, lsn in sorted(last.items())},
                    "dirty_pages": dict(sorted(dirty.items())),
                },
            )
            continue
        if m := _FLUSH.fullmatch(line):
            page = m[1]
            if page not in buffer:
                raise RecoveryError(f"line {n}: {page} was never written.")
            disk[page] = buffer[page]
            dirty.pop(page, None)
            continue
        write = _WRITE.fullmatch(line)
        action = _TXN.fullmatch(line)
        if not write and not action:
            if re.match(r"T\d+\s+(write|update)\b", line, re.IGNORECASE):
                raise RecoveryError(f"line {n}: write a change as T1 write A <old> <new>.")
            raise RecoveryError(f"line {n}: {line} is not a log action.")
        txn = int((write or action)[1])  # type: ignore[index]
        if txn in ended:
            raise RecoveryError(f"line {n}: T{txn} already ended.")
        if write:
            page, before, after = write[2], write[3], write[4]
            disk.setdefault(page, (before, 0))  # the value on disk before any change
            record = log.append(
                kind="update", txn=txn, page=page, before=before, after=after, prev=last.get(txn)
            )
            last[txn] = record.lsn
            buffer[page] = (after, record.lsn)
            dirty.setdefault(page, record.lsn)
        elif action and action[2].lower() == "begin":
            last.setdefault(txn, None)
        elif action and action[2].lower() == "commit":
            log.append(kind="commit", txn=txn, prev=last.get(txn))
            log.append(kind="end", txn=txn)
            last.pop(txn, None)
            ended.add(txn)
        else:
            rollback(txn, "abort")
            last.pop(txn, None)
            ended.add(txn)

    history = [r.json() for r in log.records]
    if not crashed:
        return {
            "crashed": False,
            "log": history,
            "at_crash": None,
            "analysis": None,
            "redo": None,
            "undo": [],
            "final": {p: v for p, (v, _) in sorted(buffer.items())},
        }
    at_crash = {p: {"value": v, "page_lsn": lsn} for p, (v, lsn) in sorted(disk.items())}
    analysis, att, dpt = _analysis(log)
    pages = dict(disk)  # the buffer is gone; recovery works on what the disk has
    redo = _redo(log, dpt, pages)
    undo = _undo(log, att, pages)
    return {
        "crashed": True,
        "log": history,
        "at_crash": at_crash,
        "analysis": analysis,
        "redo": redo,
        "undo": undo,
        "final": {p: v for p, (v, _) in sorted(pages.items())},
    }


def _analysis(log: _Log) -> tuple[dict[str, Any], dict[int, tuple[str, int]], dict[str, int]]:
    """Rebuild the transaction table (status, lastLSN) and dirty page table (recLSN)."""
    checkpoints = [r for r in log.records if r.kind == "checkpoint"]
    att: dict[int, tuple[str, int]] = {}
    dpt: dict[str, int] = {}
    start = log.records[0].lsn if log.records else 0
    if checkpoints:
        cp = checkpoints[-1]
        start = cp.lsn
        att = {
            int(t): ("running", lsn)
            for t, lsn in cp.snapshot["transactions"].items()
            if lsn is not None
        }
        dpt = dict(cp.snapshot["dirty_pages"])
    for r in log.records:
        if r.lsn <= start and checkpoints:
            continue
        if r.txn is None:
            continue
        if r.kind == "end":
            att.pop(r.txn, None)
            continue
        status = {"commit": "committed", "abort": "aborting"}.get(
            r.kind, att.get(r.txn, ("running", 0))[0]
        )
        att[r.txn] = (status, r.lsn)
        if r.kind in ("update", "CLR") and r.page is not None:
            dpt.setdefault(r.page, r.lsn)
    losers = sorted(t for t, (status, _) in att.items() if status != "committed")
    return (
        {
            "start_lsn": start,
            "transactions": [
                {"txn": t, "status": s, "last_lsn": lsn} for t, (s, lsn) in sorted(att.items())
            ],
            "dirty_pages": [{"page": p, "rec_lsn": lsn} for p, lsn in sorted(dpt.items())],
            "losers": losers,
            "winners": sorted(t for t, (s, _) in att.items() if s == "committed"),
        },
        att,
        dpt,
    )


def _redo(log: _Log, dpt: dict[str, int], pages: dict[str, tuple[str, int]]) -> dict[str, Any]:
    """Repeat history: reapply every logged change the disk might be missing."""
    if not dpt:
        return {"start_lsn": None, "steps": []}
    start = min(dpt.values())
    steps = []
    for r in log.records:
        if r.lsn < start or r.kind not in ("update", "CLR") or r.page is None or r.after is None:
            continue
        on_disk = pages.get(r.page, ("", 0))[1]
        if r.page not in dpt:
            action, reason = "skip", "the page is not in the dirty page table"
        elif dpt[r.page] > r.lsn:
            action, reason = "skip", f"the page's recLSN {dpt[r.page]} is later than {r.lsn}"
        elif on_disk >= r.lsn:
            action, reason = "skip", f"the page on disk already has LSN {on_disk} >= {r.lsn}"
        else:
            action, reason = "redo", f"the page on disk has LSN {on_disk} < {r.lsn}"
            pages[r.page] = (r.after, r.lsn)
        steps.append(
            {
                "lsn": r.lsn,
                "kind": r.kind,
                "page": r.page,
                "value": r.after,
                "action": action,
                "reason": reason,
            }
        )
    return {"start_lsn": start, "steps": steps}


def _undo(
    log: _Log, att: dict[int, tuple[str, int]], pages: dict[str, tuple[str, int]]
) -> list[dict[str, Any]]:
    """Roll back the losers, always undoing the newest change left, writing CLRs."""
    to_undo = {lsn for status, lsn in att.values() if status != "committed"}
    steps: list[dict[str, Any]] = []
    for t, (status, _) in sorted(att.items()):
        if status == "committed":
            end = log.append(kind="end", txn=t)
            steps.append(
                {"lsn": end.lsn, "kind": "end", "txn": t, "note": "committed before the crash"}
            )
    while to_undo:
        lsn = max(to_undo)
        to_undo.discard(lsn)
        r = log.at(lsn)
        txn = r.txn
        if r.kind == "CLR":
            nxt = r.undo_next
        elif r.kind == "update" and r.page is not None and r.before is not None:
            clr = log.append(
                kind="CLR", txn=txn, page=r.page, after=r.before, prev=lsn, undo_next=r.prev
            )
            pages[r.page] = (r.before, clr.lsn)
            steps.append(
                {
                    "lsn": clr.lsn,
                    "kind": "CLR",
                    "txn": txn,
                    "page": r.page,
                    "value": r.before,
                    "undoes": lsn,
                    "undo_next": r.prev,
                }
            )
            nxt = r.prev
        else:
            nxt = r.prev
        if nxt is None:
            end = log.append(kind="end", txn=txn)
            steps.append({"lsn": end.lsn, "kind": "end", "txn": txn})
        else:
            to_undo.add(nxt)
    return steps
