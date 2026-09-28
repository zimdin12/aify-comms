"""A claim long-poll whose caller has gone stops waiting and claims nothing, through the production app.

External review of 0.7.6 (O1, open since 2026-09-26). Every claim route passed
`request.is_disconnected` to `longpoll()`, and two passed nothing. The app is wrapped in
`BaseHTTPMiddleware` subclasses, under which `is_disconnected()` always answers False (the v0.7.1 S2
finding for `/listen`), so a bridge that timed out or died mid-poll stayed parked for the whole budget,
and work that arrived meanwhile was CLAIMED for it: an interrupt marked `claimed` that no bridge holds,
a run held by a sidecar that never received it.

These tests go through `service.main.app`, middleware included, as raw ASGI calls whose `receive`
hands the route an `http.disconnect`, and each asserts the route answered 200, so a refused request
cannot pass as a result.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

from service import longpoll
from service.tests._base import FastApiTestCase

TARGET = "lc-target"
REQUESTER = "lc-operator"
MACHINE = "linux:box"


async def _claim(body: dict, *, disconnect_after: float | None):
    from service.main import app

    delivered = {"request": False}

    async def receive():
        if not delivered["request"]:
            delivered["request"] = True
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        await asyncio.sleep(3600 if disconnect_after is None else disconnect_after)
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    route = "/api/v1/dispatch/controls/claim"
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": route, "raw_path": route.encode(), "query_string": b"",
        # A trusted Host: the cross-site middleware refuses an untrusted one with a 403 before the route runs.
        "headers": [(b"host", b"127.0.0.1:8800"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "root_path": "",
    }
    await app(scope, receive, send)
    statuses = [m.get("status") for m in sent if m["type"] == "http.response.start"]
    assert statuses == [200], f"the route did not answer 200, so nothing below was tested: {sent[:2]}"
    return json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))


class AClaimLongPollSeesACallerThatLeftTests(FastApiTestCase):
    DB_NAME = "aify-claim-disconnect.db"

    def setUp(self):
        super().setUp()
        for agent_id in (TARGET, REQUESTER):
            response = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder", "machineId": MACHINE})
            self.assertEqual(response.status_code, 200, response.text)
        with sqlite3.connect(self._db_path) as db:
            db.execute("INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at) "
                       "VALUES ('run-1', ?, ?, 'running', '2026-09-29T00:00:00Z')", (REQUESTER, TARGET))
        db.close()

    def _seed_control(self):
        with sqlite3.connect(self._db_path) as db:
            db.execute("INSERT INTO dispatch_controls (id, run_id, from_agent, action, body, status, requested_at) "
                       "VALUES ('ctl-1', 'run-1', ?, 'interrupt', 'stop', 'pending', '2026-09-29T00:00:00Z')",
                       (REQUESTER,))
        db.close()
        longpoll.notify("control")

    def _control_status(self):
        db = sqlite3.connect(self._db_path)
        try:
            return db.execute("SELECT status FROM dispatch_controls WHERE id = 'ctl-1'").fetchone()[0]
        finally:
            db.close()

    def _scenario(self, disconnect_after):
        """Park a 5 s claim, let the caller leave (or not), then make an interrupt claimable."""
        async def run():
            call = asyncio.create_task(_claim({"agentId": TARGET, "waitMs": 5000}, disconnect_after=disconnect_after))
            await asyncio.sleep(0.5)
            finished_early = call.done()
            self._seed_control()
            answer = await asyncio.wait_for(call, 10)
            return finished_early, answer
        return asyncio.run(run())

    def test_control_a_connected_caller_that_waits_is_handed_the_interrupt(self):
        finished_early, answer = self._scenario(disconnect_after=None)
        self.assertFalse(finished_early, "control: a connected caller was not parked")
        self.assertEqual([c["id"] for c in answer["controls"]], ["ctl-1"])
        self.assertEqual(self._control_status(), "claimed")

    def test_a_caller_that_leaves_while_parked_ends_the_wait_and_claims_nothing(self):
        finished_early, answer = self._scenario(disconnect_after=0.1)
        self.assertTrue(finished_early, "the wait did not end when the caller left")
        self.assertEqual(answer["controls"], [])
        self.assertEqual(self._control_status(), "pending", "an interrupt was claimed for a caller that had left")
