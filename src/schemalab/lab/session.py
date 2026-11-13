"""A user's transaction lab: two real PostgreSQL sessions, A and B, in a sandbox database.

Security model, in layers:
- The lab database is its own Postgres server with nothing in it but lab schemas.
- Each user gets a role lab_u<id> (no superuser, no CREATEDB/CREATEROLE, at most 8
  connections) that owns only schema lab_u<id>. CONNECT and the public schema are revoked from
  PUBLIC, so one user cannot read another's tables.
- The role's statement_timeout and idle_in_transaction_session_timeout are fixed on the role;
  the statement guard stops SET and set_config from changing them.
- Statements run only as that role, one at a time per session, after guard.check_lab_sql.
- The admin connection only sets up roles and schemas and reads lock and version information,
  with its own short timeouts so a user's lock cannot hang the state view.

A statement that waits for a lock is reported as "waiting" and keeps running in its session's
thread; result() picks it up once the other session commits or rolls back.
"""

import secrets
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus

from schemalab.agent.graph import json_value
from schemalab.lab import patterns
from schemalab.lab.guard import LabSQLError, check_lab_sql
from schemalab.plans import advise, summarize

SESSIONS = ("A", "B")
STATEMENT_TIMEOUT = "30s"  # includes time spent waiting for a lock
IDLE_TIMEOUT = "5min"  # a forgotten open transaction is closed after this
MAX_ROWS = 200
VERSION_ROWS = 30

_SETUP_LOCK = threading.Lock()  # GRANT/REVOKE from two labs at once can conflict in Postgres

_TRANSACTION = {
    TransactionStatus.IDLE: "idle",
    TransactionStatus.INTRANS: "in transaction",
    TransactionStatus.INERROR: "failed",
    TransactionStatus.ACTIVE: "running",
    TransactionStatus.UNKNOWN: "closed",
}

Outcome = dict[str, Any]


