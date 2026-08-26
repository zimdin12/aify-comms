"""A terminal control writes the status its own SQL compares against.

FOUND BY HUNTING A CLASS, not by tripping over it. `update_dispatch_run` had just been fixed for
writing a raw status while every reader assumed lowercase, so the shape got a name -- a value
normalised for a comparison and then used raw for the write -- and the service tree was searched for
it with the AST. Two candidates survived; one was a self-reassignment, and this is the other.

`_apply_terminal_status_from_control` computes `terminal_status_norm` and uses it for exactly one
thing: the `_TERMINAL_END_STATUSES` membership check that closes active runs. The two UPDATEs beneath
it bind the UNNORMALISED `terminal_status` four times, and both statements compare that same
parameter against lowercase literals:

    UPDATE terminal_sessions
       SET status = ?,                                              <- written raw
           stopped_at = CASE WHEN ? IN ('stopped','failed') ...     <- compared raw
    UPDATE agent_sessions
       SET terminal_status = ?,                                     <- written raw
           owner_mode = CASE WHEN ? IN ('stopped','failed') ...     <- compared raw

So a `terminalStatus` of "Stopped" is stored verbatim, never stamps `stopped_at`, never returns
`owner_mode` to 'managed' -- leaving the session owned by a console that has gone -- and then matches
no reaper, because every one selects on the lowercase members. Four consequences from one missing
`.lower()`, against one for the dispatch run.

THIS PATH ALSO BYPASSES THE ALLOWLIST, which is recorded and NOT fixed here.
`_terminal_status_transition` refuses any status outside `TERMINAL_SESSION_STATUSES` and returns the
normalised value; it was added on 2026-08-16 precisely so an unrecognised status could not reach the
column. These two UPDATEs do not go through it. Routing them through would also start REFUSING writes
the monotonic guard rejects, which is a live behaviour change on a control path -- a decision, not a
repair. Normalising is the part that is provable without changing which statuses are accepted.

NO LIVE DEFECT TODAY, measured rather than assumed: across `mcp/stdio` with `tests/` and `fixtures/`
pruned, the bridge sends four `terminalStatus` literals -- attached, failed, running, stopped -- and
all four are lowercase. Worth fixing anyway because the normalised twin was already sitting there
unused, which is the author expecting case to vary and then not applying it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

BRIDGE = "bridge-ctl-probe"
ENVIRONMENT = "linux:test-host:default"


class ControlWritesComparableStatusTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    AGENT = "ctl-status-agent"
    TERMINAL = "term_ctl_status_probe"
    SESSION = "sess_ctl_status_probe"

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT, "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": BRIDGE, "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self.client.post("/api/v1/agents", json={
            "agentId": self.AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:test-host", "bridgeId": BRIDGE,
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        self._seed()

    def _seed(self) -> None:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                # spawn_spec_id / spawn_request_id named and NULLed: both are nullable FOREIGN KEYs
                # whose column DEFAULT is '', which no spawn row has, so omitting them fails the
                # constraint with no column named in the error.
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "owner_mode, terminal_id, terminal_status, started_at, last_seen, "
                    "spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.SESSION, self.AGENT, ENVIRONMENT, "claude-code", "running", "console",
                     self.TERMINAL, "attached", "2026-08-26T02:00:00Z", "2026-08-26T02:00:00Z",
                     None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, status, output, error, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.TERMINAL, self.AGENT, self.SESSION, ENVIRONMENT, "claude-code", BRIDGE,
                     "claude-aify --aify-agent x", "attached", "", "",
                     "2026-08-26T02:00:00Z", "2026-08-26T02:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    #: `terminal_controls.id` is a TEXT primary key, not an autoincrementing rowid, so the id is
    #: supplied rather than read back from `lastrowid` -- which returns a number no row is keyed by
    #: and produced a 404 that looked like a routing problem.
    _seq = 0

    def _control_id(self, action: str = "stop") -> str:
        import asyncio

        from service.db import get_db

        type(self)._seq += 1
        control_id = f"ctl-{type(self)._seq:03d}"

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO terminal_controls (id, terminal_id, environment_id, bridge_id, "
                    "action, status, requested_by, requested_at) VALUES (?,?,?,?,?,?,?,?)",
                    (control_id, self.TERMINAL, ENVIRONMENT, BRIDGE, action, "pending", "tester",
                     "2026-08-26T02:01:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())
        return control_id

    def _rows(self) -> dict:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                terminal = await (await db.execute(
                    "SELECT status, stopped_at FROM terminal_sessions WHERE id = ?", (self.TERMINAL,),
                )).fetchone()
                session = await (await db.execute(
                    "SELECT terminal_status, owner_mode FROM agent_sessions WHERE id = ?", (self.SESSION,),
                )).fetchone()
                return {
                    "terminal_status": terminal["status"],
                    "stopped_at": terminal["stopped_at"],
                    "session_terminal_status": session["terminal_status"],
                    "owner_mode": session["owner_mode"],
                }
            finally:
                await db.close()

        return asyncio.run(go())

    def _report(self, terminal_status: str, status: str = "completed"):
        return self.client.patch(
            f"/api/v1/terminals/controls/{self._control_id()}",
            json={"status": status, "terminalStatus": terminal_status, "handledBy": BRIDGE,
                  "machineId": "linux:test-host"},
        )

    def test_the_fixture_starts_attached_and_console_owned(self) -> None:
        """Positive control. Every assertion below is about a CHANGE, and a fixture already in the
        target state would satisfy them without the code doing anything."""
        rows = self._rows()
        self.assertEqual(rows["terminal_status"], "attached")
        self.assertEqual(rows["owner_mode"], "console")
        self.assertFalse(rows["stopped_at"])

    def test_a_lowercase_stop_behaves_exactly_as_before(self) -> None:
        """No regression for every value the bridge actually sends."""
        self.assertEqual(self._report("stopped").status_code, 200)
        rows = self._rows()
        self.assertEqual(rows["terminal_status"], "stopped")
        self.assertTrue(rows["stopped_at"])
        self.assertEqual(rows["owner_mode"], "managed")

    def test_a_mixed_case_stop_is_stored_as_the_readers_expect(self) -> None:
        self.assertEqual(self._report("Stopped").status_code, 200)
        self.assertEqual(
            self._rows()["terminal_status"], "stopped",
            "a mixed-case terminal status was stored verbatim, so every reaper's `status IN (...)` "
            "misses this row",
        )

    def test_a_mixed_case_stop_still_stamps_stopped_at(self) -> None:
        """The second consequence: the CASE compares the same parameter against lowercase literals,
        so a verbatim value leaves the row with no stop time to age it by."""
        self.assertEqual(self._report("Stopped").status_code, 200)
        self.assertTrue(self._rows()["stopped_at"], "stopped_at was never stamped")

    def test_a_mixed_case_stop_still_returns_the_session_to_managed(self) -> None:
        """The fourth consequence, and the one with a live cost: owner_mode only returns to 'managed'
        when that CASE matches, so a mixed-case stop leaves the session owned by a console that has
        gone."""
        self.assertEqual(self._report("Stopped").status_code, 200)
        rows = self._rows()
        self.assertEqual(rows["session_terminal_status"], "stopped")
        self.assertEqual(rows["owner_mode"], "managed", "the session stayed owned by a dead console")

    def test_surrounding_whitespace_does_not_survive_either(self) -> None:
        """Held BEFORE this change too -- `terminal_status` was always `.strip()`ed, only never
        lowered -- so this one does not discriminate the fix and is recorded as a property rather
        than a regression test. It guards the strip that the lowering now sits beside."""
        self.assertEqual(self._report("  stopped  ").status_code, 200)
        self.assertEqual(self._rows()["terminal_status"], "stopped")


if __name__ == "__main__":
    import unittest

    unittest.main()
