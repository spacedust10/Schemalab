"""Transaction patterns, run for real: the naive way and the right way, side by side.

Each experiment creates its own demo_ tables in the user's lab schema, runs a few worker
connections at once (as the user's lab role, with its timeouts), and reports checks that an
outsider would care about: is the money all there, was anyone charged twice, did an edit
vanish, did an event go missing, are order numbers unique. Only the fixed SQL below runs; the
user picks an experiment and a mode, nothing else.
"""

import random
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
MAX_TRIES = 50  # an optimistic edit retries at most this often


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
                for _try in range(MAX_TRIES):
                    row = c.execute(
                        "SELECT stock, version FROM demo_products WHERE id = 1"
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("The product row is gone.")
                    stock, version = row
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
                else:
                    raise RuntimeError(f"An edit lost the race {MAX_TRIES} times in a row.")

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


# Transactional outbox

ORDERS = 20
CRASH_EVERY = 4  # the application dies right after committing every fourth order


def outbox(mode: str, connect: Connect) -> Result:
    """Place orders and tell the rest of the system about each one (a broker, here a table
    another service reads)."""
    with connect() as c:
        c.execute("DROP TABLE IF EXISTS demo_orders, demo_outbox, demo_broker")
        c.execute("CREATE TABLE demo_orders (id int PRIMARY KEY, total int NOT NULL)")
        c.execute(
            "CREATE TABLE demo_outbox (id serial PRIMARY KEY, order_id int NOT NULL, "
            "published boolean NOT NULL DEFAULT false)"
        )
        c.execute("CREATE TABLE demo_broker (order_id int NOT NULL)")
    with connect() as app, connect() as broker:
        for n in range(1, ORDERS + 1):
            with app.transaction():
                app.execute("INSERT INTO demo_orders VALUES (%s, %s)", [n, 10 * n])
                if mode != "dual_write":
                    app.execute("INSERT INTO demo_outbox (order_id) VALUES (%s)", [n])
            if n % CRASH_EVERY == 0:
                continue  # crashed after the commit, before anything else
            if mode == "dual_write":
                broker.execute("INSERT INTO demo_broker VALUES (%s)", [n])

    counts = {"orders": ORDERS, "crashes": ORDERS // CRASH_EVERY, "relay_batches": 0}
    lock = threading.Lock()

    def relay(w: int) -> None:
        with connect() as c:
            for _batch in range(ORDERS):  # at most one batch per order, even if all go twice
                with c.transaction():
                    rows = c.execute(_PENDING_LOCKED if mode == "outbox" else _PENDING).fetchall()
                    if not rows:
                        return
                    time.sleep(RACE_PAUSE)  # sending to the broker takes a moment
                    for event_id, order_id in rows:
                        c.execute("INSERT INTO demo_broker VALUES (%s)", [order_id])
                        c.execute(
                            "UPDATE demo_outbox SET published = true WHERE id = %s", [event_id]
                        )
                with lock:
                    counts["relay_batches"] += 1

    started = time.perf_counter()
    if mode != "dual_write":
        _together(relay)
    with connect() as c:
        missing, twice = c.execute(
            "SELECT (SELECT count(*) FROM demo_orders o WHERE NOT EXISTS "
            "(SELECT 1 FROM demo_broker b WHERE b.order_id = o.id)), "
            "(SELECT count(*) FROM (SELECT order_id FROM demo_broker GROUP BY order_id "
            "HAVING count(*) > 1) d)"
        ).fetchone() or (0, 0)
    return {
        "checks": [
            _check("Orders without an event", 0, missing),
            _check("Events published twice", 0, twice),
        ],
        "stats": counts,
        "ms": round((time.perf_counter() - started) * 1000),
    }


_PENDING = "SELECT id, order_id FROM demo_outbox WHERE NOT published ORDER BY id LIMIT 5"
# SKIP LOCKED: each relay takes rows no other relay holds, so no event goes out twice.
_PENDING_LOCKED = _PENDING + " FOR UPDATE SKIP LOCKED"


# TPC-C New-Order

ORDERS_PER_WORKER = 20
DISTRICTS = 2
ITEMS = 10
FIRST_ORDER_ID = 3001  # TPC-C loads 3,000 orders per district first


def new_order(mode: str, connect: Connect) -> Result:
    """The heart of TPC-C's New-Order transaction, simplified: take the district's next order
    number, insert the order, and for each of three items take stock and add an order line.
    Rows are always touched in the same order (district, then items by id) to avoid
    deadlocks."""
    with connect() as c:
        c.execute("DROP TABLE IF EXISTS demo_district, demo_stock, demo_order, demo_order_line")
        c.execute(
            "CREATE TABLE demo_district (d_w_id int, d_id int, d_next_o_id int NOT NULL, "
            "PRIMARY KEY (d_w_id, d_id))"
        )
        c.execute("CREATE TABLE demo_stock (s_i_id int PRIMARY KEY, s_quantity int NOT NULL)")
        c.execute(
            "CREATE TABLE demo_order (o_w_id int, o_d_id int, o_id int, o_ol_cnt int NOT NULL, "
            "PRIMARY KEY (o_w_id, o_d_id, o_id))"
        )
        c.execute(
            "CREATE TABLE demo_order_line (ol_w_id int, ol_d_id int, ol_o_id int, "
            "ol_number int, ol_i_id int NOT NULL, ol_quantity int NOT NULL, "
            "PRIMARY KEY (ol_w_id, ol_d_id, ol_o_id, ol_number))"
        )
        c.execute(
            "INSERT INTO demo_district SELECT 1, d, %s FROM generate_series(1, %s) d",
            [FIRST_ORDER_ID, DISTRICTS],
        )
        c.execute("INSERT INTO demo_stock SELECT i, 1000 FROM generate_series(1, %s) i", [ITEMS])
    counts = {"orders": 0, "failed": 0, "deadlocks": 0}
    lock = threading.Lock()

    def worker(w: int) -> None:
        chooser = random.Random(w)  # noqa: S311  # the same demo orders every run, not secrets
        with connect() as c:
            for _ in range(ORDERS_PER_WORKER):
                district = chooser.randint(1, DISTRICTS)
                lines = sorted(
                    (item, chooser.randint(1, 5)) for item in chooser.sample(range(1, ITEMS + 1), 3)
                )
                for _attempt in range(5):
                    try:
                        with c.transaction():
                            _new_order(c, mode, district, lines)
                        outcome = "orders"
                    except psycopg.errors.DeadlockDetected:
                        outcome = "deadlocks"
                    except psycopg.errors.UniqueViolation:
                        outcome = "failed"  # the order number was already taken
                    with lock:
                        counts[outcome] += 1
                    if outcome != "deadlocks":
                        break

    started = time.perf_counter()
    _together(worker)
    with connect() as c:
        placed, ordered, left, counters = c.execute(
            "SELECT (SELECT count(*) FROM demo_order), "
            "(SELECT coalesce(sum(ol_quantity), 0) FROM demo_order_line), "
            "(SELECT sum(s_quantity) FROM demo_stock), "
            "(SELECT sum(d_next_o_id - %s) FROM demo_district)",
            [FIRST_ORDER_ID],
        ).fetchone() or (0, 0, 0, 0)
    taken = ITEMS * 1000 - int(left)
    return {
        "checks": [
            _check("Orders placed", WORKERS * ORDERS_PER_WORKER, placed),
            _check("Orders that failed", 0, counts["failed"]),
            _check("Order numbers handed out", placed, int(counters)),
            _check("Stock count off by", 0, taken - int(ordered)),
        ],
        "stats": counts,
        "ms": round((time.perf_counter() - started) * 1000),
    }


def _new_order(
    c: psycopg.Connection[Any], mode: str, district: int, lines: list[tuple[int, int]]
) -> None:
    if mode == "atomic":
        row = c.execute(
            "UPDATE demo_district SET d_next_o_id = d_next_o_id + 1 "
            "WHERE d_w_id = 1 AND d_id = %s RETURNING d_next_o_id - 1",
            [district],
        ).fetchone()
        order_id = row[0] if row else 0
    else:
        read = _NEXT_ORDER_LOCKED if mode == "for_update" else _NEXT_ORDER
        row = c.execute(read, [district]).fetchone()
        order_id = row[0] if row else 0
        time.sleep(RACE_PAUSE)
        c.execute(
            "UPDATE demo_district SET d_next_o_id = %s WHERE d_w_id = 1 AND d_id = %s",
            [order_id + 1, district],
        )
    c.execute("INSERT INTO demo_order VALUES (1, %s, %s, %s)", [district, order_id, len(lines)])
    for number, (item, quantity) in enumerate(lines, start=1):
        if mode == "atomic":
            c.execute(
                "UPDATE demo_stock SET s_quantity = s_quantity - %s WHERE s_i_id = %s",
                [quantity, item],
            )
        else:
            read = _STOCK_LOCKED if mode == "for_update" else _STOCK
            row = c.execute(read, [item]).fetchone()
            c.execute(
                "UPDATE demo_stock SET s_quantity = %s WHERE s_i_id = %s",
                [(row[0] if row else 0) - quantity, item],
            )
        c.execute(
            "INSERT INTO demo_order_line VALUES (1, %s, %s, %s, %s, %s)",
            [district, order_id, number, item, quantity],
        )


_NEXT_ORDER = "SELECT d_next_o_id FROM demo_district WHERE d_w_id = 1 AND d_id = %s"
_NEXT_ORDER_LOCKED = _NEXT_ORDER + " FOR UPDATE"
_STOCK = "SELECT s_quantity FROM demo_stock WHERE s_i_id = %s"
_STOCK_LOCKED = _STOCK + " FOR UPDATE"


EXPERIMENTS: dict[str, tuple[Callable[[str, Connect], Result], tuple[str, ...]]] = {
    "transfer": (transfer, ("naive", "for_update", "atomic")),
    "idempotency": (idempotency, ("no_key", "check_first", "key")),
    "optimistic": (optimistic, ("last_write_wins", "version")),
    "outbox": (outbox, ("dual_write", "outbox_no_lock", "outbox")),
    "new_order": (new_order, ("naive", "for_update", "atomic")),
}


def _mode(id: str, label: str, how: str) -> dict[str, str]:
    return {"id": id, "label": label, "how": how}


CATALOG: list[dict[str, Any]] = [
    {
        "id": "transfer",
        "title": "Ledger transfer",
        "question": "Three workers make 60 transfers around three accounts at once. "
        "Is every cent still there?",
        "modes": [
            _mode(
                "naive",
                "Read, then write",
                "Each transfer reads both balances, computes "
                "the new ones in the application, and writes them back.",
            ),
            _mode(
                "for_update",
                "SELECT ... FOR UPDATE",
                "Reads both rows with FOR UPDATE, "
                "in id order, so other transfers wait their turn and none deadlock.",
            ),
            _mode(
                "atomic",
                "UPDATE in place",
                "UPDATE ... SET balance = balance - 10 WHERE "
                "balance >= 10, rows in id order: the database does the arithmetic.",
            ),
        ],
    },
    {
        "id": "idempotency",
        "title": "Idempotency keys",
        "question": "Each of 10 payment requests arrives three times at once, as when a "
        "client retries a slow request. Is each customer charged once?",
        "modes": [
            _mode("no_key", "No key", "Every request charges."),
            _mode(
                "check_first",
                "Check, then charge",
                "Looks for an earlier charge for the request first, and charges if there is none.",
            ),
            _mode(
                "key",
                "Idempotency key",
                "Claims the request's key with INSERT ... ON "
                "CONFLICT DO NOTHING in the charge's transaction; copies get the first answer.",
            ),
        ],
    },
    {
        "id": "optimistic",
        "title": "Optimistic locking",
        "question": "Three editors each add 1 to a product's stock five times, through a "
        "form that holds no lock. Do all 15 edits arrive?",
        "modes": [
            _mode(
                "last_write_wins",
                "Last write wins",
                "Reads the row, then saves it without checking whether anyone saved in between.",
            ),
            _mode(
                "version",
                "Version column",
                "Saves with WHERE version = <the version it "
                "read>; if no row changed, someone saved first, so it reads again and retries.",
            ),
        ],
    },
    {
        "id": "outbox",
        "title": "Transactional outbox",
        "question": "The application crashes right after every fourth of 20 orders. Does "
        "every order still reach the other services, exactly once?",
        "modes": [
            _mode(
                "dual_write",
                "Commit, then publish",
                "Commits the order, then sends the event in a separate step.",
            ),
            _mode(
                "outbox_no_lock",
                "Outbox, plain SELECT",
                "Writes the event to an outbox "
                "table in the order's transaction; three relays read pending rows with a "
                "plain SELECT.",
            ),
            _mode(
                "outbox",
                "Outbox, SKIP LOCKED",
                "The same outbox; relays take pending rows "
                "with FOR UPDATE SKIP LOCKED, so no two relays hold the same row.",
            ),
        ],
    },
    {
        "id": "new_order",
        "title": "TPC-C New-Order",
        "question": "Three terminals place 60 orders in two districts. Are order numbers "
        "unique and stock exact?",
        "modes": [
            _mode(
                "naive",
                "Read, then write",
                "Reads the district's next order number and "
                "each item's stock, then writes computed values.",
            ),
            _mode(
                "for_update",
                "SELECT ... FOR UPDATE",
                "Locks the district row and the stock rows (items in id order) while reading them.",
            ),
            _mode(
                "atomic",
                "UPDATE in place",
                "Takes the order number with UPDATE ... "
                "RETURNING and stock with SET s_quantity = s_quantity - n.",
            ),
        ],
    },
]


def _summary(checks: list[dict[str, Any]]) -> str:
    failed = [c for c in checks if not c["ok"]]
    if not failed:
        return "Every check passed."
    parts = "; ".join(f"{c['label']}: expected {c['expected']}, got {c['actual']}" for c in failed)
    return f"{len(failed)} of {len(checks)} checks failed. {parts}."


def run(name: str, mode: str, connect: Connect) -> Result:
    if name not in EXPERIMENTS or mode not in EXPERIMENTS[name][1]:
        raise ValueError(f"Unknown experiment {name} or mode {mode}.")
    result = EXPERIMENTS[name][0](mode, connect)
    return {"name": name, "mode": mode, "summary": _summary(result["checks"]), **result}
