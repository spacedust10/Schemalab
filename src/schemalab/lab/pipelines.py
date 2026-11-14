"""Data pipeline experiments: change data capture, partitioning, slowly changing dimensions
and the move from an OLTP schema to a star schema. Same shape as the transaction patterns:
fixed SQL in demo_ tables of the user's lab schema, the naive way beside the right one, and
checks anyone can read.
"""

import time
from typing import Any

from psycopg import sql

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


# Partitioning

_MARCH = (
    "SELECT sum(amount) FROM demo_sales WHERE sold_on >= DATE '2024-03-01' "
    "AND sold_on < DATE '2024-04-01'"
)


def partitioning(mode: str, connect: Connect) -> Result:
    """A year of sales, 10,000 a month: total one month, then retire the oldest month."""
    with connect() as c:
        c.execute("DROP TABLE IF EXISTS demo_sales CASCADE")
        if mode == "partitioned":
            c.execute(
                "CREATE TABLE demo_sales (id int, sold_on date NOT NULL, amount numeric NOT NULL) "
                "PARTITION BY RANGE (sold_on)"
            )
            for month in range(1, 13):
                c.execute(
                    sql.SQL(
                        "CREATE TABLE {} PARTITION OF demo_sales FOR VALUES FROM ({}) TO ({})"
                    ).format(
                        sql.Identifier(f"demo_sales_2024_{month:02d}"),
                        sql.Literal(f"2024-{month:02d}-01"),
                        sql.Literal(f"2024-{month + 1:02d}-01" if month < 12 else "2025-01-01"),
                    )
                )
        else:
            c.execute(
                "CREATE TABLE demo_sales (id int, sold_on date NOT NULL, amount numeric NOT NULL)"
            )
        c.execute(
            "INSERT INTO demo_sales SELECT i, DATE '2024-01-01' + "
            "make_interval(months => i % 12, days => i % 28), (i % 97) + 1 "
            "FROM generate_series(1, 120000) i"
        )
        c.execute("ANALYZE demo_sales")

        started = time.perf_counter()
        c.execute("SET max_parallel_workers_per_gather = 0")  # one process: plain row counts
        explain = sql.SQL("EXPLAIN (ANALYZE, FORMAT JSON) ") + sql.SQL(_MARCH)
        row = c.execute(explain).fetchone()
        plan = row[0][0]["Plan"] if row else {}
        scans = [n for n in _nodes(plan) if n.get("Relation Name")]
        read = sum(
            round(
                (n.get("Actual Rows", 0) + n.get("Rows Removed by Filter", 0))
                * n.get("Actual Loops", 1)
            )
            for n in scans
        )
        query_ms = (time.perf_counter() - started) * 1000

        before = _size(c)
        started = time.perf_counter()
        if mode == "partitioned":
            c.execute("DROP TABLE demo_sales_2024_01")
        else:
            c.execute("DELETE FROM demo_sales WHERE sold_on < DATE '2024-02-01'")
        retire_ms = (time.perf_counter() - started) * 1000
        freed = before - _size(c)
    return {
        "checks": [
            _check("Rows read to total March", 10_000, read),
            _check("January's disk space given back", True, freed > 0),
        ],
        "stats": {
            "tables_scanned": len({n["Relation Name"] for n in scans}),
            "query_ms": round(query_ms),
            "retire_ms": round(retire_ms),
        },
        "ms": round(query_ms + retire_ms),
    }


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    out, stack = [], [plan]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(node.get("Plans", []))
    return out


def _size(c: Any) -> int:
    """Bytes on disk for demo_sales and every partition of it."""
    row = c.execute(
        "SELECT coalesce(sum(pg_total_relation_size(relid)), 0) "
        "FROM pg_partition_tree('demo_sales')"
    ).fetchone()
    return int(row[0]) if row else 0


# Slowly changing dimension, Type 2

# (customer, day, amount), loaded in date order; Ada moves from MI to OH on March 1.
_SALES = [
    ("ada", "2024-01-10", 100),
    ("ada", "2024-02-12", 200),
    ("grace", "2024-02-20", 50),
    ("alan", "2024-03-05", 70),
    ("ada", "2024-03-15", 300),
]
_MOVE_ON = "2024-03-01"


