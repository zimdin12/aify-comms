"""A bridge saying "I am alive" must not be able to lie about WHAT it is. Tested by calling it.

`_upsert_bridge_liveness_beat` was inline in `agent_heartbeat` until v0.5.4, so exercising it meant
driving `POST /agents/{id}/heartbeat`. It is now a leaf and these tests run it against a real sqlite
database.

THE DEMOTION GUARD IS WHY THIS DESERVES ITS OWN TESTS (FIX SET B3, 2026-06-03). The host-side bridge
posts a 30-second beat with `bridgeKind="resident"`. The SAME agent may already have a wrapper-child
or channel-sidecar row carrying the authoritative managed kind. A plain COALESCE let the generic beat
overwrite it, after which the live-managed-child and live-sidecar predicates stopped matching and the
managed agent silently lost its claimer — no error, no log, just work that never got claimed.

The tests below therefore spend most of their attention on which incoming kind may overwrite which
stored kind, rather than on the happy path of refreshing `last_seen`.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.bridge_liveness_beat import _upsert_bridge_liveness_beat

SCHEMA = """
CREATE TABLE agents (
    id TEXT PRIMARY KEY, machine_id TEXT, runtime TEXT, session_mode TEXT
);
CREATE TABLE bridge_instances (
    id TEXT PRIMARY KEY, agent_id TEXT, machine_id TEXT, runtime TEXT, session_mode TEXT,
    session_handle TEXT, terminal_id TEXT, bridge_kind TEXT,
    registered_at TEXT, last_seen TEXT, superseded_by TEXT, superseded_at TEXT
);
"""
#: `id TEXT PRIMARY KEY` is copied from the real schema deliberately and is not decoration. It is the
#: entire reason the beat uses `INSERT OR IGNORE`: without the constraint the insert SUCCEEDS on a
#: bridge id another agent already owns, and these tests would describe a second row that production
#: cannot produce. A fixture schema that is merely close enough tests a system that does not exist.

BEFORE = "2026-08-15T10:00:00Z"
NOW = "2026-08-15T12:00:00Z"


class BridgeLivenessBeatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)
        await self.db.execute("INSERT INTO agents VALUES ('a1', 'machine-1', 'hermes', 'managed')")

    async def asyncTearDown(self):
        await self.db.close()

    async def _bridge(self, bridge_id="b1", *, agent="a1", kind="resident"):
        await self.db.execute(
            "INSERT INTO bridge_instances VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (bridge_id, agent, "machine-1", "hermes", "managed", "", "", kind, BEFORE, BEFORE, "", None),
        )

    async def _beat(self, *, bridge_id="b1", kind="resident", liveness=True, agent_id="a1"):
        await _upsert_bridge_liveness_beat(
            self.db, agent_id, bridge_id, kind, {"liveness": liveness} if liveness else {}, NOW)

    async def _row(self, bridge_id="b1"):
        return await (await self.db.execute(
            "SELECT * FROM bridge_instances WHERE id = ?", (bridge_id,))).fetchone()

    async def test_a_beat_refreshes_last_seen(self):
        await self._bridge()
        await self._beat()
        self.assertEqual(NOW, (await self._row())["last_seen"])

    async def test_a_beat_from_a_bridge_with_no_row_CREATES_one(self):
        """The reason this upserts. An idle channel-sidecar that never claimed has no row, and a
        plain UPDATE would no-op forever — the bridge would beat and never appear live."""
        await self._beat(kind="channel-sidecar-unknown")
        row = await self._row()
        self.assertIsNotNone(row, "a beat from an unknown bridge must create its row")
        self.assertEqual(NOW, row["last_seen"])
        self.assertEqual("machine-1", row["machine_id"], "the new row takes the agent's machine")
        self.assertEqual("hermes", row["runtime"], "the new row takes the agent's runtime")

    async def test_a_created_row_with_no_kind_defaults_to_resident(self):
        await self._beat(kind="")
        self.assertEqual("resident", (await self._row())["bridge_kind"])

    async def test_a_RESIDENT_beat_cannot_demote_a_managed_wrapper_child(self):
        """FIX SET B3. The defect: the generic 30s beat overwrote the authoritative kind, the
        live-managed-child predicate stopped matching, and the agent lost its claimer silently."""
        await self._bridge(kind="managed-wrapper-child")
        await self._beat(kind="resident")
        row = await self._row()
        self.assertEqual("managed-wrapper-child", row["bridge_kind"])
        self.assertEqual(NOW, row["last_seen"], "the beat must still count as liveness")

    async def test_an_EMPTY_kind_cannot_demote_a_managed_wrapper_child(self):
        await self._bridge(kind="managed-wrapper-child")
        await self._beat(kind="")
        self.assertEqual("managed-wrapper-child", (await self._row())["bridge_kind"])

    async def test_neither_can_demote_a_channel_sidecar(self):
        for incoming in ("resident", ""):
            with self.subTest(incoming=incoming):
                await self.db.execute("DELETE FROM bridge_instances")
                await self._bridge(kind="channel-sidecar")
                await self._beat(kind=incoming)
                self.assertEqual("channel-sidecar", (await self._row())["bridge_kind"])

    async def test_any_OTHER_incoming_kind_still_wins(self):
        """The guard is narrow on purpose: it blocks two demotions, not every update.

        A real promotion — a bridge that registers as a managed wrapper child — must still be able
        to correct a row that says `resident`.
        """
        await self._bridge(kind="resident")
        await self._beat(kind="managed-wrapper-child")
        self.assertEqual("managed-wrapper-child", (await self._row())["bridge_kind"])

    async def test_an_empty_kind_never_blanks_an_ordinary_stored_kind(self):
        """COALESCE(NULLIF(?, ''), bridge_kind): absence is not an instruction to forget."""
        await self._bridge(kind="managed-host")
        await self._beat(kind="")
        self.assertEqual("managed-host", (await self._row())["bridge_kind"])

    async def test_a_beat_never_clears_supersession(self):
        """A superseded row must stay superseded; reviving it would resurrect a dead claimer."""
        await self._bridge(kind="resident")
        await self.db.execute(
            "UPDATE bridge_instances SET superseded_by = 'b2', superseded_at = ? WHERE id = 'b1'",
            (BEFORE,))
        await self._beat()
        row = await self._row()
        self.assertEqual("b2", row["superseded_by"])
        self.assertEqual(BEFORE, row["superseded_at"])

    async def test_a_beat_without_the_liveness_flag_writes_nothing(self):
        await self._bridge()
        await self._beat(liveness=False)
        self.assertEqual(BEFORE, (await self._row())["last_seen"])

    async def test_a_beat_with_no_bridge_id_writes_nothing(self):
        await self._bridge()
        await self._beat(bridge_id="")
        self.assertEqual(BEFORE, (await self._row())["last_seen"])
        self.assertIsNone(await self._row(""), "an id-less beat must not create a phantom row")

    async def test_a_beat_cannot_STEAL_or_duplicate_another_agents_bridge_id(self):
        """Both writes are agent-scoped, and the id is a primary key, so neither path can land.

        The UPDATE misses on `agent_id`, the INSERT OR IGNORE is refused by the key, and the
        follow-up UPDATE misses again. Worth asserting as a pair: if either the key or the agent
        clause were dropped, one agent's beat would silently take over another agent's bridge row.
        """
        await self._bridge("b1", agent="someone-else", kind="managed-wrapper-child")
        await self._beat(bridge_id="b1", agent_id="a1")
        rows = await (await self.db.execute("SELECT * FROM bridge_instances")).fetchall()
        self.assertEqual(1, len(rows), "the beat must not create a second row under the same id")
        self.assertEqual("someone-else", rows[0]["agent_id"])
        self.assertEqual(BEFORE, rows[0]["last_seen"], "the id matched but the agent did not")


if __name__ == "__main__":
    unittest.main()
