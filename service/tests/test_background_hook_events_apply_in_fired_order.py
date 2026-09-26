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
HOST_A = "win32:host-a"
HOST_B = "win32:host-b"


class BackgroundHookEventsApplyInFiredOrderTests(FastApiTestCase):
    DB_NAME = "aify-hook-order.db"

    def setUp(self):
        super().setUp()
        self._register(HOST_A)

    def _register(self, machine: str, bridge: str = "hook-order-bridge"):
        response = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "launchMode": "detached", "sessionHandle": "hook-order-session", "machineId": machine,
            "bridgeId": bridge, "capabilities": ["resident-run"],
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _post(self, path: str, fired_at_us: int | None = None, machine: str = HOST_A, **body) -> dict:
        if fired_at_us is not None:
            body.update(firedAtUs=fired_at_us, machineId=machine)
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
        self._post("turn-start", 1000)
        self.assertEqual(self._busy(), 1, "precondition: the turn started")
        self._post("turn-end", 3000)
        self.assertEqual(self._busy(), 0, "precondition: the turn ended")
        self.assertEqual(self._post("turn-start", 2000).get("ignored"), "out_of_order_hook_event")
        self.assertEqual(self._busy(), 0)

    def test_a_turn_end_with_nothing_to_clear_still_orders_what_follows(self):
        self.assertEqual(self._post("turn-end", 3000).get("noop"), "already-cleared")
        self._post("turn-start", 2000)
        self.assertEqual(self._busy(), 0)

    def test_in_the_same_microsecond_the_turn_end_wins(self):
        """Review of 18c479ec: start, end and a delayed start all stamped alike left the turn working."""
        self._post("turn-start", 1000)
        self._post("turn-end", 1000)
        self.assertEqual(self._busy(), 0, "the turn-end must win a tie with the start before it")
        self._post("turn-start", 1000)
        self.assertEqual(self._busy(), 0, "a start must lose a tie with the end before it")

    def test_a_delayed_event_from_the_previous_host_is_refused_after_a_move(self):
        """Review of 18c479ec: the agent moves to a host whose clock is behind, and an event the old host
        fired late in its session arrives after the new session's turn ended."""
        self._post("turn-end", 4000, machine=HOST_A)
        self._register(HOST_B, bridge="hook-order-bridge-b")
        self._post("turn-start", 1000, machine=HOST_B)
        self.assertEqual(self._busy(), 1, "the new host's first event applies although its clock is behind")
        self._post("turn-end", 1500, machine=HOST_B)
        self.assertEqual(self._post("turn-start", 5000, machine=HOST_A).get("ignored"), "out_of_order_hook_event")
        self.assertEqual(self._busy(), 0)

    def test_the_host_is_compared_without_case(self):
        """Registration lowercases the id (models.py); a row stored before it did may not be."""
        db = sqlite3.connect(self._db_path)
        try:
            db.execute("UPDATE agents SET machine_id = ? WHERE id = ?", (HOST_A.upper(), AGENT))
            db.commit()
        finally:
            db.close()
        self._post("turn-start", 1000, machine=HOST_A)
        self.assertEqual(self._busy(), 1)

    def test_CONTROL_the_next_turns_start_applies(self):
        self._post("turn-end", 3000)
        self._post("turn-start", 4000)
        self.assertEqual(self._busy(), 1)

    def test_CONTROL_an_event_without_a_hook_time_applies_as_before(self):
        self._post("turn-end", 3000)
        self._post("turn-start")
        self.assertEqual(self._busy(), 1)

    def test_a_blocked_event_older_than_the_turns_resumption_is_refused(self):
        self._post("status-event", 1000, kind="blocked")
        self.assertEqual(self._awaiting_input(), 1, "precondition: blocked applies")
        self._post("status-event", 3000, kind="unblocked")
        self.assertEqual(self._awaiting_input(), 0, "precondition: unblocked applies")
        self.assertEqual(self._post("status-event", 2000, kind="blocked").get("applied"), False)
        self.assertEqual(self._awaiting_input(), 0)
