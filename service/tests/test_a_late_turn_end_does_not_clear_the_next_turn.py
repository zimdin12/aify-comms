"""A turn-end that arrives after the next turn started does not end that turn.

External review of 0.7.6 (O2). The hooks were ordered by fire time (`api_core/hook_event_order.py`),
but nothing else was: the bridge-side detectors and hermes' `clearTurn` posted `/turn-end` with a
`bridgeId` and nothing naming the turn, the heartbeat's `turnBusy` start recorded no time, and the
engine's run-id guard never ran because every producer sent an empty run id. So a late end cleared
whatever turn was open, a newer run's included.

Two things now name the turn an end is about, and an end carrying neither applies as before:

  * a RUN ID. `/turn-end` with `runId` clears only when that run's turn is the open one, in both
    `agent_turn_state` and the engine's `agent_status_state`.
  * a FIRE TIME. Detectors stamp `firedAtUs` and `machineId` like the hooks, and a heartbeat that
    STARTS a turn records its stamp in the same ordering, so an end observed before that start loses.

Driven through the real endpoints.
"""

from __future__ import annotations

import sqlite3

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "late-end-agent"
HOST = "win32:host-a"
LOOP = "hermes-managed-host-win32:host-a-late-end-agent"


class ALateTurnEndDoesNotClearTheNextTurnTests(FastApiTestCase):
    DB_NAME = "aify-late-turn-end.db"

    def setUp(self):
        super().setUp()
        response = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "launchMode": "detached", "sessionHandle": "late-end-session", "machineId": HOST,
            "bridgeId": "late-end-bridge", "capabilities": ["resident-run"],
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _post(self, path: str, **body) -> dict:
        response = self.client.post(f"/api/v1/agents/{AGENT}/{path}", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _heartbeat_busy(self, run_id: str, fired_at_us: int | None = None) -> None:
        body = {"bridgeId": LOOP, "turnBusy": True, "turnRunId": run_id, "turnRuntime": "hermes"}
        if fired_at_us is not None:
            body.update(firedAtUs=fired_at_us, machineId=HOST)
        self._post("heartbeat", **body)

    def _turn(self) -> tuple:
        """(turn_busy, turn_run_id) and (in_turn, turn_run_id): both tables the end must agree with."""
        db = sqlite3.connect(self._db_path)
        try:
            busy = db.execute("SELECT turn_busy, turn_run_id FROM agent_turn_state WHERE agent_id = ?", (AGENT,)).fetchone()
            engine = db.execute("SELECT in_turn, turn_run_id FROM agent_status_state WHERE agent_id = ?", (AGENT,)).fetchone()
            return (tuple(busy) if busy else (0, ""), tuple(engine) if engine else (0, ""))
        finally:
            db.close()

    # ── the two reproductions ───────────────────────────────────────────────────────────────

    def test_a_detector_end_observed_before_the_next_hook_start_does_not_clear_it(self):
        """The transcript detector saw the old turn end at 1500; the hook started the next at 2000, and
        the detector's post landed after. The body is the one the detector now sends
        (`claude-turn-detector-state.mjs`); before the fix it carried no time, and cleared."""
        self._post("turn-start", firedAtUs=2000, machineId=HOST)
        self.assertEqual(self._post("turn-end", bridgeId="late-end-bridge", turnRuntime="claude-code",
                                    source="bridge-transcript-detector", firedAtUs=1500, machineId=HOST
                                    ).get("ignored"), "out_of_order_hook_event")
        self.assertEqual(self._turn(), ((1, ""), (1, "")))

    def test_a_runless_end_observed_before_a_heartbeat_started_turn_does_not_clear_it(self):
        """Run r2's turn started on the loop's heartbeat at 2000; a runless end the gateway detector
        observed at 1500 arrives after it."""
        self._heartbeat_busy("r2", fired_at_us=2000)
        self.assertEqual(self._turn(), ((1, "r2"), (1, "r2")), "precondition: r2's turn is open")
        self._post("turn-end", bridgeId=LOOP, turnRuntime="hermes", firedAtUs=1500, machineId=HOST)
        self.assertEqual(self._turn(), ((1, "r2"), (1, "r2")), "a late runless end cleared r2's turn")

    def test_the_end_of_run_r1_does_not_clear_run_r2s_turn(self):
        self._heartbeat_busy("r2")
        answer = self._post("turn-end", bridgeId=LOOP, turnRuntime="hermes", runId="r1")
        self.assertEqual(answer.get("ignored"), "another_turn_is_open")
        self.assertEqual(self._turn(), ((1, "r2"), (1, "r2")), "r1's end cleared r2's turn")

    def test_a_heartbeat_start_that_fired_before_the_last_end_does_not_reopen_the_turn(self):
        """The heartbeat start is in the ordering both ways: a late one is refused like a late hook."""
        self._heartbeat_busy("r1", fired_at_us=1000)
        self._post("turn-end", runId="r1", firedAtUs=3000, machineId=HOST)
        self._heartbeat_busy("r2", fired_at_us=2000)
        self.assertEqual(self._turn()[0][0], 0, "a start fired before the last end reopened the turn")

    # ── controls ────────────────────────────────────────────────────────────────────────────

    def test_CONTROL_the_matching_run_end_clears_both_tables(self):
        self._heartbeat_busy("r2", fired_at_us=2000)
        self._post("turn-end", bridgeId=LOOP, turnRuntime="hermes", runId="r2", firedAtUs=2500, machineId=HOST)
        self.assertEqual(self._turn(), ((0, ""), (0, "")))

    def test_CONTROL_a_legacy_end_with_neither_still_clears(self):
        self._heartbeat_busy("r2", fired_at_us=2000)
        self._post("turn-end", bridgeId=LOOP, turnRuntime="hermes")
        self.assertEqual(self._turn(), ((0, ""), (0, "")))

    def test_a_refused_turn_event_is_logged_and_an_accepted_one_is_not(self):
        # The route answers 200 either way, so the log is the only trace of a refusal. A clock skew
        # that refused every turn-end of a relaunched bridge went unseen (review of 0.7.6).
        logger = "service.api_core.hook_event_order"
        self._post("turn-start", firedAtUs=2000, machineId=HOST)
        with self.assertLogs(logger, level="INFO") as caught:
            self._post("turn-end", bridgeId="late-end-bridge", firedAtUs=1500, machineId=HOST)
        self.assertTrue(any("refused as out of order" in line and "firedAtUs=1500" in line for line in caught.output))
        with self.assertNoLogs(logger, level="INFO"):
            self._post("turn-end", bridgeId="late-end-bridge", firedAtUs=2500, machineId=HOST)

    def test_CONTROL_a_stamped_end_after_the_start_clears(self):
        self._post("turn-start", firedAtUs=2000, machineId=HOST)
        self._post("turn-end", bridgeId="late-end-bridge", firedAtUs=2500, machineId=HOST)
        self.assertEqual(self._turn(), ((0, ""), (0, "")))

    def test_CONTROL_a_run_end_clears_when_only_the_busy_row_names_the_run(self):
        """A hook turn-start inside a run's turn leaves the busy row's run and blanks the engine's
        (`turn_boundaries.py`). The run's own end must still clear both, or `in_turn` sticks."""
        self._heartbeat_busy("r1")
        self._post("turn-start")
        self.assertEqual(self._turn(), ((1, "r1"), (1, "")), "precondition: the two tables disagree")
        self._post("turn-end", bridgeId=LOOP, runId="r1")
        self.assertEqual(self._turn(), ((0, ""), (0, "")))
