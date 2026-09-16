"""Reusing SQLite connections across requests, without letting one request's state reach the next.

WHY THIS EXISTS. Measured on the operator's idle host on 2026-09-16: the service used about 2% of a
core with nothing running, and a 90-second py-spy profile put half of that in aiosqlite's connection
threads. `get_db()` opened a NEW connection for every request and every background tick -- a new
thread, a new file handle, a PRAGMA script -- and closed it again. The traffic was modest (42 HTTP
requests a minute), but each bridge's claim long-poll re-runs its claim inside the service every few
seconds, and every re-run paid for a whole connection.

WHAT A POOLED CONNECTION MUST NOT CARRY, and how each is closed off:

* AN OPEN TRANSACTION. A request that raised, was cancelled mid-query, or never committed would hand
  its transaction -- possibly a `BEGIN IMMEDIATE` write lock -- to whoever took the connection next.
  So a returned connection is reset BY A STEP QUEUED ON ITS OWN WORKER THREAD, behind anything still in
  flight there: a cancelled request's query keeps running after its future is cancelled, and a reset
  judged from the event loop could run before it and miss the transaction it was about to open. If the
  reset cannot be confirmed, the connection is closed rather than pooled.
* CHANGED CONNECTION SETTINGS. The import path turns `foreign_keys` off and back on; anything that runs
  a PRAGMA or installs a function, authorizer, trace or progress handler leaves a connection that is no
  longer the one `get_db()` promises. Such a connection is CLOSED on return, never pooled.
* A USE AFTER CLOSE. Before pooling, touching a closed connection raised. A pooled one would silently run
  on whatever request holds it now, so the handle a caller closed is disconnected from the connection
  and keeps raising.
* ANOTHER FILE OR ANOTHER EVENT LOOP. Tests repoint the database path and each builds its own loop;
  a connection is only reused for the path and loop it was pooled under, and the rest are retired.

WHAT IS UNCHANGED. Every checkout is still exclusive -- two requests never share a connection -- so
transaction boundaries, `BEGIN IMMEDIATE` serialisation and the busy-timeout behaviour are exactly what
they were. Idle connections hold no transaction and no statement, so they take no lock; and the reconcile
sweep retires them before its TRUNCATE checkpoint anyway, so an idle connection can never be the reader
that starves it (the 83 MB WAL of 2026-06-18).

WHAT A CHECKOUT ALSO REPORTS. Each checkout notes the table every INSERT, UPDATE, DELETE or REPLACE
names, and hands that list to `on_commit` when the transaction commits (service/change_feed.py, which
tells the dashboard what to refetch). A rollback, or a return without a commit, reports nothing, since
nothing was written. This is the one place every write passes, which is why the report lives here and
not beside the 400 statements that make one.

OPT-IN. The service enables the pool in its lifespan and closes it on shutdown. A caller that uses
`get_db()` with no lifespan -- most of the test suite -- gets a fresh connection exactly as before, so a
pooled file handle can never outlive the temporary directory a test deletes.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable, Optional

import aiosqlite

from service.change_feed import written_table, written_tables_of_script

logger = logging.getLogger(__name__)

#: How many idle connections are kept, across all busy timeouts. Above it a returned connection is
#: closed. Not a limit on concurrency: a checkout with nothing idle opens a new connection, as before.
POOL_IDLE_MAX = 8

# A statement that changes how THIS connection behaves rather than what the database holds.
_CONNECTION_STATE_SQL = re.compile(r"\bpragma\b|\battach\b|\bdetach\b", re.IGNORECASE)

# Methods that install per-connection behaviour. Calling any of them makes the connection unpoolable.
_STATEFUL_METHODS = frozenset({
    # `cursor()` is here because what runs through a bare cursor never passes the SQL checks above.
    "cursor",
    "create_function", "create_aggregate", "create_collation", "set_authorizer",
    "set_progress_handler", "set_trace_callback", "enable_load_extension", "load_extension",
})


def changes_connection_state(sql) -> bool:
    """Whether running `sql` could leave the connection configured differently from a fresh one."""
    return bool(_CONNECTION_STATE_SQL.search(str(sql or "")))


def _reset_on_worker(raw) -> bool:
    """Runs ON THE CONNECTION'S WORKER THREAD, after everything queued before it.

    Ends any transaction still open and answers whether the connection is clean.
    """
    if raw.in_transaction:
        raw.rollback()
    return not raw.in_transaction


def _alive(conn: aiosqlite.Connection) -> bool:
    # aiosqlite exposes no public "is open"; these two are what its own `_execute` checks.
    return bool(getattr(conn, "_running", False)) and getattr(conn, "_connection", None) is not None


def _retire(conn: aiosqlite.Connection) -> None:
    """Close a connection without awaiting it: queues the close and stops its worker thread."""
    try:
        conn.stop()
    except Exception:  # noqa: BLE001 -- a connection already gone needs nothing more
        pass


class PooledConnection:
    """One checkout. Behaves as the `aiosqlite.Connection` it wraps; `close()` returns it to the pool."""

    __slots__ = ("_pool", "_conn", "_path", "_busy_ms", "_dirty", "_writes", "_rows_changed")

    def __init__(self, pool: "ConnectionPool", conn: aiosqlite.Connection, path, busy_ms: int) -> None:
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_busy_ms", busy_ms)
        object.__setattr__(self, "_dirty", False)
        # What this checkout has written since its last commit or rollback, in statement order.
        object.__setattr__(self, "_writes", [])
        # The connection's running count of rows changed, at checkout and after each commit or rollback.
        object.__setattr__(self, "_rows_changed", conn.total_changes)

    def _target(self) -> aiosqlite.Connection:
        conn = object.__getattribute__(self, "_conn")
        if conn is None:
            # The same failure a closed aiosqlite connection gives, so a use-after-close stays loud.
            raise ValueError("Connection closed")
        return conn

    def _mark_dirty(self) -> None:
        object.__setattr__(self, "_dirty", True)

    def _note(self, sql) -> None:
        # Noted when the statement is ISSUED. One that then fails leaves a write reported for a
        # table that did not change, if the caller still commits -- an extra refetch, never a missed one.
        write = written_table(sql) if isinstance(sql, str) else None
        if write is not None:
            object.__getattribute__(self, "_writes").append(write)

    def _take_writes(self) -> list:
        writes = object.__getattribute__(self, "_writes")
        object.__setattr__(self, "_writes", [])
        return writes

    def _rows_changed_since_last(self) -> bool:
        now = self._target().total_changes
        changed = now != object.__getattribute__(self, "_rows_changed")
        object.__setattr__(self, "_rows_changed", now)
        return changed

    def execute(self, sql, parameters=None):
        if changes_connection_state(sql):
            self._mark_dirty()
        self._note(sql)
        return self._target().execute(sql, parameters)

    def executemany(self, sql, parameters):
        if changes_connection_state(sql):
            self._mark_dirty()
        self._note(sql)
        return self._target().executemany(sql, parameters)

    def execute_fetchall(self, sql, parameters=None):
        if changes_connection_state(sql):
            self._mark_dirty()
        self._note(sql)
        return self._target().execute_fetchall(sql, parameters)

    def execute_insert(self, sql, parameters=None):
        if changes_connection_state(sql):
            self._mark_dirty()
        self._note(sql)
        return self._target().execute_insert(sql, parameters)

    async def executescript(self, sql_script):
        if changes_connection_state(sql_script):
            self._mark_dirty()
        # sqlite3 COMMITS whatever is pending before a script and runs the script outside a
        # transaction, so both are durable the moment it returns.
        cursor = await self._target().executescript(sql_script)
        writes = self._take_writes() + written_tables_of_script(sql_script)
        if self._rows_changed_since_last():
            self._report(writes)
        return cursor

    async def commit(self) -> None:
        await self._target().commit()
        writes = self._take_writes()
        # A transaction whose statements matched no rows changed nothing, and says so. MEASURED
        # 2026-09-17: with nothing running, the reconcile sweep's settling UPDATEs matched no rows every
        # minute and made every open dashboard refetch its contracts, runs, messages and stats.
        if self._rows_changed_since_last():
            self._report(writes)

    async def rollback(self) -> None:
        try:
            await self._target().rollback()
        finally:
            self._take_writes()
            self._rows_changed_since_last()

    def _report(self, writes) -> None:
        if writes:
            object.__getattribute__(self, "_pool").report_commit(writes)

    def __getattr__(self, name):
        attr = getattr(self._target(), name)
        if name in _STATEFUL_METHODS:
            self._mark_dirty()
        return attr

    def __setattr__(self, name, value):
        # `row_factory` is reset on return; any other attribute (isolation_level, text_factory, ...)
        # is a setting a fresh connection would not have.
        if name != "row_factory":
            self._mark_dirty()
        setattr(self._target(), name, value)

    async def close(self) -> None:
        conn = object.__getattribute__(self, "_conn")
        if conn is None:
            return
        # Detached FIRST and synchronously, so a second close -- or a use after this one -- can never
        # reach a connection that is back in the pool and possibly checked out by someone else.
        object.__setattr__(self, "_conn", None)
        pool = object.__getattribute__(self, "_pool")
        await pool.release(
            conn,
            object.__getattribute__(self, "_path"),
            object.__getattribute__(self, "_busy_ms"),
            dirty=object.__getattribute__(self, "_dirty"),
        )

    async def __aenter__(self) -> "PooledConnection":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


class ConnectionPool:
    """Idle connections for one database path and one event loop."""

    def __init__(self, open_connection: Callable[[object, int], Awaitable[aiosqlite.Connection]],
                 *, idle_max: int = POOL_IDLE_MAX,
                 on_commit: Optional[Callable[[list], None]] = None) -> None:
        self._open = open_connection
        self._on_commit = on_commit
        self._idle_max = idle_max
        self._idle: dict[int, list[aiosqlite.Connection]] = {}
        self._path = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.enabled = False

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────────
    def enable(self) -> None:
        self.enabled = True

    async def aclose(self) -> None:
        """Disable pooling and CLOSE every idle connection, waiting for each file handle to be released.

        Awaited rather than queued, because whoever stops the service may delete the database next: a
        test's temporary directory on Windows cannot be removed while a worker thread still holds the
        file. Checked-out connections close when they are returned, since the pool is disabled by then.
        """
        self.enabled = False
        idle = [conn for bucket in self._idle.values() for conn in bucket]
        self._idle.clear()
        for conn in idle:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001 -- a broken connection still has to be let go
                _retire(conn)

    def retire_idle(self) -> int:
        """Close every idle connection. Returns how many were retired."""
        retired = 0
        for bucket in self._idle.values():
            while bucket:
                _retire(bucket.pop())
                retired += 1
        self._idle.clear()
        return retired

    async def close_idle(self) -> int:
        """Close every idle connection and WAIT for each to be released. Pooling stays enabled."""
        idle = [conn for bucket in self._idle.values() for conn in bucket]
        self._idle.clear()
        for conn in idle:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001 -- a broken connection still has to be let go
                _retire(conn)
        return len(idle)

    def report_commit(self, writes: list) -> None:
        """A checkout committed `writes`. Never raises into the request that committed them."""
        if self._on_commit is None:
            return
        try:
            self._on_commit(writes)
        except Exception:  # noqa: BLE001 -- the data is already committed; a report must not undo that
            logger.exception("reporting committed writes failed")

    def idle_count(self) -> int:
        return sum(len(bucket) for bucket in self._idle.values())

    # ── checkout / return ────────────────────────────────────────────────────────────────────────
    def _bind(self, path) -> None:
        loop = asyncio.get_running_loop()
        if path != self._path or loop is not self._loop:
            self.retire_idle()
            self._path = path
            self._loop = loop

    async def acquire(self, path, busy_timeout_ms: int):
        if not self.enabled:
            return await self._open(path, busy_timeout_ms)
        self._bind(path)
        bucket = self._idle.get(busy_timeout_ms, [])
        while bucket:
            conn = bucket.pop()
            if _alive(conn):
                return PooledConnection(self, conn, path, busy_timeout_ms)
            _retire(conn)
        conn = await self._open(path, busy_timeout_ms)
        return PooledConnection(self, conn, path, busy_timeout_ms)

    async def release(self, conn: aiosqlite.Connection, path, busy_timeout_ms: int, *, dirty: bool) -> None:
        keep = self.enabled and not dirty and _alive(conn)
        if keep:
            try:
                keep = await conn._execute(_reset_on_worker, conn._conn)
            except BaseException:
                # Cancelled or failed while resetting: its state is unknown, so it is not reused. The
                # exception still propagates -- a cancellation must stay a cancellation.
                _retire(conn)
                raise
        # Re-checked AFTER the await: the pool may have been closed, repointed or moved to another loop
        # while the reset ran -- or before, while this connection was checked out.
        try:
            same_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            same_loop = False
        same_file = path == self._path
        if not keep or not self.enabled or not same_loop or not same_file or self.idle_count() >= self._idle_max:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001 -- closing a broken connection is best-effort
                _retire(conn)
            return
        conn.row_factory = aiosqlite.Row
        self._idle.setdefault(busy_timeout_ms, []).append(conn)
