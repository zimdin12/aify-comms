"""The change feed tells dashboards which tables a commit touched, and nothing it did not commit.

What each test would catch:

* THE CLASSIFIER against SQLite's own reading. Every write statement written as a literal in the service
  is compiled by SQLite with an authorizer attached, and the table (and, for an UPDATE, the columns) the
  parser reports must be the ones SQLite reports. A statement shape the parser misreads is a panel that
  never refreshes -- or, if it reads a real change as liveness, one that refreshes a minute late.
* THE LIVENESS DECLARATION names tables and columns that exist, so a renamed column cannot silently turn
  every heartbeat into a full change.
* THE CHECKOUT reports on commit only: a rollback, or a connection returned without a commit, reports
  nothing, because nothing was written.
* THE FEED coalesces a burst into one event, keeps liveness in its own field on its own window, and sends
  nothing while no service is attached.
* THE STATUS CACHE reports a derived status that moved, and only one that moved.
* THE SERVICE attaches the feed for its lifetime, and a real write reaches the socket manager as
  `data_changed`.
"""

from __future__ import annotations

import ast
import asyncio
import re
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import service.change_feed as change_feed
import service.db as db_module
from service.change_feed import LIVENESS_WRITES, ChangeFeed, Write, written_table
from service.db import SQLITE_BUSY_TIMEOUT_MS, _open_connection, init_db
from service.db_pool import ConnectionPool

SERVICE = Path(__file__).resolve().parents[1]
EXECUTE_METHODS = {"execute", "executemany", "execute_fetchall", "execute_insert"}
WRITE_START = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


def _write_literals():
    """Every write statement passed to an execute method as a string literal, outside the tests."""
    found = []
    for path in sorted(SERVICE.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in EXECUTE_METHODS or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and WRITE_START.match(first.value):
                found.append((f"{path.relative_to(SERVICE.parent)}:{node.lineno}", first.value))
    return found


def _sqlite_reading(conn: sqlite3.Connection, sql: str):
    """(table, updated columns) as SQLite's authorizer reports them while compiling `sql`."""
    actions = []

    def authorizer(action, arg1, arg2, _db, trigger):
        # The statement's own writes, which is what the parser reads: a trigger's writes (the agents
        # note-generation count, db.py) arrive with the trigger's name and are its consequence.
        if trigger:
            return sqlite3.SQLITE_OK
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE):
            actions.append((arg1.lower(), None))
        elif action == sqlite3.SQLITE_UPDATE:
            actions.append((arg1.lower(), arg2.lower()))
        return sqlite3.SQLITE_OK

    conn.set_authorizer(authorizer)
    try:
        bindings = ()
        for _ in range(3):
            try:
                conn.execute("EXPLAIN " + sql, bindings)
                break
            except sqlite3.ProgrammingError as exc:
                count = re.search(r"uses (\d+)", str(exc))
                if count:
                    bindings = (None,) * int(count.group(1))
                    continue
                names = re.findall(r":(\w+)", sql)
                bindings = {name: None for name in names}
    finally:
        conn.set_authorizer(None)
    if not actions:
        return None
    table = actions[0][0]
    columns = frozenset(column for name, column in actions if name == table and column)
    return table, columns


