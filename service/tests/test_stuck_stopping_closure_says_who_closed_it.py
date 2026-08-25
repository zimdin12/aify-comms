"""The one path that can stop several terminals at once must say that it did.

CENSUS, 2026-08-25: eleven functions in the service move a terminal to `stopped` or `failed`. Ten
append a terminal event naming what happened. `_reconcile_stuck_terminal_and_session_rows` was the
one that did not — and it is the only one that closes terminals with a SET-BASED update, so a single
statement can close many of them, every one stamped with the same `stopped_at`, recording nothing but
a count in the reconcile summary.

WHY THAT COMBINATION IS THE WORST ONE TO LEAVE SILENT. An operator reported two managed workers
stopping in the same second and asked what happened. Every terminal-level record for those two was
`terminal_output`; nothing said who closed them. A batch closer that leaves no trace is precisely the
shape that produces an unanswerable question, and it was the only batch closer there is.

The fix reads the affected ids before the update instead of relying on the predicate alone, so each
closure can carry a reason and an event. Two extra statements per sweep, and only when the sweep has
something to close.

Deliberately NOT changed: the grace window, the predicate, or which rows are closed. This adds a
record of a decision that was already being made.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

ENV_ID = "linux:test-host:default"


class StuckStoppingClosureTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    #: Two terminals, so the batch behaviour is what is under test rather than a single row.
    TERMINALS = ("term-stuck-a", "term-stuck-b")
    #: Comfortably past STUCK_STOPPING_GRACE_SECONDS.
    LONG_AGO = "2026-01-01T00:00:00Z"

    def setUp(self) -> None:
        super().setUp()
        for agent_id in ("stuck-agent-a", "stuck-agent-b"):
            response = self.client.post(
                "/api/v1/agents",
                json={
                    "agentId": agent_id, "role": "coder", "runtime": "claude-code",
                    "sessionMode": "managed", "machineId": "linux:test-host",
                    "bridgeId": "bridge-current", "capabilities": ["managed-run"],
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV_ID, "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": "bridge-current", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        self._seed_stuck_terminals()

    def _seed_stuck_terminals(self) -> None:
        async def _go():
            from service.db import get_db
            db = await get_db()
            try:
                for terminal_id, agent_id in zip(self.TERMINALS, ("stuck-agent-a", "stuck-agent-b")):
                    session_id = f"sess-{terminal_id}"
                    # spawn_spec_id / spawn_request_id bound to NULL: their column default is the
                    # empty string, which is not NULL, so the foreign key is enforced and finds nothing.
                    await db.execute(
                        """INSERT OR REPLACE INTO agent_sessions
                           (id, agent_id, environment_id, runtime, status, started_at, last_seen,
                            spawn_spec_id, spawn_request_id)
                           VALUES (?, ?, ?, 'claude-code', 'running', ?, ?, NULL, NULL)""",
                        (session_id, agent_id, ENV_ID, self.LONG_AGO, self.LONG_AGO),
                    )
                    await db.execute(
                        """INSERT OR REPLACE INTO terminal_sessions
                           (id, session_id, agent_id, environment_id, runtime, status,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, 'claude-code', 'stopping', ?, ?)""",
                        (terminal_id, session_id, agent_id, ENV_ID, self.LONG_AGO, self.LONG_AGO),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_go())

    def _reconcile(self) -> dict:
        async def _go():
            from service.db import get_db
            from service.reconcilers.terminal_runs import _reconcile_stuck_terminal_and_session_rows
            db = await get_db()
            try:
                result = await _reconcile_stuck_terminal_and_session_rows(db)
                await db.commit()
                return result
            finally:
                await db.close()

        return asyncio.run(_go())

    def _events(self, terminal_id: str) -> list[dict]:
        response = self.client.get(f"/api/v1/terminals/{terminal_id}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return body["events"] if "events" in body else body["terminal"]["events"]

    def _terminal(self, terminal_id: str) -> dict:
        return self.client.get(f"/api/v1/terminals/{terminal_id}").json()["terminal"]

    def test_the_fixture_is_actually_stuck(self) -> None:
        """A control. If the rows were not in `stopping` past the grace window the sweep would close
        nothing and every assertion below would hold for the wrong reason."""
        for terminal_id in self.TERMINALS:
            self.assertEqual(self._terminal(terminal_id)["status"], "stopping")
        self.assertEqual(self._reconcile()["stuck_stopping_terminals_closed"], len(self.TERMINALS))

    def test_both_terminals_are_closed(self) -> None:
        """The behaviour must not change — this adds a record, it does not change which rows close."""
        self._reconcile()
        for terminal_id in self.TERMINALS:
            self.assertEqual(self._terminal(terminal_id)["status"], "stopped")

    def test_each_closure_records_an_event_naming_the_reconciler(self) -> None:
        self._reconcile()
        for terminal_id in self.TERMINALS:
            kinds = [event["eventType"] for event in self._events(terminal_id)]
            self.assertIn(
                "terminal_stuck_stopping_closed", kinds,
                f"{terminal_id} was closed with no record of who closed it — the whole point",
            )

    def test_the_recorded_reason_explains_itself(self) -> None:
        """A bare event type sends the reader back to the source. The reason names the condition and
        the window, so the row is readable without opening the reconciler."""
        self._reconcile()
        event = next(
            e for e in self._events(self.TERMINALS[0])
            if e["eventType"] == "terminal_stuck_stopping_closed"
        )
        reason = json.loads(event["body"])["reason"]
        self.assertIn("stuck-stopping reconciler", reason)
        self.assertIn("never confirmed", reason)

    def test_the_terminal_itself_carries_the_reason(self) -> None:
        """Not everyone reads events. The `error` column is what the terminal detail shows, and it was
        empty for a row this sweep closed."""
        self._reconcile()
        self.assertIn("stuck-stopping reconciler", self._terminal(self.TERMINALS[0])["error"])
