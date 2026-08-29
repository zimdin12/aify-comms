r"""A terminal's lifecycle events survive however much output it produces.

`terminal_events` holds two different things under one cap:

  * `terminal_output` rows -- a per-chunk copy of the bytes, and the FALLBACK RECORDING
    `richest_recording` reads when `terminal_sessions.output` holds nothing but an exit marker. On a
    real death (2026-08-26) that column held 18 characters while the events held 14,773.
  * everything else -- control completions, input requests, consistency repairs, orphan reaps, PTY
    starts. The trail that says what HAPPENED to the terminal.

The pruner kept the newest 200 of everything, so the first starved the second.

MEASURED on the operator's database, 2026-08-29:

    4,605 terminal_output rows        326 lifecycle rows        26 terminals
    21 terminals at the 200 cap, of which THREE held ZERO lifecycle events
    the 200-row window covers a median of 4.5 minutes, and 3.3 on the busiest console

So `GET /terminals/{id}` -- the endpoint whose comment says it exists to explain a terminal
"including whatever it was doing when it died" -- could answer that only for the last few minutes,
and for three terminals could not answer it at all: every row in their window was output.

TWO CAPS RATHER THAN A BIGGER ONE. Raising a single cap buys more output at the same ratio. Lifecycle
rows are rare -- median 2 per terminal, 159 at the most -- so a second cap of the same size costs
almost nothing today and cannot be starved tomorrow.
"""
from __future__ import annotations

import asyncio

from service.api_core.tuning import (
    TERMINAL_EVENTS_KEPT_PER_TERMINAL,
    TERMINAL_LIFECYCLE_EVENTS_KEPT_PER_TERMINAL,
)
from service.db import get_db
from service.reconcilers.terminal_history import _prune_terminal_history
from service.tests._base import FastApiTestCase

TERMINAL = "term-starve-probe"