class TheClassifierReadsWritesTheWaySQLiteDoes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls._tmp.name) / "schema.db"
        asyncio.run(init_db(cls.path))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_every_literal_write_names_the_table_and_columns_sqlite_compiles(self):
        literals = _write_literals()
        # Positive control: the census read 300-odd write statements on 2026-09-17. A handful means the
        # walk broke, not that the service stopped writing.
        self.assertGreater(len(literals), 150, "control: the literal walk found almost no write statements")
        wrong, uncompiled = [], []
        with closing(sqlite3.connect(self.path)) as conn:
            for where, sql in literals:
                try:
                    expected = _sqlite_reading(conn, sql)
                except sqlite3.Error as exc:
                    uncompiled.append(f"{where}: {exc}")
                    continue
                got = written_table(sql)
                if expected is None or got is None:
                    wrong.append(f"{where}: sqlite={expected} parser={got}")
                    continue
                table, columns = expected
                is_update = sql.lstrip().upper().startswith("UPDATE")
                if got.table != table or (is_update and got.columns != columns):
                    wrong.append(f"{where}: sqlite=({table}, {sorted(columns)}) parser={got}")
        self.assertEqual(uncompiled, [], "statements SQLite could not compile, so nothing checked them")
        self.assertEqual(wrong, [])

    def test_CONTROL_the_comparison_can_say_no(self):
        with closing(sqlite3.connect(self.path)) as conn:
            table, columns = _sqlite_reading(conn, "UPDATE agents SET last_seen = ?, status_note = ? WHERE id = ?")
        self.assertEqual(table, "agents")
        self.assertEqual(columns, frozenset({"last_seen", "status_note"}))
        self.assertNotEqual(Write("agents", frozenset({"last_seen"})), Write(table, columns))

    def test_reads_and_transaction_statements_are_not_writes(self):
        for sql in ("SELECT * FROM agents", "BEGIN IMMEDIATE", "PRAGMA busy_timeout = 5000", "  select 1"):
            self.assertIsNone(written_table(sql), sql)

    def test_the_liveness_declaration_names_real_tables_and_columns(self):
        with closing(sqlite3.connect(self.path)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            problems = []
            for table, columns in LIVENESS_WRITES.items():
                if table not in tables:
                    problems.append(f"no table {table}")
                    continue
                real = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                problems += [f"no column {table}.{column}" for column in sorted(columns or ()) if column not in real]
        self.assertEqual(problems, [])

    def test_the_heartbeat_is_liveness_and_an_operator_status_is_not(self):
        heartbeats = [sql for _, sql in _write_literals() if "SET last_seen = ?, status = CASE" in sql]
        self.assertTrue(heartbeats, "control: the heartbeat statement was not found in the service")
        for sql in heartbeats:
            self.assertTrue(written_table(sql).liveness, sql)
        operator = written_table("UPDATE agents SET status = ?, status_note = ?, last_seen = ? WHERE id = ?")
        self.assertFalse(operator.liveness, "a status the operator set was classed as a heartbeat")
        self.assertFalse(written_table("INSERT INTO agents (id) VALUES (?)").liveness, "a new agent is not liveness")


class _Manager:
    def __init__(self):
        self.sent = []

    def change_subscribers(self):
        return ["subscriber"]

    async def broadcast(self, event, data=None, *, to=None):
        self.sent.append((event, data))


class ACheckoutReportsWhatItCommitted(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "feed.db"
        asyncio.run(init_db(self.path))
        self.reports = []
        self.pool = ConnectionPool(_open_connection, on_commit=self.reports.append)
        self.pool.enable()

    def tearDown(self):
        self._tmp.cleanup()

    def run_async(self, body):
        async def wrapped():
            try:
                await body()
            finally:
                await self.pool.aclose()
        asyncio.run(wrapped())

    def _insert(self):
        return "INSERT INTO settings (key, value) VALUES ('change-feed-test', '1')"

    def test_a_commit_whose_writes_matched_no_rows_reports_nothing(self):
        async def body():
            db = await self.pool.acquire(self.path, SQLITE_BUSY_TIMEOUT_MS)
            await db.execute("UPDATE agents SET model = 'x' WHERE id = 'nobody'")
            await db.commit()
            await db.execute(self._insert())
            await db.commit()
            await db.close()
        self.run_async(body)
        self.assertEqual([[w.table for w in report] for report in self.reports], [["settings"]],
                         "a no-op UPDATE was reported, or a real insert after it was not")

    def test_a_rollback_reports_nothing_and_does_not_leak_into_the_next_commit(self):
        async def body():
            db = await self.pool.acquire(self.path, SQLITE_BUSY_TIMEOUT_MS)
            await db.execute(self._insert())
            await db.rollback()
            await db.execute("INSERT INTO settings (key, value) VALUES ('change-feed-after-rollback', '1')")
            await db.commit()
            await db.close()
        self.run_async(body)
        self.assertEqual([[w.table for w in report] for report in self.reports], [["settings"]])

    def test_a_connection_returned_without_a_commit_reports_nothing(self):
        async def body():
            db = await self.pool.acquire(self.path, SQLITE_BUSY_TIMEOUT_MS)
            await db.execute(self._insert())
            await db.close()
            again = await self.pool.acquire(self.path, SQLITE_BUSY_TIMEOUT_MS)
            await again.commit()
            await again.close()
        self.run_async(body)
        self.assertEqual(self.reports, [])

    def test_a_failing_report_does_not_fail_the_commit(self):
        def explode(_writes):
            raise RuntimeError("feed broke")
        self.pool = ConnectionPool(_open_connection, on_commit=explode)
        self.pool.enable()

        async def body():
            db = await self.pool.acquire(self.path, SQLITE_BUSY_TIMEOUT_MS)
            await db.execute(self._insert())
            await db.commit()
            await db.close()
        self.run_async(body)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT value FROM settings WHERE key = 'change-feed-test'").fetchone(), ("1",))


class ACommitWakesTheClaimThatWaitsForIt(unittest.TestCase):
    """MEASURED 2026-09-17: a dashboard keystroke waited 512 ms and 954 ms for aify-env's claim, because
    only the dispatch route woke `terminal-control` and fifteen places write terminal controls."""

    def test_the_scope_map_names_exactly_the_scopes_claim_routes_wait_on(self):
        waited = set()
        for path in (SERVICE / "routers").rglob("*.py"):
            waited |= set(re.findall(r'scope="([a-z-]+)"', path.read_text(encoding="utf-8")))
        self.assertGreaterEqual(len(waited), 4, "control: the route walk found almost no claim scopes")
        self.assertEqual(set(change_feed.CLAIM_SCOPES.values()), waited)

    def test_a_committed_terminal_control_wakes_its_waiter_and_a_message_does_not(self):
        from service import longpoll

        async def waiter_woken_by(table_sql):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "wake.db"
                await init_db(path)
                feed = ChangeFeed()
                pool = ConnectionPool(_open_connection, on_commit=feed.committed)
                pool.enable()
                try:
                    wait = asyncio.create_task(longpoll._wait_once("terminal-control", 2.0))
                    await asyncio.sleep(0.05)
                    started = asyncio.get_running_loop().time()
                    db = await pool.acquire(path, SQLITE_BUSY_TIMEOUT_MS)
                    try:
                        # The row stands alone: no terminal exists for it to reference.
                        await db.execute("PRAGMA foreign_keys=OFF")
                        await db.execute(table_sql)
                        await db.commit()
                    finally:
                        await db.close()
                    await wait
                    return asyncio.get_running_loop().time() - started
                finally:
                    await pool.aclose()

        control = asyncio.run(waiter_woken_by(
            "INSERT INTO terminal_controls (id, terminal_id, environment_id, action, status, requested_at) "
            "VALUES ('wake-1', 't1', 'e1', 'input', 'pending', '2026-01-01T00:00:00Z')"))
        message = asyncio.run(waiter_woken_by(
            "INSERT INTO settings (key, value) VALUES ('wake-control', '1')"))
        self.assertLess(control, 0.5, "a committed terminal control did not wake the claim")
        self.assertGreater(message, 1.5, "an unrelated commit woke the terminal-control claim")


class TheFeedCoalescesAndSeparatesLiveness(unittest.TestCase):
    def test_a_burst_is_one_event_with_a_rising_seq(self):
        async def body():
            feed, manager = ChangeFeed(), _Manager()
            feed.attach(manager)
            feed.committed([Write("messages", frozenset())])
            feed.committed([Write("dispatch_runs", frozenset({"status"}))])
            await asyncio.sleep(change_feed.COALESCE_S + 0.2)
            feed.committed([Write("channels", frozenset())])
            await asyncio.sleep(change_feed.COALESCE_S + 0.2)
            return feed, manager
        feed, manager = asyncio.run(body())
        self.assertEqual([event for event, _ in manager.sent], ["data_changed", "data_changed"])
        first, second = (data for _, data in manager.sent)
        self.assertEqual(first["tables"], ["dispatch_runs", "messages"])
        self.assertEqual((first["seq"], second["seq"]), (1, 2))
        self.assertEqual(first["instance"], feed.instance)

    def test_liveness_waits_its_own_window_and_rides_along_with_a_real_change(self):
        async def body():
            feed, manager = ChangeFeed(), _Manager()
            feed.attach(manager)
            feed.committed([Write("agents", frozenset({"last_seen"}))])
            await asyncio.sleep(change_feed.COALESCE_S + 0.2)
            sent_before_change = list(manager.sent)
            feed.committed([Write("settings", frozenset())])
            await asyncio.sleep(change_feed.COALESCE_S + 0.2)
            feed.detach()
            return sent_before_change, manager
        before, manager = asyncio.run(body())
        self.assertEqual(before, [], "liveness alone was sent on the short window")
        self.assertEqual(len(manager.sent), 1)
        self.assertEqual(manager.sent[0][1]["tables"], ["settings"])
        self.assertEqual(manager.sent[0][1]["liveness"], ["agents"])

    def test_an_unattached_feed_sends_and_schedules_nothing(self):
        async def body():
            feed = ChangeFeed()
            feed.committed([Write("messages", frozenset())])
            return feed
        feed = asyncio.run(body())
        self.assertEqual(feed.pending(), (frozenset(), frozenset()))
        self.assertIsNone(feed._flush_handle)


class TheStatusCacheReportsAMovedStatus(unittest.TestCase):
    def test_only_a_changed_status_is_reported(self):
        from service.reconcilers import status_cache

        moved = []
        with mock.patch.object(status_cache.CHANGE_FEED, "agent_status_moved", moved.append):
            status_cache._live_state_drop("feed-agent")
            status_cache._live_state_set("feed-agent", {"status": "online"})
            status_cache._live_state_set("feed-agent", {"status": "online"})
            status_cache._live_state_set("feed-agent", {"status": "offline"})
            status_cache._live_state_drop("feed-agent")
        self.assertEqual(moved, ["feed-agent"], "a first computation, or an unchanged status, was reported")


class TheServiceRunsTheFeed(unittest.TestCase):
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
        self._tmp.cleanup()

    def test_a_write_reaches_the_socket_manager_as_data_changed_while_the_service_runs(self):
        from fastapi.testclient import TestClient

        feed = change_feed.CHANGE_FEED
        self.assertIsNone(feed._manager, "the feed is attached before any service started")
        with TestClient(self.app, base_url="http://127.0.0.1:8800") as client:
            manager = self.app.state.ws_manager
            self.assertIs(feed._manager, manager)
            sent = []

            async def record(event, data=None, *, to=None):
                sent.append((event, data))
            manager.broadcast = record
            response = client.post("/api/v1/agents", json={"agentId": "feed-agent", "role": "coder"})
            self.assertEqual(response.status_code, 200, response.text)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(event == "data_changed" for event, _ in sent):
                time.sleep(0.05)
        changes = [data for event, data in sent if event == "data_changed"]
        self.assertTrue(changes, f"no data_changed was sent; saw {[event for event, _ in sent]}")
        self.assertIn("agents", {table for data in changes for table in data["tables"]})
        self.assertIsNone(feed._manager, "the feed stayed attached after the service stopped")


class OnlyASocketThatAskedIsSentChanges(unittest.TestCase):
    """MEASURED 2026-09-17: a dashboard tab loaded before `data_changed` existed refetched all ten endpoints
    on every one, because its code refetches on any unknown event. Service CPU went 1.58% -> 4.21% of a
    core with one such tab open. So the event goes only to sockets that asked for it."""

    class _Socket:
        def __init__(self):
            self.frames = []

        async def accept(self):
            pass

        async def send_text(self, text):
            self.frames.append(text)

    def test_data_changed_reaches_the_asking_socket_and_not_the_old_one(self):
        import json
        from service.ws import ConnectionManager

        async def body():
            manager = ConnectionManager()
            old, new = self._Socket(), self._Socket()
            await manager.connect(old)
            await manager.connect(new, wants_changes=True)
            feed = ChangeFeed()
            feed.attach(manager)
            feed.committed([Write("messages", frozenset())])
            await feed.flush()
            await manager.broadcast("message_sent", {})
            manager.disconnect(new)
            self.assertEqual(manager.change_subscribers(), [], "a closed socket is still subscribed")
            return old, new
        old, new = asyncio.run(body())
        events = lambda sock: [json.loads(frame)["event"] for frame in sock.frames]
        self.assertEqual(events(new), ["data_changed", "message_sent"])
        self.assertEqual(events(old), ["message_sent"], "a socket that did not ask was sent data_changed")


class TheStatusPushRunsOnlyWhileSomebodyWatches(unittest.TestCase):
    def test_no_socket_no_recompute(self):
        from service import status_push

        calls = []

        async def recompute():
            calls.append(1)
            return 0

        class Manager:
            count = 0

            def change_subscribers(self):
                return ["subscriber"] * self.count

        async def body():
            manager = Manager()
            with mock.patch.object(status_push, "refresh_expired_statuses_once", recompute):
                task = asyncio.create_task(status_push.periodic_status_push(manager, interval_s=0.01))
                await asyncio.sleep(0.1)
                idle_calls = len(calls)
                manager.count = 1
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return idle_calls
        idle_calls = asyncio.run(body())
        self.assertEqual(idle_calls, 0, "statuses were recomputed with no dashboard connected")
        self.assertGreater(len(calls), 0, "control: a connected dashboard got no recompute")


if __name__ == "__main__":
    unittest.main()
