"""A resize wait polls for two integers, and until now it downloaded a whole console to get them.

WHAT IT COST, measured against the live fleet on 2026-09-08 with both controls in the same run:

    GET /terminals/{id}?cols&rows   p50 21.1ms   147,250 bytes   <- one poll
    GET /terminals?limit=50         p50  5.6ms     6,297 bytes   <- ALL NINE terminals
    GET /health                     p50  1.4ms       242 bytes   <- the control

The poll was fifteen times the control and four times the cost of listing every terminal on the
host. Of its 147KB, the output buffer is 110KB encoded and the event page 48KB; `waitForTerminalSize`
reads `cols` and `rows` and discards the rest. It runs up to THIRTY times at 100ms, and
`forceTerminalRepaint` calls it TWICE, so one console Refresh was bounded by sixty of them -- 8.8MB
and 1.3s of service time, on a service that must stay single-worker and is shared by every other
console on the host.

WHAT THIS FILE PINS, and why each part needs pinning:

  IT ANSWERS. A size endpoint that 404s or returns zeroes would send the resize wait round all
  thirty attempts and then throw, which is worse than the cost it was built to remove.

  IT REFUSES THE REST, with the heavy endpoint as the POSITIVE CONTROL in the same test. Asserting
  "no output key" proves nothing unless something proves the key exists to be absent -- an endpoint
  that returned `{}` would pass a bare absence check. So both are called and compared.

  IT DOES NOT FLUSH THE WRITE QUEUE, and that is a fact about ownership rather than an optimisation.
  `cols` and `rows` are written by the CONTROL COMPLETION path in `routers/terminal_controls.py` --
  the host reporting what its pty actually took -- committed directly. Nothing about them travels
  the output queue's lazy tail. This is pinned by driving the real control path and reading the size
  back with no output write anywhere in between: if a future change ever routes the size through the
  queue, this test goes red rather than the console hanging for three seconds in front of an
  operator.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.db import get_db
from service.tests._base import FastApiTestCase

BRIDGE = "bridge-size-poll"
ENVIRONMENT = "linux:size-poll-host:default"


class TheSizePollAsksOnlyForASizeTests(FastApiTestCase):
    AGENT = "size-poll-agent"
    TERMINAL = "term_size_poll"

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT, "machineId": "linux:size-poll-host", "os": "linux", "kind": "linux",
            "bridgeId": BRIDGE, "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self.client.post("/api/v1/agents", json={
            "agentId": self.AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:size-poll-host", "bridgeId": BRIDGE,
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        self._seed_terminal()

    def _seed_terminal(self) -> None:
        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "started_at, last_seen, spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"sess-{self.AGENT}", self.AGENT, ENVIRONMENT, "claude-code", "running",
                     "2026-09-08T02:00:00Z", "2026-09-08T02:00:00Z", None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, status, output, error, created_at, updated_at, "
                    "cols, rows) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.TERMINAL, self.AGENT, f"sess-{self.AGENT}", ENVIRONMENT, "claude-code",
                     BRIDGE, "claude-aify --aify-agent x", "attached", "", "",
                     "2026-09-08T02:00:00Z", "2026-09-08T02:00:00Z", 132, 26),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _size(self):
        return self.client.get(f"/api/v1/terminals/{self.TERMINAL}/size")

    def test_it_answers_with_the_size_the_resize_wait_compares(self):
        response = self._size()
        self.assertEqual(response.status_code, 200, response.text)
        terminal = response.json()["terminal"]
        # The two fields `waitForTerminalSize` reads, by the names it reads them under. A rename
        # here is a console that polls thirty times and throws.
        self.assertEqual(terminal["cols"], 132)
        self.assertEqual(terminal["rows"], 26)
        self.assertEqual(terminal["id"], self.TERMINAL)

    def test_an_unknown_terminal_is_a_404_rather_than_a_zero_size(self):
        # A zero-size answer would be indistinguishable from a pty that has not reported yet, and
        # the caller would poll for three seconds before failing with the wrong reason.
        response = self.client.get("/api/v1/terminals/term_does_not_exist/size")
        self.assertEqual(response.status_code, 404, response.text)

    def test_it_carries_no_output_no_snapshot_and_no_events(self):
        # THE POSITIVE CONTROL IS THE HEAVY ENDPOINT, in this same test. Asserting a key is absent
        # proves nothing on its own: an endpoint returning `{}` passes that. So the heavy response
        # is fetched first and asserted to CARRY what this one must not.
        # A BUFFER WORTH THE NAME. The first version of this test wrote one short line and then
        # asserted the light response was twenty times smaller -- a ratio taken from the live fleet
        # and applied to a fixture that had nothing in it. The ratio is only meaningful against a
        # console that has actually produced output, so the fixture produces some.
        self.client.post(f"/api/v1/terminals/{self.TERMINAL}/output", json={
            "bridgeId": BRIDGE, "output": "a screenful of bytes the size poll has no use for\n" * 400,
        })
        heavy = self.client.get(f"/api/v1/terminals/{self.TERMINAL}?cols=132&rows=26")
        self.assertEqual(heavy.status_code, 200, heavy.text)
        self.assertIn("output", heavy.json()["terminal"])
        self.assertIn("events", heavy.json())

        light = self._size()
        self.assertEqual(light.status_code, 200, light.text)
        self.assertNotIn("events", light.json())
        for absent in ("output", "snapshot"):
            self.assertNotIn(absent, light.json()["terminal"])
        # And it is smaller by the margin the change was made for, not merely smaller.
        self.assertLess(len(light.content) * 20, len(heavy.content))

    def test_the_size_arrives_without_any_output_write_to_flush(self):
        # THE OWNERSHIP FACT, driven rather than asserted: complete a resize control reporting a new
        # size, write NO output, and read the size back. It has to be current. If a future change
        # ever routes `cols`/`rows` through the output queue's lazy tail, this goes red here instead
        # of hanging a console in front of an operator.
        #
        # THE REPORTED SIZE DIFFERS FROM THE REQUESTED ONE, and that is the whole point of the pair.
        # Asking for 100x40 and reporting 100x40 -- as this test first did -- is satisfied by EITHER
        # writer, so deleting the one that records what the host actually took would have left it
        # green. 90x30 can only have come from the report.
        requested = self.client.post(f"/api/v1/terminals/{self.TERMINAL}/resize", json={
            "cols": 100, "rows": 40, "requestedBy": "test",
        })
        self.assertEqual(requested.status_code, 200, requested.text)
        control_id = requested.json()["control"]["id"]

        completed = self.client.patch(f"/api/v1/terminals/controls/{control_id}", json={
            "bridgeId": BRIDGE, "status": "completed", "cols": 90, "rows": 30,
        })
        self.assertEqual(completed.status_code, 200, completed.text)

        terminal = self._size().json()["terminal"]
        self.assertEqual((terminal["cols"], terminal["rows"]), (90, 30),
                         "the size must be the one the HOST reported, not the one the service asked "
                         "for -- those are different facts and only one of them is true")

    def test_the_size_endpoint_does_not_flush_the_write_queue(self):
        # OBSERVED, NOT ARGUED. "It does not flush" was a claim in a comment; a spy is what makes it
        # a fact. The heavy endpoint is the POSITIVE CONTROL in this same test, because a counter
        # that never increments proves nothing about the endpoint that is supposed to increment it.
        import service.routers.terminals as terminals_router

        calls = []
        original = terminals_router.TERMINAL_OUTPUT_WRITES.flush_terminal

        async def counting_flush(terminal_id):
            calls.append(terminal_id)
            return await original(terminal_id)

        terminals_router.TERMINAL_OUTPUT_WRITES.flush_terminal = counting_flush
        try:
            self.assertEqual(self._size().status_code, 200)
            self.assertEqual(calls, [], "the size endpoint flushed the write queue, which it has no "
                                        "reason to do -- these columns never travel that queue")

            heavy = self.client.get(f"/api/v1/terminals/{self.TERMINAL}?cols=132&rows=26")
            self.assertEqual(heavy.status_code, 200, heavy.text)
            self.assertEqual(calls, [self.TERMINAL],
                             "the heavy endpoint did NOT flush, so this spy cannot tell a flush from "
                             "the absence of one and the assertion above means nothing")
        finally:
            terminals_router.TERMINAL_OUTPUT_WRITES.flush_terminal = original