def scd2(mode: str, connect: Connect) -> Result:
    """Load a quarter of sales into a fact table while one customer moves state."""
    with connect() as c:
        c.execute("DROP TABLE IF EXISTS demo_fact_sales, demo_dim_customer")
        c.execute(
            "CREATE TABLE demo_dim_customer (customer_key serial PRIMARY KEY, "
            "customer_id text NOT NULL, state text NOT NULL, valid_from date NOT NULL, "
            "valid_to date, is_current boolean NOT NULL DEFAULT true)"
        )
        c.execute(
            "CREATE TABLE demo_fact_sales (customer_key int NOT NULL "
            "REFERENCES demo_dim_customer, sold_on date NOT NULL, amount int NOT NULL)"
        )
        c.execute(
            "INSERT INTO demo_dim_customer (customer_id, state, valid_from) VALUES "
            "('ada', 'MI', '2024-01-01'), ('grace', 'MI', '2024-01-01'), "
            "('alan', 'OH', '2024-01-01')"
        )
        started = time.perf_counter()
        moved = False
        for customer, day, amount in _SALES:
            if not moved and day >= _MOVE_ON:
                _move_ada(c, mode)
                moved = True
            # A sale points at the customer's version that is current when it is loaded.
            c.execute(
                "INSERT INTO demo_fact_sales SELECT customer_key, %s, %s FROM demo_dim_customer "
                "WHERE customer_id = %s AND is_current",
                [day, amount, customer],
            )
        by_state = dict(
            c.execute(
                "SELECT d.state, sum(f.amount) FROM demo_fact_sales f "
                "JOIN demo_dim_customer d USING (customer_key) GROUP BY d.state"
            ).fetchall()
        )
        row = c.execute(
            "SELECT count(*) FROM demo_dim_customer WHERE customer_id = 'ada'"
        ).fetchone()
        versions = row[0] if row else 0
    return {
        "checks": [
            _check("Q1 revenue in Michigan", 350, int(by_state.get("MI", 0))),
            _check("Q1 revenue in Ohio", 370, int(by_state.get("OH", 0))),
            _check("Versions of Ada kept", 2, versions),
        ],
        "stats": {"sales": len(_SALES), "customers": 3},
        "ms": round((time.perf_counter() - started) * 1000),
    }


def _move_ada(c: Any, mode: str) -> None:
    if mode == "overwrite":  # Type 1: the row now says OH, for every sale it ever had
        c.execute("UPDATE demo_dim_customer SET state = 'OH' WHERE customer_id = 'ada'")
        return
    # Type 2: close the current version and add a new one from the day of the move.
    c.execute(
        "UPDATE demo_dim_customer SET valid_to = %s, is_current = false "
        "WHERE customer_id = 'ada' AND is_current",
        [_MOVE_ON],
    )
    c.execute(
        "INSERT INTO demo_dim_customer (customer_id, state, valid_from) VALUES ('ada', 'OH', %s)",
        [_MOVE_ON],
    )


# OLTP to star schema

