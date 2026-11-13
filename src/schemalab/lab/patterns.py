"""Transaction patterns, run for real: the naive way and the right way, side by side.

Each experiment creates its own demo_ tables in the user's lab schema, runs a few worker
connections at once (as the user's lab role, with its timeouts), and reports checks that an
outsider would care about: is the money all there, was anyone charged twice, did an edit
vanish, did an event go missing, are order numbers unique. Only the fixed SQL below runs; the
user picks an experiment and a mode, nothing else.
"""

import threading
import time
from collections.abc import Callable
from typing import Any

import psycopg

Connect = Callable[[], psycopg.Connection[Any]]
Result = dict[str, Any]

WORKERS = 3
TRANSFERS = 20  # per worker
AMOUNT = 10
RACE_PAUSE = 0.005  # seconds between reading and writing, so races show up reliably


def _check(label: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {"label": label, "expected": expected, "actual": actual, "ok": expected == actual}


def _together(work: Callable[[int], None], workers: int = WORKERS) -> None:
    """Run work(0..workers-1) at the same time, each in its own thread."""
    start = threading.Barrier(workers)

    def go(w: int) -> None:
        start.wait()
        work(w)

    threads = [threading.Thread(target=go, args=(w,)) for w in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# Ledger transfer


def transfer(mode: str, connect: Connect) -> Result:
    """Move AMOUNT around three accounts, many transfers at once. Transfers that share an
    account but not a pair (1 -> 2 while 2 -> 3) are where a lost update destroys money."""
    with connect() as c:
        c.execute("DROP TABLE IF EXISTS demo_accounts")
        c.execute("CREATE TABLE demo_accounts (id int PRIMARY KEY, balance int NOT NULL)")
        c.execute("INSERT INTO demo_accounts VALUES (1, 1000), (2, 1000), (3, 1000)")
    counts = {"transfers": 0, "deadlocks": 0, "declined": 0}
    lock = threading.Lock()

    def worker(w: int) -> None:
        with connect() as c:
            for i in range(TRANSFERS):
                src, dst = (w + i) % 3 + 1, (w + i + 1) % 3 + 1
                for _ in range(5):  # a deadlock victim tries again
                    try:
                        with c.transaction():
                            _move(c, mode, src, dst)
                        with lock:
                            counts["transfers"] += 1
                        break
                    except psycopg.errors.DeadlockDetected:
                        with lock:
                            counts["deadlocks"] += 1
                    except _Declined:
                        with lock:
                            counts["declined"] += 1
                        break

    started = time.perf_counter()
    _together(worker)
    with connect() as c:
        total, below = c.execute(
            "SELECT sum(balance), count(*) FILTER (WHERE balance < 0) FROM demo_accounts"
        ).fetchone() or (0, 0)
    return {
        "checks": [
            _check("Money in the bank", 3000, int(total)),
            _check("Accounts below zero", 0, below),
        ],
        "stats": counts,
        "ms": round((time.perf_counter() - started) * 1000),
    }


_READ = "SELECT id, balance FROM demo_accounts WHERE id = ANY(%s) ORDER BY id"
_READ_LOCKED = _READ + " FOR UPDATE"


class _Declined(Exception):
    """Not enough money: the transfer's transaction rolls back."""


def _move(c: psycopg.Connection[Any], mode: str, src: int, dst: int) -> None:
    if mode == "atomic":
        # The database does the arithmetic under its own row lock, so there is nothing to
        # race; rows are changed in id order so two transfers cannot deadlock, and a debit
        # that would overdraw rolls the whole transfer back.
        for account in sorted((src, dst)):
            if account == src:
                debited = c.execute(
                    "UPDATE demo_accounts SET balance = balance - %s "
                    "WHERE id = %s AND balance >= %s",
                    [AMOUNT, src, AMOUNT],
                ).rowcount
                if not debited:
                    raise _Declined
            else:
                c.execute(
                    "UPDATE demo_accounts SET balance = balance + %s WHERE id = %s", [AMOUNT, dst]
                )
        return
    # FOR UPDATE locks both rows, always in id order, so two transfers cannot deadlock.
    read = _READ_LOCKED if mode == "for_update" else _READ
    balances = dict(c.execute(read, [[src, dst]]).fetchall())
    time.sleep(RACE_PAUSE)  # the application computes the new balances here
    if balances[src] < AMOUNT:
        return
    c.execute("UPDATE demo_accounts SET balance = %s WHERE id = %s", [balances[src] - AMOUNT, src])
    c.execute("UPDATE demo_accounts SET balance = %s WHERE id = %s", [balances[dst] + AMOUNT, dst])


# Idempotency keys

PAYMENTS = 10


def idempotency(mode: str, connect: Connect) -> Result:
    """Each payment request arrives three times at once, as when a client retries after a
    timeout while the first attempt is still being processed."""
    with connect() as c:
        c.execute("DROP TABLE IF EXISTS demo_charges, demo_idempotency")
        c.execute(
            "CREATE TABLE demo_charges (id serial PRIMARY KEY, request text NOT NULL, "
            "amount int NOT NULL)"
        )
        c.execute("CREATE TABLE demo_idempotency (key text PRIMARY KEY, charge_id int)")
    counts = {"requests": 0, "replayed": 0}
    lock = threading.Lock()
    rounds = threading.Barrier(WORKERS)  # all copies of a request start together

    def worker(w: int) -> None:
        with connect() as c:
            for p in range(PAYMENTS):
                rounds.wait()
                replayed = _pay(c, mode, f"payment-{p}")
                with lock:
                    counts["requests"] += 1
                    counts["replayed"] += replayed

    started = time.perf_counter()
    _together(worker)
    with connect() as c:
        charges, twice = c.execute(
            "SELECT count(*), (SELECT count(*) FROM (SELECT request FROM demo_charges "
            "GROUP BY request HAVING count(*) > 1) d) FROM demo_charges"
        ).fetchone() or (0, 0)
    return {
        "checks": [
            _check("Charges made", PAYMENTS, charges),
            _check("Customers charged twice", 0, twice),
        ],
        "stats": counts,
        "ms": round((time.perf_counter() - started) * 1000),
    }


def _pay(c: psycopg.Connection[Any], mode: str, key: str) -> int:
    """Handle one request; 1 if it was answered from an earlier attempt instead of charging."""
    with c.transaction():
        if mode == "key":
            # The unique key is claimed in the same transaction as the charge. A copy that
            # arrives meanwhile waits on the key's index entry, then finds it taken.
            claimed = c.execute(
                "INSERT INTO demo_idempotency (key) VALUES (%s) ON CONFLICT (key) DO NOTHING",
                [key],
            ).rowcount
            if not claimed:
                return 1  # answer with the stored result of the first attempt
            row = c.execute(
                "INSERT INTO demo_charges (request, amount) VALUES (%s, 25) RETURNING id", [key]
            ).fetchone()
            c.execute(
                "UPDATE demo_idempotency SET charge_id = %s WHERE key = %s",
                [row[0] if row else None, key],
            )
            return 0
        if mode == "check_first":
            seen = c.execute("SELECT 1 FROM demo_charges WHERE request = %s", [key]).fetchone()
            time.sleep(RACE_PAUSE)  # the payment provider is called here
            if seen:
                return 1
        c.execute("INSERT INTO demo_charges (request, amount) VALUES (%s, 25)", [key])
        return 0


# Optimistic locking

EDITS = 5  # per editor


def optimistic(mode: str, connect: Connect) -> Result:
    """Three editors each add 1 to a product's stock five times. They read, think without
    holding a lock, then write: the way a web form edits a row."""
    with connect() as c:
        c.execute("DROP TABLE IF EXISTS demo_products")
        c.execute(
            "CREATE TABLE demo_products (id int PRIMARY KEY, stock int NOT NULL, "
            "version int NOT NULL DEFAULT 1)"
        )
        c.execute("INSERT INTO demo_products (id, stock) VALUES (1, 100)")
    counts = {"edits": 0, "conflicts": 0}
    lock = threading.Lock()

    def worker(w: int) -> None:
        with connect() as c:
            for _ in range(EDITS):
                while True:
                    row = c.execute(
                        "SELECT stock, version FROM demo_products WHERE id = 1"
                    ).fetchone()
                    stock, version = row if row else (0, 0)
                    time.sleep(RACE_PAUSE)  # the editor looks at the form
                    if mode == "version":
                        saved = c.execute(
                            "UPDATE demo_products SET stock = %s, version = version + 1 "
                            "WHERE id = 1 AND version = %s",
                            [stock + 1, version],
                        ).rowcount
                    else:
                        saved = c.execute(
                            "UPDATE demo_products SET stock = %s WHERE id = 1", [stock + 1]
                        ).rowcount
                    with lock:
                        if saved:
                            counts["edits"] += 1
                        else:
                            counts["conflicts"] += 1  # someone saved first: read again
                    if saved:
                        break

    started = time.perf_counter()
    _together(worker)
    with connect() as c:
        row = c.execute("SELECT stock FROM demo_products WHERE id = 1").fetchone()
    final = row[0] if row else 0
    expected = 100 + WORKERS * EDITS
    return {
        "checks": [
            _check("Stock after every edit", expected, final),
            _check("Edits lost", 0, expected - final),
        ],
        "stats": counts,
        "ms": round((time.perf_counter() - started) * 1000),
    }


EXPERIMENTS: dict[str, tuple[Callable[[str, Connect], Result], tuple[str, ...]]] = {
    "transfer": (transfer, ("naive", "for_update", "atomic")),
    "idempotency": (idempotency, ("no_key", "check_first", "key")),
    "optimistic": (optimistic, ("last_write_wins", "version")),
}


def run(name: str, mode: str, connect: Connect) -> Result:
    if name not in EXPERIMENTS or mode not in EXPERIMENTS[name][1]:
        raise ValueError(f"Unknown experiment {name} or mode {mode}.")
    return {"name": name, "mode": mode, **EXPERIMENTS[name][0](mode, connect)}
