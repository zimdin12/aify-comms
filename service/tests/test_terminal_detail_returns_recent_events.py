"""The endpoint that exists to explain a terminal must show its RECENT events.

`GET /terminals/{id}` returns the terminal's event rows, and it selected them
`ORDER BY id ASC LIMIT 200` — the OLDEST two hundred. For any console busier than that, every recent
event is unreachable, including whatever it was doing when it died. The one endpoint whose job is to
explain a terminal answers about its first minutes and stops.

FOUND WHILE DIAGNOSING A LIVE INCIDENT, 2026-08-25. Two managed workers stopped in the same second and
the question was what happened to them. The terminal detail returned exactly 200 events — a cap hit
exactly is what truncation looks like from outside — and every one of them was `terminal_output`.

The fix is which 200, not how many: selected DESC and reversed, so the response stays chronological
and only its contents change. Callers that walk the list forward see the same shape.

NOT CHANGED HERE, and recorded rather than done: those 200 event rows are 48,116 bytes of a 133,878
byte response on the console's polled fetch — 36% of a hot payload that the console does not read (it
uses `terminal.snapshot`). Making them opt-in is a response-shape change that an existing regression
test pins, so it belongs to whoever can weigh breaking an API consumer.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase


class TerminalDetailRecentEventsTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    TERMINAL_ID = "term-busy"
    ENV_ID = "linux:test-host:default"
    #: Comfortably past the endpoint's 200-row cap, so the two orderings cannot agree.
    TOTAL_EVENTS = 260

    def _seed(self) -> None:
        import asyncio

        # Registered through the API rather than inserted: `agents` carries NOT NULL columns a
        # hand-built row keeps discovering one at a time, and registration is the only thing that
        # knows the full set.
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "busy-agent", "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": "linux:test-host",
                "bridgeId": "bridge-current", "capabilities": ["managed-run"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        # terminal_sessions.environment_id carries a foreign key, so the environment has to exist
        # before the terminal can. Registered through the heartbeat endpoint for the same reason the
        # agent is registered rather than inserted.
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV_ID, "machineId": "linux:test-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-current", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)

        async def _go():
            from service.db import get_db
            db = await get_db()
            try:
                # terminal_sessions.session_id references agent_sessions, so the session row has to
                # exist first. Its own spawn foreign keys are bound to NULL explicitly: their column
                # default is the empty string, which is not NULL, so SQLite enforces them and finds
                # no match.
                await db.execute(
                    """INSERT OR REPLACE INTO agent_sessions
                       (id, agent_id, environment_id, runtime, status, started_at, last_seen,
                        spawn_spec_id, spawn_request_id)
                       VALUES ('sess-busy', 'busy-agent', ?, 'claude-code', 'running', ?, ?,
                               NULL, NULL)""",
                    (self.ENV_ID, "2026-08-25T00:00:00Z", "2026-08-25T00:00:00Z"),
                )
                await db.execute(
                    # environment_id and runtime are NOT NULL on THIS table; spawn_spec_id belongs
                    # to agent_sessions, which is a different table with a similar name.
                    """INSERT OR REPLACE INTO terminal_sessions
                       (id, session_id, agent_id, environment_id, runtime, status,
                        created_at, updated_at)
                       VALUES (?, 'sess-busy', 'busy-agent', ?, 'claude-code',
                               'stopped', ?, ?)""",
                    (self.TERMINAL_ID, self.ENV_ID, "2026-08-25T00:00:00Z", "2026-08-25T00:00:00Z"),
                )
                for n in range(self.TOTAL_EVENTS):
                    # The LAST event is the interesting one — a lifecycle row among output noise, which
                    # is exactly the shape a death leaves behind.
                    kind = "terminal_exit" if n == self.TOTAL_EVENTS - 1 else "terminal_output"
                    await db.execute(
                        """INSERT INTO terminal_events (terminal_id, event_type, body, created_at)
                           VALUES (?, ?, ?, ?)""",
                        (self.TERMINAL_ID, kind, f"line-{n:04d}", "2026-08-25T00:00:00Z"),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_go())

    def _events(self) -> list[dict]:
        response = self.client.get(f"/api/v1/terminals/{self.TERMINAL_ID}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        # `events` sits at the TOP level of the response, beside `terminal`, not inside it.
        return body["events"] if "events" in body else body["terminal"]["events"]

    def test_the_fixture_exceeds_the_cap(self) -> None:
        """A control. With 200 or fewer events both orderings return the same rows and every
        assertion below passes whichever way the query is written."""
        self._seed()
        self.assertGreater(self.TOTAL_EVENTS, 200)
        self.assertEqual(len(self._events()), 200, "the endpoint's cap is not 200 any more")

    def test_the_most_recent_events_are_the_ones_returned(self) -> None:
        self._seed()
        bodies = [event["body"] for event in self._events()]
        self.assertIn(
            f"line-{self.TOTAL_EVENTS - 1:04d}", bodies,
            "the newest event is missing — the oldest 200 were returned, which is the defect",
        )
        self.assertNotIn(
            "line-0000", bodies,
            "the very first event came back, so the window is still anchored at the start",
        )

    def test_a_lifecycle_event_among_output_survives(self) -> None:
        """The reason this matters rather than a tidiness point: the row that explains a death is one
        lifecycle event at the end of a long tail of output."""
        self._seed()
        self.assertIn("terminal_exit", [event["eventType"] for event in self._events()])

    def test_the_response_is_still_chronological(self) -> None:
        """Selected DESC and reversed. A caller walking the list forward must still move forward in
        time, or this becomes a contract change instead of a fix."""
        self._seed()
        ids = [event["id"] for event in self._events()]
        self.assertEqual(ids, sorted(ids), "events came back newest-first")