class Lab:
    def __init__(self, admin_dsn: str, user_id: int) -> None:
        self.admin_dsn = admin_dsn
        self.role = f"lab_u{int(user_id)}"  # an integer id: never user text
        self.admin: psycopg.Connection[Any] | None = None
        self.admin_lock = threading.Lock()
        self.conns: dict[str, psycopg.Connection[Any]] = {}
        self.planner: psycopg.Connection[Any] | None = None  # a third session for EXPLAIN
        self.plan_lock = threading.Lock()
        self.pattern_lock = threading.Lock()  # experiments share demo_ tables: one at a time
        self.pools = {s: ThreadPoolExecutor(max_workers=1) for s in SESSIONS}
        self.pending: dict[str, Future[Outcome]] = {}
        self.lock = threading.Lock()
        self.password = ""
        self.last_used = time.monotonic()

    # Setup

    def start(self) -> None:
        self.admin = psycopg.connect(self.admin_dsn, autocommit=True)
        self._prepare()
        self._open()
        # Inspection must never wait on a user's lock for long.
        self.admin.execute("SET lock_timeout = '500ms'")
        self.admin.execute("SET statement_timeout = '3s'")

    def _admin(self) -> psycopg.Connection[Any]:
        if self.admin is None:
            raise RuntimeError("The lab has not been started.")
        return self.admin

    def _prepare(self) -> None:
        role = sql.Identifier(self.role)
        self.password = secrets.token_urlsafe(24)
        with _SETUP_LOCK:
            a = self._admin()
            database = sql.Identifier(a.info.dbname)
            a.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(database))
            a.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            # Settings can change without SET; take both other routes away from lab roles.
            a.execute(
                "REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM PUBLIC"
            )
            a.execute("REVOKE UPDATE ON pg_catalog.pg_settings FROM PUBLIC")
            exists = a.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [self.role]).fetchone()
            if exists:
                self._terminate()
                a.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(role, sql.Literal(self.password))
                )
            else:
                a.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                        "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8 PASSWORD {}"
                    ).format(role, sql.Literal(self.password))
                )
            # Two sessions, the planner, and the pattern experiments' worker connections.
            a.execute(sql.SQL("ALTER ROLE {} CONNECTION LIMIT 8").format(role))
            for setting, value in (
                ("statement_timeout", STATEMENT_TIMEOUT),
                ("idle_in_transaction_session_timeout", IDLE_TIMEOUT),
                ("lock_timeout", "0"),
                ("temp_file_limit", "100MB"),  # superuser-only setting: the role cannot raise it
                ("search_path", self.role),
            ):
                a.execute(
                    sql.SQL("ALTER ROLE {} SET {} = {}").format(
                        role, sql.Identifier(setting), sql.Literal(value)
                    )
                )
            a.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role))
            a.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(role))
            a.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(role, role))

    def _open(self) -> None:
        for s in SESSIONS:
            self.conns[s] = psycopg.connect(
                self.admin_dsn,
                user=self.role,
                password=self.password,
                autocommit=True,  # the user's BEGIN and COMMIT control transactions
                application_name=f"schemalab lab {s}",
            )

    def _terminate(self) -> None:
        if self.admin is not None:
            self.admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s",
                [self.role],
            )

    def reset(self) -> None:
        """Close both sessions, drop every table, and open fresh sessions."""
        with self.lock, self.admin_lock:
            self._close_sessions()
            self._admin().execute("RESET statement_timeout")  # setup may take longer than 3 s
            self._prepare()
            self._open()
            self._admin().execute("SET statement_timeout = '3s'")

    def _close_sessions(self) -> None:
        self._terminate()  # also ends a statement still waiting for a lock
        for conn in self.conns.values():
            conn.close()
        if self.planner is not None:
            self.planner.close()
            self.planner = None
        for future in self.pending.values():
            future.cancel()
        self.pending.clear()

    def close(self) -> None:
        with self.lock, self.admin_lock:
            self._close_sessions()
            for pool in self.pools.values():
                pool.shutdown(wait=False, cancel_futures=True)
            if self.admin is not None:
                self.admin.close()

    # Running statements

    def run(self, session: str, statement: str, wait_s: float = 1.5) -> Outcome:
        """Run one statement in session A or B. Raises LabSQLError for a refused statement and
        RuntimeError if the session is still waiting for an earlier one."""
        if session not in SESSIONS:
            raise ValueError(f"Session must be one of {', '.join(SESSIONS)}.")
        text = check_lab_sql(statement)
        with self.lock:
            earlier = self.pending.get(session)
            if earlier is not None and not earlier.done():
                if self._blocked(session):
                    raise RuntimeError(f"Session {session} is still waiting for a lock.")
                raise RuntimeError(f"Session {session} is still running its last statement.")
            future = self.pools[session].submit(self._execute, session, text)
            self.pending[session] = future
            self.last_used = time.monotonic()
        try:
            outcome = future.result(timeout=wait_s)
        except TimeoutError:
            return {**self._unfinished(session), "statement": text}
        with self.lock:
            if self.pending.get(session) is future:
                del self.pending[session]
        return outcome

    def result(self, session: str) -> Outcome | None:
        """The outcome of a statement that was waiting, once it finishes; None if nothing ran."""
        with self.lock:
            future = self.pending.get(session)
            if future is None:
                return None
            if not future.done():
                return self._unfinished(session)
            del self.pending[session]
        return future.result()

    def _blocked(self, session: str) -> bool:
        """Whether the session's statement waits for a lock, not merely takes a while."""
        pid = self.conns[session].info.backend_pid
        with self.admin_lock:
            row = (
                self._admin()
                .execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid])
                .fetchone()
            )
        return bool(row and row[0] == "Lock")

    def _unfinished(self, session: str) -> Outcome:
        if self._blocked(session):
            return {"state": "waiting", "transaction": "waiting"}
        return {"state": "running", "transaction": "running"}

    def _execute(self, session: str, statement: str) -> Outcome:
        conn = self.conns[session]
        if conn.closed:
            self.conns[session] = conn = psycopg.connect(
                self.admin_dsn, user=self.role, password=self.password, autocommit=True
            )
        notices: list[str] = []

        def on_notice(diag: psycopg.errors.Diagnostic) -> None:
            notices.append(diag.message_primary or "")

        conn.add_notice_handler(on_notice)
        out: Outcome
        try:
            with conn.cursor() as cur:
                cur.execute(statement.encode())  # bytes: sent as written, no % placeholders
                columns = [d.name for d in cur.description] if cur.description else []
                rows = cur.fetchmany(MAX_ROWS) if cur.description else []
                out = {
                    "state": "done",
                    "status": cur.statusmessage,
                    "columns": columns,
                    "rows": [[json_value(v) for v in r] for r in rows],
                }
        except psycopg.OperationalError as e:
            out = {"state": "error", "error": _message(e), "code": e.sqlstate}
            if conn.closed:
                out["error"] += " The session was closed and will reopen on the next statement."
        except psycopg.Error as e:
            out = {"state": "error", "error": _message(e), "code": e.sqlstate}
        finally:
            if not conn.closed:
                conn.remove_notice_handler(on_notice)
        return {
            **out,
            "statement": statement,
            "notices": notices,
            "transaction": self._status(session),
        }

    def _status(self, session: str) -> str:
        conn = self.conns.get(session)
        if conn is None or conn.closed:
            return "closed"
        return _TRANSACTION.get(conn.info.transaction_status, "unknown")

    # Query plans

    def pattern(self, name: str, mode: str) -> dict[str, Any]:
        """Run a transaction-pattern experiment (fixed SQL only) as this lab's role."""

        def connect() -> psycopg.Connection[Any]:
            return psycopg.connect(
                self.admin_dsn, user=self.role, password=self.password, autocommit=True
            )

        # Refuse rather than queue: a queued run would hold a server thread while it waits.
        if not self.pattern_lock.acquire(blocking=False):
            raise RuntimeError("An experiment is already running in your lab.")
        try:
            return patterns.run(name, mode, connect)
        finally:
            self.pattern_lock.release()

    def explain(self, statement: str) -> dict[str, Any]:
        """EXPLAIN ANALYZE a query and advise on indexes. EXPLAIN ANALYZE runs the query, so it
        takes SELECT and WITH only and always runs inside a transaction that is rolled back:
        even a data-modifying WITH changes nothing."""
        text = check_lab_sql(statement)
        if text.split(None, 1)[0].upper() not in ("SELECT", "WITH", "TABLE", "VALUES"):
            raise LabSQLError("Explain a SELECT query; plans for changes would change your data.")
        with self.plan_lock:
            if self.planner is None or self.planner.closed:
                self.planner = psycopg.connect(
                    self.admin_dsn,
                    user=self.role,
                    password=self.password,
                    autocommit=True,
                    application_name="schemalab lab plans",
                )
            conn = self.planner
            conn.execute("BEGIN")
            try:
                with conn.cursor() as cur:
                    cur.execute(b"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + text.encode())
                    row = cur.fetchone()
                    indexes = conn.execute(
                        "SELECT tablename, indexdef FROM pg_indexes "
                        "WHERE schemaname = current_schema()"
                    ).fetchall()
            finally:
                conn.execute("ROLLBACK")
        if row is None:
            raise LabSQLError("PostgreSQL returned no plan.")
        summary = summarize(row[0])
        return {"summary": summary, "advice": advise(summary, _index_columns(indexes))}

    # Looking inside

    def state(self) -> dict[str, Any]:
        """Each session's transaction, who waits for whom, the locks they hold or want, and the
        current row versions (xmin, xmax, ctid) of every lab table."""
        pids = {s: c.info.backend_pid for s, c in self.conns.items() if not c.closed}
        names = {pid: s for s, pid in pids.items()}
        with self.admin_lock:
            a = self._admin()
            activity = {
                row[0]: row
                for row in a.execute(
                    "SELECT pid, backend_xid::text, pg_blocking_pids(pid), wait_event_type "
                    "FROM pg_stat_activity WHERE pid = ANY(%s)",
                    [list(pids.values())],
                ).fetchall()
            }
            sessions: dict[str, Any] = {}
            for s in SESSIONS:
                pid = pids.get(s)
                row = activity.get(pid) if pid else None
                waiting = bool(row and row[3] == "Lock")
                sessions[s] = {
                    "pid": pid,
                    "transaction": "waiting for a lock" if waiting else self._status(s),
                    "xid": row[1] if row else None,
                    "blocked_by": sorted(names[p] for p in (row[2] if row else []) if p in names),
                }
            locks = [
                {
                    "session": names.get(pid, "?"),
                    "type": locktype,
                    "relation": relation,
                    "mode": mode,
                    "granted": granted,
                    "transaction": xid,
                }
                for pid, locktype, relation, mode, granted, xid in a.execute(
                    "SELECT l.pid, l.locktype, c.relname, l.mode, l.granted, "
                    "l.transactionid::text FROM pg_locks l "
                    "LEFT JOIN pg_class c ON c.oid = l.relation "
                    "WHERE l.pid = ANY(%s) AND l.locktype <> 'virtualxid' "
                    "AND (c.oid IS NULL OR c.relnamespace = %s::regnamespace) "
                    "ORDER BY l.granted DESC, l.pid",
                    [list(pids.values()), self.role],
                ).fetchall()
            ]
            versions = []
            tables = a.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY 1 LIMIT 5",
                [self.role],
            ).fetchall()
            for (table,) in tables:
                query = sql.SQL("SELECT xmin::text, xmax::text, ctid::text, * FROM {}.{} LIMIT {}")
                try:
                    cur = a.execute(
                        query.format(sql.Identifier(self.role), sql.Identifier(table), VERSION_ROWS)
                    )
                    columns = [d.name for d in cur.description or []][3:]
                    rows = [
                        {
                            "xmin": r[0],
                            "xmax": r[1],
                            "ctid": r[2],
                            "values": [json_value(v) for v in r[3:]],
                        }
                        for r in cur.fetchall()
                    ]
                    versions.append({"table": table, "columns": columns, "rows": rows})
                except psycopg.errors.LockNotAvailable:
                    versions.append({"table": table, "columns": [], "rows": [], "locked": True})
        return {"sessions": sessions, "locks": locks, "versions": versions}


def _message(e: psycopg.Error) -> str:
    diag = e.diag
    return (diag.message_primary or str(e)).strip() + (
        f" {diag.message_detail}" if diag.message_detail else ""
    )


def _index_columns(rows: list[tuple[str, str]]) -> dict[str, list[list[str]]]:
    """Plain column lists of the existing indexes, from pg_indexes.indexdef."""
    out: dict[str, list[list[str]]] = {}
    for table, definition in rows:
        inside = definition[definition.rfind("(") + 1 : definition.rfind(")")]
        columns = [c.strip().strip('"').split()[0] for c in inside.split(",") if c.strip()]
        out.setdefault(table, []).append(columns)
    return out
