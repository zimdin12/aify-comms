"""The connection pool may reuse a connection, and nothing a request did may reach the next one.

WHY THESE TESTS. The pool exists because the idle service spent about 2% of a core opening and closing
SQLite connections (service/db_pool.py, measured 2026-09-16). Reusing them is only safe if a returned
connection is indistinguishable from a fresh one, so every way a request can leave state behind is
driven here: an open transaction, a write lock held by a request that was cancelled mid-query, a PRAGMA,
a changed attribute, a use after close, and a database path or event loop that changed underneath.

Each hazard is paired with a CONTROL that shows the pool really reuses the connection in the clean case,
so a pool that quietly stopped pooling -- which passes every hazard test -- still fails here.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

import aiosqlite

import service.db as db_module
from service.db import SQLITE_BUSY_TIMEOUT_MS, SQLITE_CLAIM_BUSY_TIMEOUT_MS, _open_connection
from service.db_pool import ConnectionPool, changes_connection_state


def _raw(handle):
    """The aiosqlite connection behind a checkout."""
    return object.__getattribute__(handle, "_conn")


class PoolTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "pool.db"
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        self.pool = ConnectionPool(_open_connection)
        self.pool.enable()

    def tearDown(self):
        self._tmp.cleanup()

    def run_async(self, coro_fn):
        async def wrapped():
            try:
                return await coro_fn()
            finally:
                await self.pool.aclose()
        return asyncio.run(wrapped())

    def get(self, busy=SQLITE_BUSY_TIMEOUT_MS):
        return self.pool.acquire(self.path, busy)


class CleanReuse(PoolTestCase):
    def test_CONTROL_a_returned_connection_is_reused(self):
        async def body():
            first = await self.get()
            raw = _raw(first)
            await first.close()
            second = await self.get()
            self.assertIs(_raw(second), raw, "a clean connection was not reused -- the pool does nothing")
            await second.close()
        self.run_async(body)

    def test_two_checkouts_at_once_never_share_a_connection(self):
        async def body():
            a = await self.get()
            b = await self.get()
            self.assertIsNot(_raw(a), _raw(b))
            await a.close()
            await b.close()
        self.run_async(body)

    def test_the_busy_timeout_a_checkout_asked_for_is_the_one_it_gets(self):
        # Claim probes ask for a short timeout; a connection pooled under the long one must not serve them.
        async def body():
            slow = await self.get(SQLITE_BUSY_TIMEOUT_MS)
            slow_raw = _raw(slow)
            await slow.close()
            quick = await self.get(SQLITE_CLAIM_BUSY_TIMEOUT_MS)
            self.assertIsNot(_raw(quick), slow_raw)
            row = await (await quick.execute("PRAGMA busy_timeout")).fetchone()
            self.assertEqual(row[0], SQLITE_CLAIM_BUSY_TIMEOUT_MS)
            await quick.close()
        self.run_async(body)

    def test_rows_come_back_as_Row_even_after_a_caller_changed_the_factory(self):
        async def body():
            first = await self.get()
            first.row_factory = None
            raw = _raw(first)
            await first.close()
            second = await self.get()
            self.assertIs(_raw(second), raw, "control: changing row_factory alone must not stop reuse")
            self.assertIs(second.row_factory, aiosqlite.Row)
            await second.close()
        self.run_async(body)


class NothingCarriesOver(PoolTestCase):
    def test_an_uncommitted_transaction_is_rolled_back_before_reuse(self):
        async def body():
            first = await self.get()
            await first.execute("INSERT INTO t (v) VALUES ('never committed')")
            self.assertTrue(first.in_transaction)
            raw = _raw(first)
            await first.close()
            second = await self.get()
            self.assertIs(_raw(second), raw)
            self.assertFalse(second.in_transaction, "the next request inherited an open transaction")
            count = (await (await second.execute("SELECT COUNT(*) FROM t")).fetchone())[0]
            self.assertEqual(count, 0, "an uncommitted write became visible to the next request")
            await second.close()
        self.run_async(body)

    def test_a_write_lock_left_by_a_cancelled_request_is_released(self):
        # The hazard the worker-thread reset exists for: the request is cancelled while its BEGIN IMMEDIATE
        # is still queued, so the transaction opens AFTER the request stopped waiting for it.
        async def body():
            import time

            handle = await self.get()
            # A slow operation first, so the BEGIN IMMEDIATE is certainly still QUEUED when the request is
            # cancelled. Without it the cancel could land before the BEGIN was queued at all, and this test
            # would pass without a lock ever being taken.
            blocker = asyncio.ensure_future(handle._execute(time.sleep, 0.3))
            pending = asyncio.ensure_future(handle.execute("BEGIN IMMEDIATE"))
            await asyncio.sleep(0.05)
            self.assertFalse(pending.done(), "control: the BEGIN ran before the cancel, so nothing is being tested")
            pending.cancel()
            await handle.close()
            await asyncio.gather(blocker, return_exceptions=True)
            # Another connection must be able to take the write lock at once.
            other = sqlite3.connect(self.path, timeout=0)
            try:
                other.execute("BEGIN IMMEDIATE")
                other.rollback()
            except sqlite3.OperationalError as exc:
                self.fail(f"a pooled connection kept the write lock after its request was cancelled: {exc}")
            finally:
                other.close()
        self.run_async(body)

    def test_a_PRAGMA_makes_the_connection_unpoolable(self):
        async def body():
            first = await self.get()
            await first.execute("PRAGMA foreign_keys=OFF")
            raw = _raw(first)
            await first.close()
            second = await self.get()
            self.assertIsNot(_raw(second), raw, "a connection with changed PRAGMAs was handed to another request")
            fk = (await (await second.execute("PRAGMA foreign_keys")).fetchone())[0]
            self.assertEqual(fk, 1)
            await second.close()
        self.run_async(body)

    def test_changing_any_other_attribute_makes_the_connection_unpoolable(self):
        async def body():
            first = await self.get()
            first.text_factory = bytes
            raw = _raw(first)
            await first.close()
            second = await self.get()
            self.assertIsNot(_raw(second), raw)
            await second.close()
        self.run_async(body)

    def test_installing_a_function_makes_the_connection_unpoolable(self):
        async def body():
            first = await self.get()
            await first.create_function("twice", 1, lambda x: x * 2)
            raw = _raw(first)
            await first.close()
            second = await self.get()
            self.assertIsNot(_raw(second), raw)
            await second.close()
        self.run_async(body)

    def test_a_closed_handle_stays_closed_even_while_its_connection_serves_someone_else(self):
        async def body():
            first = await self.get()
            await first.close()
            second = await self.get()
            with self.assertRaises(ValueError):
                await first.execute("SELECT 1")
            await first.close()  # a second close is a no-op, not a second release
            self.assertEqual(self.pool.idle_count(), 0, "a double close returned a checked-out connection")
            await second.close()
        self.run_async(body)


class WhereAConnectionBelongs(PoolTestCase):
    def test_a_new_database_path_does_not_reuse_the_old_files_connections(self):
        async def body():
            first = await self.get()
            raw = _raw(first)
            await first.close()
            other = Path(self._tmp.name) / "other.db"
            sqlite3.connect(other).close()
            second = await self.pool.acquire(other, SQLITE_BUSY_TIMEOUT_MS)
            self.assertIsNot(_raw(second), raw)
            await second.close()
        self.run_async(body)

    def test_a_connection_checked_out_before_the_path_changed_is_not_pooled_under_the_new_one(self):
        async def body():
            old = await self.get()
            other = Path(self._tmp.name) / "other.db"
            sqlite3.connect(other).close()
            new = await self.pool.acquire(other, SQLITE_BUSY_TIMEOUT_MS)
            old_raw = _raw(old)
            await old.close()
            self.assertIsNone(getattr(old_raw, "_connection", None), "a connection to the old file was pooled, not closed")
            await new.close()
            again = await self.pool.acquire(other, SQLITE_BUSY_TIMEOUT_MS)
            self.assertIsNot(_raw(again), old_raw, "a connection to the old file served the new one")
            await again.close()
        self.run_async(body)

    def test_a_new_event_loop_does_not_reuse_the_previous_loops_connections(self):
        seen = []

        async def first_loop():
            handle = await self.get()
            seen.append(_raw(handle))
            await handle.close()

        async def second_loop():
            handle = await self.get()
            seen.append(_raw(handle))
            await handle.close()
            await self.pool.aclose()

        asyncio.run(first_loop())
        asyncio.run(second_loop())
        self.assertIsNot(seen[0], seen[1])

    def test_idle_connections_are_bounded(self):
        async def body():
            pool = ConnectionPool(_open_connection, idle_max=2)
            pool.enable()
            handles = [await pool.acquire(self.path, SQLITE_BUSY_TIMEOUT_MS) for _ in range(4)]
            for handle in handles:
                await handle.close()
            self.assertEqual(pool.idle_count(), 2)
            await pool.aclose()
        self.run_async(body)

    def test_shutdown_closes_idle_connections_and_returns_close_the_rest(self):
        async def body():
            idle = await self.get()
            out = await self.get()
            idle_raw, out_raw = _raw(idle), _raw(out)
            await idle.close()
            await self.pool.aclose()
            self.assertIsNone(getattr(idle_raw, "_connection", None), "shutdown left an idle connection open")
            await out.close()
            self.assertIsNone(getattr(out_raw, "_connection", None), "a connection returned after shutdown stayed open")
            self.assertEqual(self.pool.idle_count(), 0)
        self.run_async(body)

    def test_a_disabled_pool_opens_and_closes_a_fresh_connection_every_time(self):
        # The contract for everything that uses get_db() without the service's lifespan.
        async def body():
            pool = ConnectionPool(_open_connection)
            first = await pool.acquire(self.path, SQLITE_BUSY_TIMEOUT_MS)
            self.assertIsInstance(first, aiosqlite.Connection)
            await first.close()
            self.assertIsNone(getattr(first, "_connection", None))
            self.assertEqual(pool.idle_count(), 0)
        self.run_async(body)

    def test_retiring_idle_connections_closes_them(self):
        async def body():
            handle = await self.get()
            raw = _raw(handle)
            await handle.close()
            self.assertEqual(self.pool.retire_idle(), 1)
            self.assertEqual(self.pool.idle_count(), 0)
            second = await self.get()
            self.assertIsNot(_raw(second), raw)
            await second.close()
        self.run_async(body)


class WhatCountsAsConnectionState(unittest.TestCase):
    def test_statements_that_configure_the_connection(self):
        for sql in ("PRAGMA foreign_keys=OFF", "pragma wal_checkpoint(TRUNCATE)", "ATTACH DATABASE 'x' AS y", "DETACH y"):
            self.assertTrue(changes_connection_state(sql), sql)

    def test_CONTROL_ordinary_statements_and_look_alike_words_do_not(self):
        for sql in (
            "SELECT * FROM agents",
            "INSERT INTO messages (attachments) VALUES (?)",
            "UPDATE agents SET launch_mode = 'detached'",
        ):
            self.assertFalse(changes_connection_state(sql), sql)


class TheServiceOwnsThePool(unittest.TestCase):
    """Driven through the REAL lifespan: the pool is on only while the service runs."""

    def setUp(self):
        import service.main as main_module
        from service.config import ServiceConfig

        self._tmp = tempfile.TemporaryDirectory()
        self._main = main_module
        self._original_get_config = main_module.get_config
        self._original_path = db_module._db_path
        config = ServiceConfig(data_dir=self._tmp.name, config_dir=self._tmp.name, mcp_enabled=False, cors_origins=["*"])
        main_module.get_config = lambda: config
        self.app = main_module.create_app()

    def tearDown(self):
        self._main.get_config = self._original_get_config
        db_module._db_path = self._original_path
        # Removing the directory is itself an assertion on Windows: a pooled connection left open by the
        # shutdown would hold the database file and fail this cleanup.
        self._tmp.cleanup()

    def test_the_pool_is_on_while_the_service_runs_and_empty_once_it_stops(self):
        from fastapi.testclient import TestClient

        pool = db_module.CONNECTION_POOL
        self.assertFalse(pool.enabled, "the pool is on before any service started it")
        with TestClient(self.app, base_url="http://127.0.0.1:8800") as client:
            self.assertTrue(pool.enabled, "the running service does not pool")
            response = client.get("/api/v1/agents")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertGreaterEqual(pool.idle_count(), 1, "a request's connection was not returned to the pool")
        self.assertFalse(pool.enabled, "the pool stayed on after the service stopped")
        self.assertEqual(pool.idle_count(), 0, "shutdown left idle connections open")


if __name__ == "__main__":
    unittest.main()