_OLTP_SETUP = [
    "CREATE TABLE demo_regions (id int PRIMARY KEY, name text NOT NULL)",
    "CREATE TABLE demo_categories (id int PRIMARY KEY, name text NOT NULL)",
    "CREATE TABLE demo_oltp_customers (id int PRIMARY KEY, name text NOT NULL, "
    "region_id int NOT NULL REFERENCES demo_regions)",
    "CREATE TABLE demo_products (id int PRIMARY KEY, name text NOT NULL, "
    "category_id int NOT NULL REFERENCES demo_categories, price numeric NOT NULL)",
    "CREATE TABLE demo_oltp_orders (id int PRIMARY KEY, customer_id int NOT NULL "
    "REFERENCES demo_oltp_customers, ordered_at date NOT NULL)",
    "CREATE TABLE demo_order_lines (order_id int REFERENCES demo_oltp_orders, line int, "
    "product_id int NOT NULL REFERENCES demo_products, qty int NOT NULL, "
    "unit_price numeric NOT NULL, PRIMARY KEY (order_id, line))",
    "INSERT INTO demo_regions VALUES (1, 'Midwest'), (2, 'Northeast'), (3, 'South'), (4, 'West')",
    "INSERT INTO demo_categories VALUES (1, 'Books'), (2, 'Games'), (3, 'Garden'), "
    "(4, 'Kitchen'), (5, 'Toys')",
    "INSERT INTO demo_oltp_customers SELECT i, 'customer ' || i, i % 4 + 1 "
    "FROM generate_series(1, 200) i",
    "INSERT INTO demo_products SELECT i, 'product ' || i, i % 5 + 1, (i % 20 + 1) * 5 "
    "FROM generate_series(1, 50) i",
    "INSERT INTO demo_oltp_orders SELECT i, i % 200 + 1, DATE '2024-01-01' + i % 366 "
    "FROM generate_series(1, 3000) i",
    "INSERT INTO demo_order_lines SELECT o, l, (o * 7 + l) % 50 + 1, l, p.price "
    "FROM generate_series(1, 3000) o CROSS JOIN generate_series(1, 3) l "
    "JOIN demo_products p ON p.id = (o * 7 + l) % 50 + 1",
]

_STAR_LOAD = [
    "CREATE TABLE demo_dim_date (date_key int PRIMARY KEY, day date NOT NULL, "
    "month text NOT NULL, year int NOT NULL)",
    "CREATE TABLE demo_dim_customer_star (customer_key serial PRIMARY KEY, "
    "customer_id int NOT NULL, name text NOT NULL, region text NOT NULL)",
    "CREATE TABLE demo_dim_product (product_key serial PRIMARY KEY, product_id int NOT NULL, "
    "name text NOT NULL, category text NOT NULL)",
    "CREATE TABLE demo_fact_order_lines (date_key int NOT NULL REFERENCES demo_dim_date, "
    "customer_key int NOT NULL REFERENCES demo_dim_customer_star, "
    "product_key int NOT NULL REFERENCES demo_dim_product, quantity int NOT NULL, "
    "revenue numeric NOT NULL)",
    "INSERT INTO demo_dim_date SELECT to_char(d, 'YYYYMMDD')::int, d, to_char(d, 'YYYY-MM'), "
    "extract(year FROM d)::int FROM generate_series(DATE '2024-01-01', DATE '2024-12-31', "
    "INTERVAL '1 day') AS g(d)",
    # Region and category are folded into their dimensions: no joins left to make later.
    "INSERT INTO demo_dim_customer_star (customer_id, name, region) SELECT c.id, c.name, r.name "
    "FROM demo_oltp_customers c JOIN demo_regions r ON r.id = c.region_id",
    "INSERT INTO demo_dim_product (product_id, name, category) SELECT p.id, p.name, k.name "
    "FROM demo_products p JOIN demo_categories k ON k.id = p.category_id",
    "INSERT INTO demo_fact_order_lines SELECT to_char(o.ordered_at, 'YYYYMMDD')::int, "
    "dc.customer_key, dp.product_key, l.qty, l.qty * l.unit_price "
    "FROM demo_order_lines l JOIN demo_oltp_orders o ON o.id = l.order_id "
    "JOIN demo_dim_customer_star dc ON dc.customer_id = o.customer_id "
    "JOIN demo_dim_product dp ON dp.product_id = l.product_id",
]

_OLTP_QUESTION = (
    "SELECT k.name, r.name, to_char(o.ordered_at, 'YYYY-MM'), sum(l.qty * l.unit_price) "
    "FROM demo_order_lines l JOIN demo_oltp_orders o ON o.id = l.order_id "
    "JOIN demo_products p ON p.id = l.product_id JOIN demo_categories k ON k.id = p.category_id "
    "JOIN demo_oltp_customers c ON c.id = o.customer_id JOIN demo_regions r ON r.id = c.region_id "
    "GROUP BY 1, 2, 3"
)
_STAR_QUESTION = (
    "SELECT p.category, c.region, d.month, sum(f.revenue) FROM demo_fact_order_lines f "
    "JOIN demo_dim_product p USING (product_key) "
    "JOIN demo_dim_customer_star c USING (customer_key) "
    "JOIN demo_dim_date d USING (date_key) GROUP BY 1, 2, 3"
)


