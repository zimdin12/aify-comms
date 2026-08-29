r"""The session's mirrored terminal status advances for every live status the vocabulary declares.

THE DEFECT. `terminal_write_queue._write_terminal_output` mirrors a live terminal's status onto
`agent_sessions.terminal_status`, and it decided which statuses count as live with a hand-typed set::

    {"attached", "running", "live", "idle", "starting", "stopping"}

The vocabulary's own "not yet gone" set is `TERMINAL_LIVE_FILTER_STATUSES | {"stopping"}`::

    {"starting", "attached", "running", "active", "idle", "recovering", "stopping"}

So the copy was missing `active` and `recovering`, and carried `live`, which is not a terminal status
at all -- `TERMINAL_SESSION_STATUSES` does not contain it and `_terminal_status_transition` refuses
anything outside that set, so that member could never match.

WHAT IT COST is in the branch's own comment: without the mirror, "agent_sessions.terminal_status
stays 'starting' forever and the engine reports a permanent transitioning 'working' even for an idle
console". Latent rather than live -- the bridge sends `attached` and `failed` with output -- but the
column it leaves stale is one the status engine reads.

WHY IT SURVIVED. `api_core/terminal_status.py` records a sweep that unified sixteen
`WHERE status IN (...)` filters across eleven modules. This one is a PYTHON set membership, so a
sweep looking for `WHERE status IN` never reached it. The set now has a NAME
(`TERMINAL_STOPPABLE_STATUSES`) and the SQL fragment is derived from it, which is the direction that
keeps the two forms from disagreeing.

THIS TEST DRIVES THE QUEUE, not the constant. A test that compared two constants would pass while the
queue kept its own list -- the same shape as a green helper suite over a disconnected call site.
"""
from __future__ import annotations

import asyncio
import unittest

from service.api_core.terminal_status import (
    TERMINAL_SESSION_STATUSES,
    TERMINAL_STOPPABLE_STATUSES,
)
from service.db import get_db
from service.tests._base import FastApiTestCase

AGENT = "mirror-probe"
SESSION = "sess-mirror-probe"
TERMINAL = "term-mirror-probe"
ENVIRONMENT = "env-mirror-probe"


class TheTerminalMirrorReadsTheVocabularyTests(FastApiTestCase):
    def _write(self, query: str, params: tuple = ()) -> None:
        async def run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(run())

    def _read_mirror(self) -> str:
        async def run():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT terminal_status FROM agent_sessions WHERE id = ?", (SESSION,)
                )).fetchone()
                return str((row["terminal_status"] if row else "") or "")
            finally:
                await db.close()

        return asyncio.run(run())

    def _seed(self) -> None:
        # THE ENVIRONMENT FIRST. `agent_sessions.environment_id` is NOT NULL *and* carries a foreign
        # key, so neither NULL nor '' will do -- the same trap `spawn_spec_id` sets with its ''
        # default. Registered through the real heartbeat endpoint rather than inserted, so the row
        # is whatever the service actually writes.
        env = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT, "label": ENVIRONMENT, "machineId": "probe-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-mirror", "cwdRoots": ["/w"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"]}],
        })
        self.assertEqual(env.status_code, 200, env.text)
        response = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "launchMode": "detached", "machineId": "probe-host",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self._write("DELETE FROM agent_sessions WHERE id = ?", (SESSION,))
        self._write(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode,"
            " status, started_at, last_seen, terminal_id, terminal_status, spawn_spec_id,"
            " spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (SESSION, AGENT, ENVIRONMENT, "claude-code", "/w", "managed-warm", "running",
             "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z", TERMINAL, "starting", None, None),
        )
        self._write("DELETE FROM terminal_sessions WHERE id = ?", (TERMINAL,))
        self._write(
            # Every NOT NULL column with no default, named: session_id, agent_id, environment_id,
            # runtime, created_at, updated_at. Read from the schema rather than discovered one
            # IntegrityError at a time.
            "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime,"
            " status, created_at, updated_at, output_seq) VALUES (?,?,?,?,?,?,?,?,?)",
            (TERMINAL, SESSION, AGENT, ENVIRONMENT, "claude-code", "starting",
             "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z", 0),
        )

    def _post_output(self, status: str) -> None:
        from service.terminal_write_queue import TerminalOutputWriteQueue

        async def run():
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE agent_sessions SET terminal_status = 'starting' WHERE id = ?", (SESSION,)
                )
                await db.commit()
            finally:
                await db.close()
            # The queue's own writer, called directly: batching and the websocket fan-out are not
            # what this proves, and a queue with no ws_manager broadcasts nothing.
            queue = TerminalOutputWriteQueue()
            await queue._write_terminal_output(TERMINAL, "x", status=status, seq=1)

        asyncio.run(run())

    def test_the_vocabulary_is_what_this_test_thinks_it_is(self) -> None:
        """The control. If the set were empty the loop below would assert nothing."""
        self.assertGreaterEqual(len(TERMINAL_STOPPABLE_STATUSES), 6)
        self.assertIn("active", TERMINAL_STOPPABLE_STATUSES, "the missing member is missing again")
        self.assertIn("recovering", TERMINAL_STOPPABLE_STATUSES, "the other missing member")
        self.assertNotIn("live", TERMINAL_SESSION_STATUSES,
                         "'live' is not a terminal status; the old hand-typed set carried it")

    def test_every_live_status_advances_the_mirror(self) -> None:
        """The behaviour, driven through the queue for each member of the declared set."""
        self._seed()
        for status in sorted(TERMINAL_STOPPABLE_STATUSES):
            with self.subTest(status=status):
                self._post_output(status)
                self.assertEqual(
                    self._read_mirror(), status,
                    f"a terminal reporting {status!r} left the session's mirrored status stale, "
                    "which is what makes the engine read a permanent transitioning 'working'",
                )

    def test_a_status_outside_the_vocabulary_does_not_advance_it(self) -> None:
        """The negative control, and the member the old set carried. `live` is not a status, so it
        must not mirror -- otherwise this test would pass on a branch that matched anything."""
        self._seed()
        self._post_output("live")
        self.assertEqual(self._read_mirror(), "starting",
                         "a value outside the vocabulary was mirrored onto the session")


if __name__ == "__main__":
    unittest.main()
