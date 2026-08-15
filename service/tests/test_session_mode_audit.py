"""The mode-switch audit trail, tested by calling the writer.

`_record_session_mode_switch_audit` was inline in `switch_agent_session_mode` until v0.5.4, so
exercising it meant driving `PATCH /agents/{id}/session-mode`. It is now a leaf and these tests run
it against a real sqlite database.

THE SYNTHETIC RUN IS THE WHOLE STORY. `dispatch_events.run_id` is a NOT NULL foreign key to
`dispatch_runs(id)`, so an AGENT-level event has nothing to hang off. The workaround is an anchor
row: a `dispatch_runs` record marked `audit` in both mode columns, born `completed` so it can never
enter the claim or queue paths, with the event attached to it.

`completed` IS LOAD-BEARING and is what most of these tests are about. A row born `queued` or
`running` would be claimable, and a bridge would pick up an audit record and try to execute it as
work — an audit entry that spawns a turn is worse than no audit entry.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.session_mode_audit import _record_session_mode_switch_audit

SCHEMA = """
CREATE TABLE dispatch_runs (
    id TEXT PRIMARY KEY, from_agent TEXT, target_agent TEXT, dispatch_mode TEXT,
    execution_mode TEXT, runtime TEXT, message_type TEXT, subject TEXT, body TEXT,
    status TEXT, summary TEXT, requested_at TEXT, finished_at TEXT
);
CREATE TABLE dispatch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, event_type TEXT NOT NULL,
    body TEXT DEFAULT '', created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES dispatch_runs(id) ON DELETE CASCADE
);
"""
#: Column names AND the foreign key are copied from the real schema deliberately. The FK is the
#: reason this code writes a synthetic run at all — a fixture without it would let the event insert
#: succeed on its own and make every test here agree with a system that does not exist.

NOW = "2026-08-15T12:00:00Z"

#: The claim and queue paths key on these. An audit row landing in any of them is the failure.
CLAIMABLE_STATUSES = {"queued", "claimed", "running"}


class SessionModeAuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA foreign_keys=ON")
        await self.db.executescript(SCHEMA)

    async def asyncTearDown(self):
        await self.db.close()

    async def _record(self, *, agent_id="a1", current_mode="resident", new_mode="managed",
                      runtime="hermes", requested_by="steven"):
        await _record_session_mode_switch_audit(
            self.db, agent_id, current_mode, new_mode, runtime, requested_by, NOW)

    async def _run(self):
        rows = await (await self.db.execute("SELECT * FROM dispatch_runs")).fetchall()
        self.assertEqual(1, len(rows), "exactly one anchor run per switch")
        return rows[0]

    async def _event(self):
        rows = await (await self.db.execute("SELECT * FROM dispatch_events")).fetchall()
        self.assertEqual(1, len(rows), "exactly one audit event per switch")
        return rows[0]

    async def test_the_anchor_run_is_born_COMPLETED(self):
        """A claimable audit row is a bridge executing an audit record as work."""
        await self._record()
        run = await self._run()
        self.assertEqual("completed", run["status"])
        self.assertNotIn(run["status"], CLAIMABLE_STATUSES)

    async def test_the_anchor_run_is_marked_AUDIT_in_both_mode_columns(self):
        """Either one alone would leave it looking like real work to whichever reader keys on the
        other."""
        await self._record()
        run = await self._run()
        self.assertEqual("audit", run["dispatch_mode"])
        self.assertEqual("audit", run["execution_mode"])
        self.assertEqual("audit", run["message_type"])

    async def test_the_anchor_run_is_finished_at_the_moment_it_is_created(self):
        """An unfinished completed run is the shape a reaper goes looking for."""
        await self._record()
        run = await self._run()
        self.assertEqual(NOW, run["requested_at"])
        self.assertEqual(NOW, run["finished_at"])

    async def test_the_event_hangs_off_the_run_that_was_just_written(self):
        """The FK is why the anchor exists; this proves the two actually join."""
        await self._record()
        run, event = await self._run(), await self._event()
        self.assertEqual(run["id"], event["run_id"])

    async def test_the_transition_is_readable_from_the_event_type(self):
        await self._record(current_mode="resident", new_mode="managed")
        self.assertEqual("mode_switch_resident_to_managed", (await self._event())["event_type"])

    async def test_the_body_names_the_agent_and_who_asked(self):
        """Both halves of "who did what to whom", which is what an audit trail is for."""
        await self._record(agent_id="mc-coder", requested_by="steven")
        body = (await self._event())["body"]
        self.assertIn("mc-coder", body)
        self.assertIn("steven", body)

    async def test_the_run_body_records_the_transition_as_well_as_the_event(self):
        """The run is what a timeline reader sees first; a bare id there would say nothing."""
        await self._record(agent_id="mc-coder", current_mode="managed", new_mode="resident",
                           requested_by="steven")
        run = await self._run()
        self.assertIn("mc-coder", run["body"])
        self.assertIn("managed->resident", run["body"])
        self.assertIn("steven", run["body"])
        self.assertEqual("session-mode-switch", run["subject"])

    async def test_the_run_is_attributed_from_the_requester_to_the_agent(self):
        await self._record(agent_id="mc-coder", requested_by="steven")
        run = await self._run()
        self.assertEqual("steven", run["from_agent"])
        self.assertEqual("mc-coder", run["target_agent"])

    async def test_two_switches_do_not_collide(self):
        """The id carries a millisecond stamp AND random hex; only the second half saves it here."""
        await self._record()
        await self._record(current_mode="managed", new_mode="resident")
        runs = await (await self.db.execute("SELECT * FROM dispatch_runs")).fetchall()
        self.assertEqual(2, len(runs))
        self.assertEqual(2, len({r["id"] for r in runs}), "the anchor ids must be unique")

    async def test_the_anchor_id_is_recognisable_as_an_audit_row(self):
        """An operator reading a raw table has to be able to tell this is not real work."""
        await self._record()
        self.assertTrue((await self._run())["id"].startswith("mode_switch_"))


if __name__ == "__main__":
    unittest.main()