def star(mode: str, connect: Connect) -> Result:
    """Revenue by category, region and month, from the order-taking schema or from a star
    schema loaded from it."""
    with connect() as c:
        c.execute(
            "DROP TABLE IF EXISTS demo_fact_order_lines, demo_dim_date, demo_dim_customer_star, "
            "demo_dim_product, demo_order_lines, demo_oltp_orders, demo_products, "
            "demo_oltp_customers, demo_categories, demo_regions"
        )
        for statement in _OLTP_SETUP:
            c.execute(statement)
        source = set(c.execute(_OLTP_QUESTION).fetchall())
        load_ms = 0.0
        question = _OLTP_QUESTION
        if mode == "star":
            started = time.perf_counter()
            for statement in _STAR_LOAD:
                c.execute(statement)
            load_ms = (time.perf_counter() - started) * 1000
            question = _STAR_QUESTION
        # Only this experiment's tables: a bare ANALYZE would also work through the user's own.
        c.execute(
            "ANALYZE demo_order_lines, demo_oltp_orders, demo_products, demo_categories, "
            "demo_oltp_customers, demo_regions"
        )
        if mode == "star":
            c.execute(
                "ANALYZE demo_fact_order_lines, demo_dim_date, demo_dim_customer_star, "
                "demo_dim_product"
            )
        row = c.execute(sql.SQL("EXPLAIN (FORMAT JSON) ") + sql.SQL(question)).fetchone()
        plan = row[0][0]["Plan"] if row else {}
        tables = {n["Relation Name"] for n in _nodes(plan) if n.get("Relation Name")}
        started = time.perf_counter()
        answer = set(c.execute(question).fetchall())
        query_ms = (time.perf_counter() - started) * 1000
    return {
        "checks": [
            _check("Tables joined for the question", 4, len(tables)),
            _check("Same answer as the source", True, answer == source),
        ],
        "stats": {"groups": len(answer), "load_ms": round(load_ms), "query_ms": round(query_ms)},
        "ms": round(load_ms + query_ms),
    }


EXPERIMENTS = {
    "cdc": (cdc, ("polling", "change_log")),
    "partitioning": (partitioning, ("one_table", "partitioned")),
    "scd2": (scd2, ("overwrite", "type2")),
    "star": (star, ("oltp", "star")),
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
    {
        "id": "partitioning",
        "group": "pipelines",
        "title": "Partitioning",
        "question": "A year of sales, 10,000 rows a month. How much does totalling March "
        "read, and does retiring January give its disk space back?",
        "modes": [
            _mode(
                "one_table", "One table", "All rows in one table; January is retired with DELETE."
            ),
            _mode(
                "partitioned",
                "Monthly partitions",
                "PARTITION BY RANGE (sold_on), one "
                "partition per month; the planner skips months a query cannot need, and "
                "January is retired by dropping its partition.",
            ),
        ],
    },
    {
        "id": "scd2",
        "group": "pipelines",
        "title": "Slowly changing dimension",
        "question": "Ada moves from Michigan to Ohio on March 1, between her sales. Is Q1 "
        "revenue by state still right afterwards?",
        "modes": [
            _mode(
                "overwrite",
                "Overwrite (Type 1)",
                "The customer row's state is updated in "
                "place; every sale joined to it now reads the new state.",
            ),
            _mode(
                "type2",
                "Versioned rows (Type 2)",
                "The current row is closed with valid_to "
                "and a new version starts; each sale keeps the key of the version current "
                "when it was loaded.",
            ),
        ],
    },
    {
        "id": "star",
        "group": "pipelines",
        "title": "OLTP to star schema",
        "question": "Revenue by category, region and month, over 3,000 orders. How many "
        "tables does the question touch, and is the answer the same?",
        "modes": [
            _mode(
                "oltp",
                "Order-taking schema",
                "Asks the normalized tables the application "
                "writes to: order lines, orders, products, categories, customers, regions.",
            ),
            _mode(
                "star",
                "Star schema",
                "Loads a fact table of order lines with product, "
                "customer and date dimensions (category and region folded in), then asks it.",
            ),
        ],
    },
]