class OutputChatterDoesNotEvictTheLifecycleTrail(FastApiTestCase):
    def _sql(self, query: str, params: tuple = ()):
        async def run():
            db = await get_db()
            try:
                return await (await db.execute(query, params)).fetchall()
            finally:
                await db.close()

        return asyncio.run(run())

    def setUp(self) -> None:
        """`terminal_events.terminal_id` is a real foreign key, so the row needs its whole family:
        an environment, an agent, a session, a terminal. Seeded through the API where one exists --
        a hand-built parent is another copy of the schema, and the first version of this fixture
        inserted events for a terminal that did not exist and got an IntegrityError for it."""
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "env-starve", "label": "probe", "machineId": "probe-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-1", "cwdRoots": ["/w"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"]}],
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self.client.post("/api/v1/agents", json={
            "agentId": "starve-agent", "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "launchMode": "detached",
        })
        self.assertEqual(registered.status_code, 200, registered.text)

        async def seed():
            db = await get_db()
            try:
                # `spawn_spec_id`/`spawn_request_id` DEFAULT to '' and carry foreign keys, so a row
                # leaning on the defaults fails against a spawn row with id ''. NULL is exempt.
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace,"
                    " mode, status, started_at, last_seen, spawn_spec_id, spawn_request_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    ("session-starve", "starve-agent", "env-starve", "claude-code", "/w",
                     "managed-warm", "running", "2026-08-29T00:00:00Z", "2026-08-29T00:00:00Z",
                     None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id,"
                    " bridge_id, runtime, workspace, command, status, requested_by, created_at,"
                    " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (TERMINAL, "session-starve", "starve-agent", "env-starve", "bridge-1",
                     "claude-code", "/w", "claude", "running", "dashboard",
                     "2026-08-29T00:00:00Z", "2026-08-29T00:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed())

    def _events(self, kinds: list[tuple[str, int]]) -> None:
        """Insert events in the given order, so row ids reflect arrival."""
        async def run():
            db = await get_db()
            try:
                for event_type, count in kinds:
                    await db.executemany(
                        "INSERT INTO terminal_events (terminal_id, event_type, body, created_at)"
                        " VALUES (?,?,?,?)",
                        [(TERMINAL, event_type, f"{event_type}-{index}", "2026-08-29T00:00:00Z")
                         for index in range(count)],
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(run())

    def _prune(self) -> dict:
        async def run():
            db = await get_db()
            try:
                return await _prune_terminal_history(db)
            finally:
                await db.close()

        return asyncio.run(run())

    def _counts(self) -> tuple[int, int]:
        rows = self._sql(
            "SELECT SUM(CASE WHEN event_type = 'terminal_output' THEN 1 ELSE 0 END),"
            "       SUM(CASE WHEN event_type != 'terminal_output' THEN 1 ELSE 0 END)"
            " FROM terminal_events WHERE terminal_id = ?", (TERMINAL,),
        )
        return int(rows[0][0] or 0), int(rows[0][1] or 0)

    def test_THE_DEFECT_output_no_longer_evicts_the_lifecycle_trail(self):
        """The exact shape of the three starved terminals: a handful of lifecycle rows, then enough
        output to fill the window twice over. Under one shared cap the lifecycle rows are the OLDEST
        and go first."""
        self._events([("terminal_control_completed", 3),
                      ("managed_pty_start_requested", 2),
                      ("terminal_output", TERMINAL_EVENTS_KEPT_PER_TERMINAL * 2)])
        self._prune()
        output, lifecycle = self._counts()
        self.assertEqual(lifecycle, 5, (
            "the lifecycle trail was evicted by output chatter; three of the operator's terminals "
            "were in exactly this state, holding zero lifecycle events"
        ))
        self.assertEqual(output, TERMINAL_EVENTS_KEPT_PER_TERMINAL)

    def test_the_output_recording_is_still_capped(self):
        """The other half. `richest_recording` reads these rows, but they are also what made the
        window three minutes long -- so the fix must not become "keep everything"."""
        self._events([("terminal_output", TERMINAL_EVENTS_KEPT_PER_TERMINAL + 40)])
        self._prune()
        output, _lifecycle = self._counts()
        self.assertEqual(output, TERMINAL_EVENTS_KEPT_PER_TERMINAL)

    def test_the_lifecycle_trail_is_capped_TOO(self):
        """A cap nobody enforces is not a cap. One terminal in the fleet holds 159 lifecycle rows, so
        this is not hypothetical -- an agent that restarts in a loop can produce them without limit."""
        self._events([("terminal_control_completed",
                       TERMINAL_LIFECYCLE_EVENTS_KEPT_PER_TERMINAL + 25)])
        self._prune()
        _output, lifecycle = self._counts()
        self.assertEqual(lifecycle, TERMINAL_LIFECYCLE_EVENTS_KEPT_PER_TERMINAL)

    def test_THE_NEWEST_of_each_kind_survives(self):
        """Which end is kept is the whole point of the endpoint this feeds: the route already had to
        be fixed once for returning a terminal's OLDEST events. A pruner that kept the oldest would
        reintroduce that from the other side."""
        self._events([("terminal_output", 5), ("terminal_control_completed", 5)])
        self._events([("terminal_output", TERMINAL_EVENTS_KEPT_PER_TERMINAL)])
        self._prune()
        bodies = [row[0] for row in self._sql(
            "SELECT body FROM terminal_events WHERE terminal_id = ? AND event_type = 'terminal_output'"
            " ORDER BY id ASC LIMIT 1", (TERMINAL,),
        )]
        self.assertEqual(bodies, ["terminal_output-0"], (
            "the surviving output rows start at the first row of the SECOND batch, so the oldest "
            "batch was dropped -- which is the intended end"
        ))

    def test_a_quiet_terminal_loses_nothing(self):
        """The common case, and the control against a pruner that deletes when it should not."""
        self._events([("terminal_output", 12), ("terminal_input_requested", 3)])
        self._prune()
        self.assertEqual(self._counts(), (12, 3))

    def test_THE_PRUNER_ACTUALLY_RAN(self):
        """POSITIVE CONTROL. Every assertion above is a count after a call; a `_prune_terminal_history`
        that returned early would satisfy the quiet-terminal case and be indistinguishable from a
        working one on a fixture small enough."""
        self._events([("terminal_output", TERMINAL_EVENTS_KEPT_PER_TERMINAL + 30)])
        result = self._prune()
        self.assertGreaterEqual(result.get("terminal_events_capped", 0), 30, (
            f"the pruner reported {result.get('terminal_events_capped')} capped rows; it did not run"
        ))
