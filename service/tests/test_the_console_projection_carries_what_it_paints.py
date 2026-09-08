"""The console refetches a whole terminal to repaint from a fraction of it.

WHAT IT COSTS, measured against the live fleet 2026-09-08:

    GET /terminals/{id}?cols&rows   147,250 bytes on the wire, 21.1ms p50
      terminal.output                 93,430   63.4%  written only when there is NO snapshot
      events                          46,516   31.6%  read by nothing on this path
      terminal.snapshot                6,442    4.4%  the thing the console actually writes
      every other field + framing        862    0.6%  the size and sequence fields the caller reads

ONE RESPONSE, ONE ENCODER, AND CHECKED BY RE-ENCODING IT. The first version of these figures mixed a
different terminal's fields with this terminal's total and sized them with Python's default JSON
settings rather than the compact UTF-8 the server emits -- so they summed to 164,400 against a whole
of 147,250. They now come from one saved response and reproduce its wire size exactly.

Both console callers do `term.write(snapshot || output)`, so the tail is a FALLBACK and the event
page is not read at all. `view=console` answers what the caller is asking -- give me what I will
paint -- instead of shipping everything and letting it choose.

WHY THIS MATTERS BEYOND BYTES. The resync runs on every sequence GAP, which is the standing suspect
for the operator's intermittent console lag: one gap costs a full refetch and repaint, presenting as
"fine, then a stall, then fine". That hypothesis is unproven and this change does not prove it. What
it does is make the recovery cheap enough that the question changes.

THE FALLBACK IS THE PART THAT COULD HAVE BEEN SILENTLY WRONG, so it is the part with the most tests
here. pyte is optional and a dead terminal has its buffer forgotten, so `snapshot` is genuinely
absent sometimes -- and that is exactly when an operator is reading the console to find out why
something died. Dropping the tail unconditionally would blank that screen. The tail is dropped ONLY
when a snapshot exists to replace it.

A NAMED PROJECTION, NOT A BAG OF TOGGLES, and the DEFAULT IS UNTOUCHED. A caller that does not ask
for it gets exactly today's response, which is what makes this safe without first proving that no
other consumer anywhere reads the event page -- a universal negative this repo has been wrong about
before ("scoped searches give false ABSENCE").
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.db import get_db
from service.tests._base import FastApiTestCase

BRIDGE = "bridge-console-projection"
ENVIRONMENT = "linux:projection-host:default"


class TheConsoleProjectionCarriesWhatItPaintsTests(FastApiTestCase):
    AGENT = "projection-agent"
    TERMINAL = "term_projection"

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT, "machineId": "linux:projection-host", "os": "linux", "kind": "linux",
            "bridgeId": BRIDGE, "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self.client.post("/api/v1/agents", json={
            "agentId": self.AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:projection-host", "bridgeId": BRIDGE,
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

    def _write(self, text: str):
        return self.client.post(f"/api/v1/terminals/{self.TERMINAL}/output",
                                json={"bridgeId": BRIDGE, "output": text})

    def _get(self, view: str = "") -> dict:
        suffix = f"&view={view}" if view else ""
        response = self.client.get(
            f"/api/v1/terminals/{self.TERMINAL}?cols=132&rows=26{suffix}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_projection_carries_the_snapshot_and_drops_what_nothing_paints(self):
        # THE DEFAULT RESPONSE IS THE POSITIVE CONTROL, in this same test. Asserting a key is absent
        # proves nothing unless something proves it exists to be absent.
        self._write("a screen of output the console will not paint from\n" * 200)
        full = self._get()
        self.assertIn("events", full)
        self.assertIn("output", full["terminal"])
        self.assertTrue(full["terminal"].get("snapshot"),
                        "no snapshot was rendered, so this fixture cannot test the case where one "
                        "replaces the tail")

        console = self._get("console")
        self.assertNotIn("events", console)
        self.assertNotIn("output", console["terminal"])
        self.assertEqual(console["terminal"]["snapshot"], full["terminal"]["snapshot"],
                         "the projection must carry the SAME snapshot, not a differently rendered one")

    def test_the_size_fields_the_caller_reads_survive_the_projection(self):
        # `applyRenderedWidth` and the mount's own bookkeeping read these off the same payload. A
        # projection that dropped them would repaint at the wrong width, which is the garbling the
        # snapshot exists to prevent.
        self._write("something to render\n")
        console = self._get("console")["terminal"]
        for field in ("id", "cols", "rows", "renderedCols", "renderedRows", "outputSeq", "status"):
            self.assertIn(field, console, f"the console projection dropped `{field}`, which a caller reads")

    def test_the_raw_tail_SURVIVES_when_there_is_no_snapshot_to_replace_it(self):
        # THE FALLBACK, and the case that would have been silently wrong. Both callers write
        # `snapshot || output`; pyte is optional and a dead terminal's buffer is forgotten, so a
        # missing snapshot is real -- and it is exactly when an operator opens a console to find out
        # why something died. Dropping the tail there would blank that screen.
        import service.api_core.terminal_snapshot_view as snapshot_view

        original = snapshot_view._attach_terminal_snapshot

        async def no_snapshot(term_dict, cols, rows):
            term_dict["snapshot"] = ""

        # Patched where the ROUTE looks it up, not only where it is defined: the router imported the
        # name at module load, so rebinding the source module alone would leave the route calling the
        # original and this test passing for the wrong reason.
        import service.routers.terminals as terminals_router
        route_original = terminals_router._attach_terminal_snapshot
        terminals_router._attach_terminal_snapshot = no_snapshot
        snapshot_view._attach_terminal_snapshot = no_snapshot
        try:
            self._write("the last thing a dying worker printed\n")
            console = self._get("console")["terminal"]
            self.assertFalse(console.get("snapshot"))
            self.assertIn("output", console,
                          "with no snapshot the console has nothing else to paint, and the tail was "
                          "dropped anyway")
            self.assertIn("the last thing a dying worker printed", console["output"])
        finally:
            terminals_router._attach_terminal_snapshot = route_original
            snapshot_view._attach_terminal_snapshot = original

    def test_an_unrecognised_view_is_the_full_response_rather_than_an_error(self):
        # A projection nobody declared must not be a 500 or an empty body. An older client, a typo or
        # a hand-made request gets what it would have got before the parameter existed.
        self._write("output\n")
        response = self.client.get(f"/api/v1/terminals/{self.TERMINAL}?cols=132&rows=26&view=wat")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("events", response.json())
        self.assertIn("output", response.json()["terminal"])

    def test_the_projection_is_smaller_by_the_margin_it_was_made_for(self):
        self._write("a screenful of bytes the console does not paint from\n" * 400)
        full = self.client.get(f"/api/v1/terminals/{self.TERMINAL}?cols=132&rows=26")
        console = self.client.get(f"/api/v1/terminals/{self.TERMINAL}?cols=132&rows=26&view=console")
        self.assertLess(len(console.content) * 5, len(full.content),
                        f"the projection is {len(console.content)} bytes against {len(full.content)}, "
                        f"which is not the reduction this change exists for")
