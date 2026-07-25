"""Session history is retained, not hoarded — and pruning must never touch anything live.

A managed agent gets a NEW agent_sessions row per cold start; that is correct, each row is a
distinct worker process. What was missing is retention: nothing pruned the terminal rows, so they
accumulated forever (mc-senior-dev held 79, the fleet 449 back to April) and the operator read the
pile as "duplicate sessions".

This is the only DELETE in the reconcile loop, and `terminal_sessions.session_id` is ON DELETE
CASCADE, so the guards matter more than the feature. Every one of them is pinned below.
"""
import asyncio
import time

from service.db import get_db
from service.routers import api_v2

from service.tests._base import FastApiTestCase


def _iso_ago(seconds: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


DAY = 86400


class SessionHistoryRetentionTests(FastApiTestCase):
    DB_NAME = "aify-session-retention-test.db"

    def _seed(self, session_id, agent_id, *, status, ended_ago=None, started_ago=None):
        started = _iso_ago(started_ago if started_ago is not None else (ended_ago or 0))
        ended = "" if ended_ago is None else _iso_ago(ended_ago)

        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO agent_sessions
                        (id, agent_id, environment_id, runtime, mode, status,
                         started_at, ended_at, last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (session_id, agent_id, "env-1", "hermes", "managed-warm", status,
                     started, ended, started),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _seed_terminal(self, terminal_id, session_id, agent_id, status):
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO terminal_sessions
                        (id, session_id, agent_id, environment_id, runtime, status,
                         created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (terminal_id, session_id, agent_id, "env-1", "hermes", status,
                     api_v2._now(), api_v2._now()),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _prune(self):
        async def _run():
            db = await get_db()
            try:
                n = await api_v2._prune_session_history(db, limit=500)
                await db.commit()
                return n
            finally:
                await db.close()

        return asyncio.run(_run())

    def _exists(self, session_id):
        async def _run():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT 1 FROM agent_sessions WHERE id = ?", (session_id,)
                )).fetchone()
                return row is not None
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_old_terminal_history_is_pruned(self):
        # 12 old ended rows: the newest 10 are kept by policy, so only the oldest 2 may go.
        for i in range(12):
            self._seed(f"sess-old-{i:02d}", "prune-agent", status="ended",
                       ended_ago=(60 + i) * DAY, started_ago=(60 + i) * DAY)
        self.assertEqual(self._prune(), 2, "only rows beyond the keep-newest window may be pruned")
        self.assertFalse(self._exists("sess-old-11"))
        self.assertFalse(self._exists("sess-old-10"))
        self.assertTrue(self._exists("sess-old-00"), "the newest history must survive")

    def test_recent_history_is_kept(self):
        """Inside the retention window nothing is pruned, however many rows there are."""
        for i in range(20):
            self._seed(f"sess-recent-{i:02d}", "recent-agent", status="ended",
                       ended_ago=DAY, started_ago=DAY + i)
        self.assertEqual(self._prune(), 0)

    def test_live_session_is_never_pruned(self):
        """A live row has no ended_at and a non-terminal status — untouchable at any age."""
        self._seed("sess-live", "live-agent", status="running", started_ago=200 * DAY)
        for i in range(12):
            self._seed(f"sess-live-pad-{i:02d}", "live-agent", status="ended",
                       ended_ago=(100 + i) * DAY, started_ago=(100 + i) * DAY)
        self._prune()
        self.assertTrue(self._exists("sess-live"), "a live session must survive pruning")

    def test_contradictory_live_status_without_ended_at_is_never_pruned(self):
        """status=running with no ended_at is a stale row the reconcilers heal — not ours to
        delete, because deleting it would cascade its terminal away."""
        self._seed("sess-contra", "contra-agent", status="running", started_ago=300 * DAY)
        for i in range(12):
            self._seed(f"sess-contra-pad-{i:02d}", "contra-agent", status="ended",
                       ended_ago=(150 + i) * DAY, started_ago=(150 + i) * DAY)
        self._prune()
        self.assertTrue(self._exists("sess-contra"))

    def test_session_with_a_LIVE_terminal_is_never_pruned(self):
        """THE CASCADE GUARD. terminal_sessions.session_id is ON DELETE CASCADE, so pruning a
        session that still owns a live console would kill that console."""
        for i in range(12):
            self._seed(f"sess-cas-pad-{i:02d}", "cascade-agent", status="ended",
                       ended_ago=(200 + i) * DAY, started_ago=(200 + i) * DAY)
        self._seed("sess-cascade", "cascade-agent", status="ended",
                   ended_ago=400 * DAY, started_ago=400 * DAY)
        self._seed_terminal("term-live", "sess-cascade", "cascade-agent", "attached")
        self._prune()
        self.assertTrue(
            self._exists("sess-cascade"),
            "a session whose terminal is still live must not be pruned — CASCADE would take the "
            "live console with it",
        )

    def test_dead_terminal_does_not_protect_history(self):
        """The mirror of the cascade guard: a DEAD terminal is no reason to keep the row."""
        for i in range(12):
            self._seed(f"sess-dead-pad-{i:02d}", "dead-term-agent", status="ended",
                       ended_ago=(200 + i) * DAY, started_ago=(200 + i) * DAY)
        self._seed("sess-dead-term", "dead-term-agent", status="ended",
                   ended_ago=400 * DAY, started_ago=400 * DAY)
        self._seed_terminal("term-dead", "sess-dead-term", "dead-term-agent", "stopped")
        self._prune()
        self.assertFalse(self._exists("sess-dead-term"))

    def test_retention_can_be_disabled(self):
        async def _disable():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO settings (key, value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    ("session_history_retention_days", "0"),
                )
                await db.commit()
            finally:
                await db.close()

        for i in range(12):
            self._seed(f"sess-off-{i:02d}", "disabled-agent", status="ended",
                       ended_ago=(300 + i) * DAY, started_ago=(300 + i) * DAY)
        asyncio.run(_disable())
        self.assertEqual(self._prune(), 0, "retention_days=0 must disable pruning entirely")

    def test_prune_is_bounded_per_pass(self):
        """Writes stay short — one sweep must never become a long write transaction."""
        for i in range(40):
            self._seed(f"sess-bound-{i:02d}", "bound-agent", status="ended",
                       ended_ago=(500 + i) * DAY, started_ago=(500 + i) * DAY)

        async def _run():
            db = await get_db()
            try:
                n = await api_v2._prune_session_history(db, limit=5)
                await db.commit()
                return n
            finally:
                await db.close()

        self.assertEqual(asyncio.run(_run()), 5, "limit must cap a single pass")

    def test_keep_per_agent_is_operator_settable(self):
        """The floor is a SETTING, not a constant (operator: "all these day numbers and stuff
        should be settings configurable")."""
        async def _set(key, value):
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO settings (key, value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )
                await db.commit()
            finally:
                await db.close()

        for i in range(12):
            self._seed(f"sess-keep-{i:02d}", "keep-agent", status="ended",
                       ended_ago=(90 + i) * DAY, started_ago=(90 + i) * DAY)
        asyncio.run(_set("session_history_keep_per_agent", 3))
        # keep 3 of 12 → 9 pruned (default 10 would have pruned only 2)
        self.assertEqual(self._prune(), 9)

    def test_unsafe_keep_per_agent_is_clamped_not_obeyed(self):
        """keep=0 would let the sweep delete an agent's entire history, including the row the
        dashboard is describing. Clamp to the safety floor instead of trusting the value."""
        async def _set(value):
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO settings (key, value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    ("session_history_keep_per_agent", str(value)),
                )
                await db.commit()
            finally:
                await db.close()

        for i in range(4):
            self._seed(f"sess-clamp-{i:02d}", "clamp-agent", status="ended",
                       ended_ago=(90 + i) * DAY, started_ago=(90 + i) * DAY)
        asyncio.run(_set(0))
        self._prune()
        remaining = [i for i in range(4) if self._exists(f"sess-clamp-{i:02d}")]
        self.assertEqual(len(remaining), api_v2.SESSION_HISTORY_MIN_KEEP_PER_AGENT,
                         "keep=0 must clamp to the safety floor, never wipe an agent's history")

    def test_garbage_settings_fall_back_to_defaults(self):
        """A typo in Settings must not raise inside the reconcile loop, nor prune with a nonsense
        bound."""
        async def _set(key, value):
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO settings (key, value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
                await db.commit()
            finally:
                await db.close()

        for i in range(12):
            self._seed(f"sess-junk-{i:02d}", "junk-agent", status="ended",
                       ended_ago=(90 + i) * DAY, started_ago=(90 + i) * DAY)
        asyncio.run(_set("session_history_retention_days", "not-a-number"))
        asyncio.run(_set("session_history_keep_per_agent", ""))
        # Falls back to the documented defaults (3 days / keep 10) → 2 of 12 pruned.
        self.assertEqual(self._prune(), 2)
