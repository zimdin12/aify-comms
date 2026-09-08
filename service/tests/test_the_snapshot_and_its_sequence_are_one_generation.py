"""A terminal GET hands the browser a screen and a number describing it. They must be the same screen.

FOUND BY THE WHOLE-DIFF REVIEW, 2026-09-08, and constructed rather than caught in the wild.
`get_terminal` does three things in this order:

    term_dict = _terminal_session_to_dict(terminal)   # reads outputSeq from the live buffer
    ... await db.execute("SELECT role FROM agents ...")   # yields the event loop
    await _attach_terminal_snapshot(term_dict, cols, rows)   # renders the CURRENT live screen

Output appended during that await is IN the rendered screen and NOT in the number beside it. So the
response seeds a browser with a picture that already contains bytes the browser is about to be sent
again -- and `xterm-mount.mjs` seeds `lastSeq` from exactly this field, so the retransmission passes
the `seq <= lastSeq` filter and is written a second time. For a TUI that is not a duplicated line,
it is a cursor movement nobody asked for, which is the corruption class the sequence exists to stop.

AND IT HIDES ITSELF. The next quiescent GET returns the HIGHER sequence with the identical snapshot,
so anyone checking afterwards sees a consistent pair.

WHAT THIS TEST IS NOT. It is not an attribution of the operator's intermittent console lag, and it
is not a live observation -- the interleaving is injected at the existing await with the real
producer. It is a correctness property of the response.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.api_core.terminal_output import _append_terminal_output
from service.api_core.terminal_tail_buffer import current_seq
from service.db import get_db
from service.routers import terminals as terminals_router
from service.terminal_snapshot import render_live_screen
from service.tests._base import FastApiTestCase

BRIDGE = "bridge-one-generation"
ENVIRONMENT = "linux:one-generation-host:default"
INJECTED = "NEW_GENERATION_ARRIVED_DURING_THE_AWAIT"
ESC = chr(27)

#: A LIVE SCREEN ONLY EXISTS FOR A PAINTING PROCESS, and that is the whole reason this fixture
#: writes escapes. `feed_live_screen` refuses to create a screen for a chunk with no ESC in it --
#: "plain logs must remain byte-for-byte logs" -- so a fixture of plain lines takes the REPLAY path,
#: where the snapshot is rendered from a tail captured before the await and is already coherent with
#: its sequence. The first version of this file wrote plain text and tested the branch it does not
#: fix, which its own positive control caught.
PAINT = ESC + "[2J" + ESC + "[H"


class TheSnapshotAndItsSequenceAreOneGenerationTests(FastApiTestCase):
    AGENT = "one-generation-agent"
    TERMINAL = "term_one_generation"
    #: A terminal that never paints, so `feed_live_screen` refuses it a screen and the GET
    #: takes the REPLAY branch. `_LIVE_SCREENS` is process-global and outlives a test, so the
    #: painting terminal above cannot be reused to test the branch that has no live screen.
    PLAIN_TERMINAL = "term_one_generation_plain"

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT, "machineId": "linux:one-generation-host", "os": "linux",
            "kind": "linux", "bridgeId": BRIDGE, "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self.client.post("/api/v1/agents", json={
            "agentId": self.AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:one-generation-host", "bridgeId": BRIDGE,
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        self._seed_terminal(self.TERMINAL)
        self._seed_terminal(self.PLAIN_TERMINAL)

    def _seed_terminal(self, terminal_id: str) -> None:
        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO agent_sessions (id, agent_id, environment_id, runtime, "
                    "status, started_at, last_seen, spawn_spec_id, spawn_request_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"sess-{self.AGENT}", self.AGENT, ENVIRONMENT, "claude-code", "running",
                     "2026-09-08T02:00:00Z", "2026-09-08T02:00:00Z", None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, status, output, error, created_at, updated_at, "
                    "cols, rows) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (terminal_id, self.AGENT, f"sess-{self.AGENT}", ENVIRONMENT, "claude-code",
                     BRIDGE, "claude-aify --aify-agent x", "attached", "", "",
                     "2026-09-08T02:00:00Z", "2026-09-08T02:00:00Z", 132, 26),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _write(self, text: str, terminal_id: str = ""):
        target = terminal_id or self.TERMINAL
        response = self.client.post(f"/api/v1/terminals/{target}/output",
                                    json={"bridgeId": BRIDGE, "output": text})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def _get(self, terminal_id: str = "") -> dict:
        target = terminal_id or self.TERMINAL
        response = self.client.get(f"/api/v1/terminals/{target}?cols=132&rows=26")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["terminal"]

    def _produce_during_the_await(self, terminal_id: str = "", chunk: str = "", status: str = ""):
        """Append output through the REAL producer, at the existing await, exactly once.

        Wrapping `_attach_terminal_snapshot` puts the injection between the serialisation that reads
        the sequence and the render that reads the screen -- which is the span the role lookup
        occupies. A stub that only bumped a counter would prove nothing about the pair; this feeds
        the live screen and records the sequence the way a producing agent does.

        `chunk` DECIDES WHICH BRANCH THE RESPONSE TAKES, which is not obvious and cost a red test:
        the default carries escapes, and an escape-carrying chunk CREATES a live screen for a
        terminal that had none. So a replay-branch test has to inject plain bytes, or the injection
        moves the terminal onto the branch it was trying to stay off.
        """
        real = terminals_router._attach_terminal_snapshot
        state = {"injected": False}

        async def wrapper(term_dict, cols, rows):
            if not state["injected"]:
                state["injected"] = True
                db = await get_db()
                try:
                    target = terminal_id or self.TERMINAL
                    row = await (await db.execute(
                        "SELECT * FROM terminal_sessions WHERE id = ?", (target,)
                    )).fetchone()
                    # THE SEQUENCE IS THE PRODUCER'S TO ASSIGN, so this assigns one. `POST
                    # /terminals/{id}/output` computes the next number from the terminal's own
                    # sequence and hands it down; `_append_terminal_output` records whatever it is
                    # given and records NOTHING when given None. An injection that fed the screen
                    # and left the sequence alone would move only half the pair and could not show
                    # a tear at all -- which is what the first version of this test did.
                    seq = current_seq(target, row["output_seq"] or 0) + 1
                    body = chunk or (ESC + "[10;1H" + INJECTED + ESC + "[0m")
                    await _append_terminal_output(db, row, body, seq=seq, status=status)
                    await db.commit()
                finally:
                    await db.close()
            return await real(term_dict, cols, rows)

        return patch.object(terminals_router, "_attach_terminal_snapshot", wrapper), state

    def test_the_live_branch_is_the_one_under_test(self):
        """POSITIVE CONTROL. The fix is on the LIVE-screen path; a fixture that never reaches it
        would pass every assertion below while measuring the replay path instead."""
        self._write(PAINT + "a first line of output")
        self.assertIsNotNone(render_live_screen(self.TERMINAL),
                             "no live screen exists for this terminal, so the branch this file "
                             "tests is never taken and its other assertions are vacuous")

    def test_output_arriving_at_the_await_is_in_BOTH_the_screen_and_the_sequence(self):
        self._write(PAINT + "a first line of output")
        before = self._get()

        patcher, state = self._produce_during_the_await()
        with patcher:
            torn = self._get()
        self.assertTrue(state["injected"], "the producer never ran, so nothing was interleaved")

        # THE INJECTION REACHED THE SCREEN. Without this the test could pass by the snapshot simply
        # not containing the new bytes, which is a different (and also wrong) response.
        self.assertIn(INJECTED, torn["snapshot"],
                      "the injected output is not in the rendered screen, so this response is not "
                      "the torn pair the test is about")
        self.assertNotIn(INJECTED, before["snapshot"])

        # THE PAIR. A quiescent GET taken afterwards renders the same screen; if the sequences
        # differ, the earlier response described that screen with a smaller number and the browser
        # would accept the injected frame again.
        quiet = self._get()
        self.assertEqual(torn["snapshot"], quiet["snapshot"],
                         "the two reads rendered different screens, so this comparison is not "
                         "about the sequence")
        self.assertEqual(torn["outputSeq"], quiet["outputSeq"],
                         f"the response carried sequence {torn['outputSeq']} for a screen the next "
                         f"quiescent read describes as {quiet['outputSeq']} -- the browser is seeded "
                         f"with bytes it will be sent again")
        self.assertGreater(torn["outputSeq"], before["outputSeq"],
                           "the sequence did not move at all, so nothing was actually appended")

    def test_a_terminal_that_is_ENDING_pairs_them_too(self):
        """THE HALF THE FIRST REPAIR LEFT OPEN, and review reproduced it with `status=stopped`.

        That repair took the number from the TAIL BUFFER. An appending write on a terminal that is
        stopping feeds the live screen and then `forget()`s the buffer -- so the read fell through to
        the stale serialised value while the screen carried the new bytes. Served sequence 1 with the
        new generation on screen; the next quiescent GET, sequence 2, identical snapshot.

        THE BUFFER AND THE SCREEN HAVE DIFFERENT LIFETIMES. Only the one whose lifetime the picture
        shares can describe the picture, so the number lives on the screen now.
        """
        self._write(PAINT + "before the end")
        before = self._get()

        patcher, state = self._produce_during_the_await(status="stopped")
        with patcher:
            torn = self._get()
        self.assertTrue(state["injected"], "the producer never ran, so nothing was interleaved")

        self.assertIn(INJECTED, torn["snapshot"],
                      "the injected output is not in the rendered screen, so this response is not "
                      "the torn pair the test is about")
        self.assertNotIn(INJECTED, before["snapshot"])

        quiet = self._get()
        self.assertEqual(torn["snapshot"], quiet["snapshot"],
                         "the two reads rendered different screens, so this comparison is not "
                         "about the sequence")
        self.assertEqual(torn["outputSeq"], quiet["outputSeq"],
                         f"an ENDING terminal served sequence {torn['outputSeq']} for a screen the "
                         f"next read describes as {quiet['outputSeq']}")
        self.assertGreater(torn["outputSeq"], before["outputSeq"],
                           "the sequence did not move at all, so nothing was actually appended")

    def test_the_REPLAY_branch_keeps_the_pair_it_already_had(self):
        """The fix is scoped to the live branch, and this pins why it must be.

        The replay branch renders from `term_dict["output"]`, which `_terminal_session_to_dict` read
        in the same expression as `outputSeq` -- no await between them, so that pair is already one
        generation. Re-reading the sequence there would pair a NEWER number with an OLDER screen,
        which is the same defect mirrored: the browser would be told it already holds a frame that
        is in neither its seed nor its future.
        """
        self._write("a plain log line with no escapes", self.PLAIN_TERMINAL)
        self.assertIsNone(render_live_screen(self.PLAIN_TERMINAL),
                          "this terminal has a live screen, so it is not exercising the replay "
                          "branch this test is about")
        before = self._get(self.PLAIN_TERMINAL)

        # PLAIN BYTES, so the injection does not hand this terminal a live screen and move it onto
        # the other branch mid-test. It did exactly that on the first run.
        patcher, state = self._produce_during_the_await(self.PLAIN_TERMINAL, chunk=INJECTED)
        with patcher:
            during = self._get(self.PLAIN_TERMINAL)
        self.assertTrue(state["injected"], "the producer never ran, so nothing was interleaved")
        self.assertIsNone(render_live_screen(self.PLAIN_TERMINAL),
                          "the injection created a live screen, so this response took the branch "
                          "the test was trying to stay off")

        self.assertNotIn(INJECTED, during["snapshot"] or during["output"],
                         "the replay branch rendered output that arrived after it read its tail")
        self.assertEqual(during["outputSeq"], before["outputSeq"],
                         f"the sequence moved to {during['outputSeq']} for a screen rendered from a "
                         f"tail read at {before['outputSeq']}")

    def test_a_QUIET_terminal_still_pairs_its_snapshot_with_its_own_sequence(self):
        """NEGATIVE CONTROL for the fix itself: with nothing interleaved the pair is unchanged.

        A fix that simply raised the sequence would pass the test above and break this one.
        """
        self._write(PAINT + "one line")
        first = self._get()
        second = self._get()
        self.assertEqual(first["snapshot"], second["snapshot"])
        self.assertEqual(first["outputSeq"], second["outputSeq"],
                         "two reads of an idle terminal disagreed about its sequence")
