"""The size a terminal is recorded at comes from the host, not from what was asked for.

WHAT THIS CLOSES. `terminal_sessions.cols` had exactly one writer: a COMPLETED resize control, using
the dims the SERVICE requested. Measured on the live control table, every start control carries
`cols = 0, rows = 0` (35 of them); only resizes carry real numbers. So a terminal had no recorded
width until a person opened its console and a fit round-tripped -- and across 13 live terminals,
`cols > 0` held if and only if a completed resize existed, 13 of 13 with no disagreement.

WHY A MISSING WIDTH IS NOT COSMETIC. With no stored width the snapshot renderer infers one from the
drawn cells. Rendering a screen at a width the source was NOT drawn at re-wraps every line, which is
what the "scrambled console" complaint has looked like since May. The service already says so in
`terminal_controls.py`: recording real cols "kills the live-redraw garble caused by inferred !=
actual width". It just had no way to learn them until somebody resized.

THE REPORT WINS OVER THE REQUEST, and that ordering is the design decision here rather than an
accident. A request is a wish: a host may clamp it, refuse it, or open a pty at its own default when
asked for nothing. Only the host knows what its pty actually took. So a reported size overwrites a
requested one, and the requested-size branch remains for hosts that report nothing.

`processId` is the precedent this follows exactly -- a fact only the tier holding the pty can know,
reported on control completion and persisted onto the terminal row.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.db import get_db
from service.tests._base import FastApiTestCase

BRIDGE = "bridge-size-probe"
ENVIRONMENT = "linux:test-host:default"


class TheHostReportsItsPtySizeTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    AGENT = "size-probe-agent"
    TERMINAL = "term_size_probe"
    SESSION = "sess_size_probe"

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
        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "owner_mode, terminal_id, terminal_status, started_at, last_seen, "
                    "spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.SESSION, self.AGENT, ENVIRONMENT, "claude-code", "running", "console",
                     self.TERMINAL, "attached", "2026-09-03T02:00:00Z", "2026-09-03T02:00:00Z",
                     None, None),
                )
                # cols/rows deliberately UNSET, which is the state every freshly spawned terminal
                # is in and the whole reason this path exists.
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, status, output, error, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.TERMINAL, self.AGENT, self.SESSION, ENVIRONMENT, "claude-code", BRIDGE,
                     # Some output, because the snapshot is only rendered for a terminal that HAS
                     # a log -- and a stored width that never reaches a render is the half of this
                     # that matters. An empty log made `renderedCols` absent entirely.
                     "claude-aify --aify-agent x", "attached", "hello from the pty", "",
                     "2026-09-03T02:00:00Z", "2026-09-03T02:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    _seq = 0

    def _control(self, action: str, cols: int = 0, rows: int = 0) -> str:
        """A pending control. `cols`/`rows` are what the SERVICE asked for -- 0 for a start."""
        type(self)._seq += 1
        control_id = f"size-ctl-{type(self)._seq:03d}"

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO terminal_controls (id, terminal_id, environment_id, bridge_id, "
                    "action, status, requested_by, requested_at, cols, rows) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (control_id, self.TERMINAL, ENVIRONMENT, BRIDGE, action, "pending", "tester",
                     "2026-09-03T02:01:00Z", cols, rows),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())
        return control_id

    def _size(self) -> tuple:
        async def go():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT cols, rows FROM terminal_sessions WHERE id = ?", (self.TERMINAL,),
                )).fetchone()
                return (int(row["cols"] or 0), int(row["rows"] or 0))
            finally:
                await db.close()

        return asyncio.run(go())

    def test_the_fixture_starts_with_no_recorded_size(self) -> None:
        """POSITIVE CONTROL. Every assertion below is about a size ARRIVING, and a fixture that
        already carried one would satisfy them whatever the code did."""
        self.assertEqual(self._size(), (0, 0))

    def test_a_completed_start_control_records_the_size_the_host_reports(self) -> None:
        # The defect this closes. The control asked for nothing -- cols 0, as every real start
        # control does -- and the pty was nonetheless opened at a real width.
        control_id = self._control("start", cols=0, rows=0)
        answered = self.client.patch(
            f"/api/v1/terminals/controls/{control_id}",
            json={"status": "completed", "terminalStatus": "attached", "cols": 120, "rows": 30},
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(self._size(), (120, 30))

    def test_the_reported_size_reaches_the_rendered_snapshot(self) -> None:
        # The consequence, not just the column: a stored width is what stops the renderer guessing.
        # Asserting the row alone would pass if nothing downstream ever read it.
        control_id = self._control("start")
        self.client.patch(
            f"/api/v1/terminals/controls/{control_id}",
            json={"status": "completed", "terminalStatus": "attached", "cols": 150, "rows": 40},
        )
        # The viewer is NARROWER than the pty, which is the case that garbles. The renderer takes
        # max(viewer, source), so a recorded 150 must win over a 100-wide pane -- and before the
        # width was recorded there was nothing for it to win with, so the pane's 100 was used and
        # every line the pty drew past column 100 wrapped.
        fetched = self.client.get(f"/api/v1/terminals/{self.TERMINAL}?cols=100&rows=30")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["terminal"]["cols"], 150)
        self.assertEqual(fetched.json()["terminal"]["renderedCols"], 150,
                         "the recorded width did not reach the render, so it changes nothing")

    def test_a_host_that_reports_no_size_changes_nothing(self) -> None:
        # FAILS CLOSED. Most hosts and most controls report no size at all, and a guard that treats
        # a missing value as zero would blank out a width that had been correctly recorded.
        self.client.patch(
            f"/api/v1/terminals/controls/{self._control('start')}",
            json={"status": "completed", "terminalStatus": "attached", "cols": 110, "rows": 25},
        )
        self.assertEqual(self._size(), (110, 25))
        answered = self.client.patch(
            f"/api/v1/terminals/controls/{self._control('input')}",
            json={"status": "completed"},
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(self._size(), (110, 25), "a report carrying no size erased the stored one")

    def test_a_zero_is_not_a_size(self) -> None:
        # A host saying "I do not know" must not be recorded as a zero-width terminal, which would
        # be worse than no width at all -- the renderer's own fallback needs the column empty.
        self.client.patch(
            f"/api/v1/terminals/controls/{self._control('start')}",
            json={"status": "completed", "terminalStatus": "attached", "cols": 100, "rows": 20},
        )
        self.client.patch(
            f"/api/v1/terminals/controls/{self._control('start')}",
            json={"status": "completed", "terminalStatus": "attached", "cols": 0, "rows": 0},
        )
        self.assertEqual(self._size(), (100, 20))

    def test_a_FAILED_control_records_nothing_however_it_reports(self) -> None:
        # A control that failed did not apply anything, so whatever it says about size is a claim
        # about a pty that is not in that state.
        self.client.patch(
            f"/api/v1/terminals/controls/{self._control('start')}",
            json={"status": "completed", "terminalStatus": "attached", "cols": 100, "rows": 20},
        )
        answered = self.client.patch(
            f"/api/v1/terminals/controls/{self._control('resize', cols=90, rows=24)}",
            json={"status": "failed", "error": "pty refused", "cols": 90, "rows": 24},
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(self._size(), (100, 20))

    def test_WHAT_THE_HOST_APPLIED_BEATS_WHAT_THE_SERVICE_ASKED_FOR(self) -> None:
        # The ordering decision. A resize requesting 200 that the host clamped to 132 must be
        # recorded as 132: the request is a wish and only the host knows what its pty took. Written
        # as its own test because the two branches agree on every ordinary resize, so nothing else
        # here would notice if the request started winning.
        answered = self.client.patch(
            f"/api/v1/terminals/controls/{self._control('resize', cols=200, rows=50)}",
            json={"status": "completed", "terminalStatus": "attached", "cols": 132, "rows": 43},
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(self._size(), (132, 43))

    def test_a_resize_that_reports_nothing_still_records_what_it_asked_for(self) -> None:
        # The older path, kept: a host that completes a resize without reporting dims is saying it
        # applied them, and that behaviour predates this change and must survive it.
        answered = self.client.patch(
            f"/api/v1/terminals/controls/{self._control('resize', cols=96, rows=26)}",
            json={"status": "completed", "terminalStatus": "attached"},
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(self._size(), (96, 26))
