"""Background runtime hooks can arrive out of order; the service applies them in the order they fired.

The turn hooks run in the background so a prompt never waits on them (api_core/hook_event_order.py). A
turn-start slowed by a loaded host can then land after its own turn's turn-end, and applying it would
leave the agent `working` with nothing left to end the turn. Driven through the real endpoints.
"""

from __future__ import annotations

import sqlite3

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "hook-order-agent"


class BackgroundHookEventsApplyInFiredOrderTests(FastApiTestCase):
    DB_NAME = "aify-hook-order.db"

    def setUp(self):
        super().setUp()
        self._register()

    def _register(self):
        response = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "launchMode": "detached", "sessionHandle": "hook-order-session", "machineId": "win32:test-host",
            "bridgeId": "hook-order-bridge", "capabilities": ["resident-run"],
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _post(self, path: str, body: dict) -> dict:
        response = self.client.post(f"/api/v1/agents/{AGENT}/{path}", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _row(self, sql: str):
        db = sqlite3.connect(self._db_path)
        try:
            db.row_factory = sqlite3.Row
            return db.execute(sql, (AGENT,)).fetchone()
        finally:
            db.close()

    def _busy(self) -> int:
        row = self._row("SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?")
        return int(row["turn_busy"]) if row else 0

    def _awaiting_input(self) -> int:
        row = self._row("SELECT awaiting_input FROM agent_status_state WHERE agent_id = ?")
        return int(row["awaiting_input"]) if row else 0

    def test_a_turn_start_arriving_after_its_turns_end_is_refused(self):
        self._post("turn-start", {"at": 1000})
        self.assertEqual(self._busy(), 1, "precondition: the turn started")
        self._post("turn-end", {"at": 3000})
        self.assertEqual(self._busy(), 0, "precondition: the turn ended")
        self.assertEqual(self._post("turn-start", {"at": 2000}).get("ignored"), "older_than_last_hook_event")
        self.assertEqual(self._busy(), 0)

    def test_a_turn_end_with_nothing_to_clear_still_orders_what_follows(self):
        self.assertEqual(self._post("turn-end", {"at": 3000}).get("noop"), "already-cleared")
        self._post("turn-start", {"at": 2000})
        self.assertEqual(self._busy(), 0)

    def test_CONTROL_the_next_turns_start_applies(self):
        self._post("turn-end", {"at": 3000})
        self._post("turn-start", {"at": 4000})
        self.assertEqual(self._busy(), 1)

    def test_CONTROL_an_event_without_a_hook_time_applies_as_before(self):
        self._post("turn-end", {"at": 3000})
        self._post("turn-start", {})
        self.assertEqual(self._busy(), 1)

    def test_CONTROL_registration_forgets_the_previous_hosts_clock(self):
        self._post("turn-end", {"at": 5000})
        self._register()
        self._post("turn-start", {"at": 1000})
        self.assertEqual(self._busy(), 1)

    def test_a_blocked_event_older_than_the_turns_resumption_is_refused(self):
        self._post("status-event", {"kind": "blocked", "at": 1000})
        self.assertEqual(self._awaiting_input(), 1, "precondition: blocked applies")
        self._post("status-event", {"kind": "unblocked", "at": 3000})
        self.assertEqual(self._awaiting_input(), 0, "precondition: unblocked applies")
        self.assertEqual(self._post("status-event", {"kind": "blocked", "at": 2000}).get("applied"), False)
        self.assertEqual(self._awaiting_input(), 0)
