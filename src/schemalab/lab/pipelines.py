"""Data pipeline experiments: change data capture, partitioning, slowly changing dimensions
and the move from an OLTP schema to a star schema. Same shape as the transaction patterns:
fixed SQL in demo_ tables of the user's lab schema, the naive way beside the right one, and
checks anyone can read.
"""

import time
from typing import Any

from schemalab.lab.patterns import Connect, Result, _check, _mode

# Change data capture

_CDC_FUNCTION = """
CREATE FUNCTION demo_capture() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    INSERT INTO demo_changes (op, id, row) VALUES ('delete', OLD.id, NULL);
    RETURN OLD;
  END IF;
  INSERT INTO demo_changes (op, id, row) VALUES (lower(TG_OP), NEW.id, to_jsonb(NEW));
  RETURN NEW;
END $$
"""


_CUSTOMERS = (
    "CREATE TABLE {} (id int PRIMARY KEY, name text NOT NULL, city text NOT NULL, "
    "updated_at timestamptz NOT NULL DEFAULT now())"
)


def cdc(mode: str, connect: Connect) -> Result:
    """Keep a replica of a customers table in step with the source."""
    with connect() as c:
        c.execute("DROP TABLE IF EXISTS demo_customers, demo_replica, demo_changes")
        c.execute("DROP FUNCTION IF EXISTS demo_capture() CASCADE")
        c.execute(_CUSTOMERS.format("demo_customers"))
        c.execute(_CUSTOMERS.format("demo_replica"))
        if mode == "change_log":
            c.execute(
                "CREATE TABLE demo_changes (seq bigserial PRIMARY KEY, op text NOT NULL, "
                "id int NOT NULL, row jsonb, applied boolean NOT NULL DEFAULT false)"
            )
            c.execute(_CDC_FUNCTION)
            c.execute(
                "CREATE TRIGGER demo_capture AFTER INSERT OR UPDATE OR DELETE ON demo_customers "
                "FOR EACH ROW EXECUTE FUNCTION demo_capture()"
            )
        c.execute(
            "INSERT INTO demo_customers (id, name, city) VALUES (1, 'Ada', 'Ann Arbor'), "
            "(2, 'Grace', 'Detroit'), (3, 'Alan', 'Lansing'), (4, 'Edsger', 'Flint'), "
            "(5, 'Barbara', 'Dearborn')"
        )
    counts = {"syncs": 0, "changes": 0}
    last_seen: list[Any] = [None]  # the polling sync's high-water mark

    def sync() -> None:
        counts["syncs"] += 1
        with connect() as c, c.transaction():
            if mode == "polling":
                rows = c.execute(
                    "SELECT id, name, city, updated_at FROM demo_customers "
                    "WHERE %s::timestamptz IS NULL OR updated_at > %s ORDER BY updated_at",
                    [last_seen[0], last_seen[0]],
                ).fetchall()
                for id_, name, city, at in rows:
                    _upsert(c, id_, name, city, at)
                    last_seen[0] = max(last_seen[0] or at, at)
                counts["changes"] += len(rows)
                return
            # Not "seq greater than the last one seen": a transaction can take its number
            # before another and commit after it. Take every change not yet applied, in order.
            changes = c.execute(
                "SELECT seq, op, id, row FROM demo_changes WHERE NOT applied ORDER BY seq "
                "FOR UPDATE SKIP LOCKED"
            ).fetchall()
            for seq, op, id_, row in changes:
                if op == "delete":
                    c.execute("DELETE FROM demo_replica WHERE id = %s", [id_])
                else:
                    _upsert(c, id_, row["name"], row["city"], row["updated_at"])
                c.execute("UPDATE demo_changes SET applied = true WHERE seq = %s", [seq])
            counts["changes"] += len(changes)

    started = time.perf_counter()
    sync()
    with connect() as slow, connect() as app:
        slow.autocommit = False  # a long transaction: it changes a row now, commits later
        slow.execute("UPDATE demo_customers SET city = 'Detroit', updated_at = now() WHERE id = 4")
        time.sleep(0.05)
        with app.transaction():  # an ordinary transaction that starts and commits meanwhile
            app.execute(
                "UPDATE demo_customers SET city = 'Toledo', updated_at = now() WHERE id = 2"
            )
            app.execute("DELETE FROM demo_customers WHERE id = 3")
            app.execute(
                "INSERT INTO demo_customers (id, name, city) VALUES (6, 'Margaret', 'Troy')"
            )
        sync()  # sees the ordinary transaction, not the long one
        slow.commit()
    sync()

    with connect() as c:
        source = set(c.execute("SELECT id, name, city FROM demo_customers").fetchall())
        replica = set(c.execute("SELECT id, name, city FROM demo_replica").fetchall())
    source_ids = {r[0] for r in source}
    deleted_left = sum(1 for r in replica if r[0] not in source_ids)
    missed = sum(1 for r in source if r not in replica)
    return {
        "checks": [
            _check("Replica matches the source", True, source == replica),
            _check("Deleted rows still in the replica", 0, deleted_left),
            _check("Changes missed", 0, missed),
        ],
        "stats": counts,
        "ms": round((time.perf_counter() - started) * 1000),
    }


def _upsert(c: Any, id_: int, name: str, city: str, at: Any) -> None:
    c.execute(
        "INSERT INTO demo_replica (id, name, city, updated_at) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, city = EXCLUDED.city, "
        "updated_at = EXCLUDED.updated_at",
        [id_, name, city, at],
    )


EXPERIMENTS = {
    "cdc": (cdc, ("polling", "change_log")),
}

CATALOG: list[dict[str, Any]] = [
    {
        "id": "cdc",
        "group": "pipelines",
        "title": "Change data capture",
        "question": "A replica of the customers table is synced after each batch of changes, "
        "including a delete and a long transaction that commits after a sync. Does the replica "
        "end up equal to the source?",
        "modes": [
            _mode(
                "polling",
                "Poll updated_at",
                "Each sync copies rows whose updated_at is newer than the newest it has seen.",
            ),
            _mode(
                "change_log",
                "Change log",
                "A trigger writes every insert, update and delete "
                "to a change table in the same transaction; each sync applies the changes not "
                "yet applied, in order. (Production CDC tools such as Debezium read the "
                "write-ahead log instead.)",
            ),
        ],
    },
]
