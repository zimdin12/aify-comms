"""P7 witnesses: the claim's steer bypass (ledger B19) and the explicit-queue skip, through `POST /dispatch/claim`.

While the hold is on, a claim still delivers a run that can be steered into the turn. The bypass
(`claim_gating._has_claimable_steerable_run`) needs every one of: the target has `steer`; the run is not an
explicit queue and asks to steer; it runs in channel or resident mode; this bridge claims that mode; and it
asks for no other runtime. P0 C6 keeps the bypass unchanged across the switch, so each arm is witnessed on
its own: one world per arm, false on that arm only, beside the all-true twin that IS delivered mid-turn.

THE CANARY. The claim's selection repeats several of the same filters, so with one run per world most arms
would decide the same whether the bypass ran or not. Every world therefore also queues a canary: a steerable
run in `managed` mode, which this resident target cannot execute. If the claim gets past the hold to
selection, selection cancels the canary as a mode mismatch. A held claim leaves it queued. So each arm
witnesses what the hold actually prevents: the claim proceeding at all.

Every world is busy on `fresh-hook-turn`, which holds under both laws.
"""
from __future__ import annotations

import sqlite3

from service.tests._base import FastApiTestCase
from service.tests.delivery_witnesses import HOLD_LAW, INPUT_ADAPTER, TurnSpecimen

MACHINE = "linux:test-host"
BUSY = next(case.spec for case in HOLD_LAW if case.spec.name == "fresh-hook-turn")
STEER_RUN = {"execution_mode": "resident", "queue_if_busy": 0, "steer_if_busy": 1, "requested_runtime": ""}
MODES = ["resident", "managed"]


class _ClaimWorld(FastApiTestCase):
    """A busy resident codex and its queue, claimed through the route."""

    def _rows(self, query: str, params=()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(query, params).fetchall())
        finally:
            conn.close()

    def _queue(self, run_id: str, agent_id: str, *, at: str, **run) -> None:
        run = {**STEER_RUN, **run}
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, message_type, "
                "subject, body, priority, status, queue_if_busy, steer_if_busy, requested_runtime, requested_at) "
                "VALUES (?,?,?,'start_if_possible',?,'info','s','b','normal','queued',?,?,?,?)",
                (run_id, "dashboard", agent_id, run["execution_mode"], run["queue_if_busy"], run["steer_if_busy"],
                 run["requested_runtime"], at))
            conn.commit()
        finally:
            conn.close()

    def _world(self, agent_id: str, *, steer: bool = True, spec: TurnSpecimen = BUSY, **run) -> None:
        """A resident codex, busy, with the subject run R1 and the canary queued behind it."""
        answer = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "codex", "sessionMode": "resident",
            "machineId": MACHINE, "bridgeId": f"bridge-{agent_id}", "sessionHandle": f"thread-{agent_id}",
            "capabilities": ["resident-run", "resume", "interrupt"] + (["steer"] if steer else []),
            "runtimeConfig": {"appServerUrl": "ws://127.0.0.1:9"}})
        self.assertEqual(answer.status_code, 200, answer.text)
        INPUT_ADAPTER.apply(self._db_path, agent_id, spec)
        self._queue(f"r1-{agent_id}", agent_id, at="2001-01-01T00:00:00Z", **run)
        self._queue(f"canary-{agent_id}", agent_id, at="2002-01-01T00:00:00Z", execution_mode="managed")

    def _claim(self, agent_id: str, modes=MODES) -> dict:
        answer = self.client.post("/api/v1/dispatch/claim", json={
            "agentId": agent_id, "bridgeId": f"bridge-{agent_id}", "machineId": MACHINE, "executionModes": modes})
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def _statuses(self, agent_id: str) -> dict:
        return {row["id"]: row["status"] for row in
                self._rows("SELECT id, status FROM dispatch_runs WHERE target_agent = ?", (agent_id,))}

    def _assert_held(self, agent_id: str, body: dict) -> None:
        self.assertEqual(body, {"ok": True, "run": None}, "the hold answers no run and nothing else")
        self.assertEqual(self._statuses(agent_id), {f"r1-{agent_id}": "queued", f"canary-{agent_id}": "queued"},
                         "a held claim must not reach selection: nothing in the queue may change")

    def _assert_steered(self, agent_id: str, body: dict) -> None:
        self.assertEqual((body.get("run") or {}).get("id"), f"r1-{agent_id}", body)
        self.assertEqual(self._statuses(agent_id)[f"r1-{agent_id}"], "claimed")



class SteerBypassWitness(_ClaimWorld):
    """One world per bypass arm, each beside the all-true twin that is delivered into the turn."""

    def test_a_target_without_steer_is_held(self):
        self._world("arm-capability", steer=False)
        self._assert_held("arm-capability", self._claim("arm-capability"))
        self._world("arm-capability-twin")
        self._assert_steered("arm-capability-twin", self._claim("arm-capability-twin"))

    def test_an_explicit_queue_run_is_held(self):
        self._world("arm-queue", queue_if_busy=1)
        self._assert_held("arm-queue", self._claim("arm-queue"))
        self._world("arm-queue-twin")
        self._assert_steered("arm-queue-twin", self._claim("arm-queue-twin"))

    def test_a_run_that_does_not_ask_to_steer_is_held(self):
        self._world("arm-steer-flag", steer_if_busy=0)
        self._assert_held("arm-steer-flag", self._claim("arm-steer-flag"))
        self._world("arm-steer-flag-twin")
        self._assert_steered("arm-steer-flag-twin", self._claim("arm-steer-flag-twin"))

    def test_a_managed_mode_run_is_held(self):
        self._world("arm-mode", execution_mode="managed")
        self._assert_held("arm-mode", self._claim("arm-mode"))
        self._world("arm-mode-twin")
        self._assert_steered("arm-mode-twin", self._claim("arm-mode-twin"))

    def test_a_run_in_a_mode_this_bridge_does_not_claim_is_held(self):
        self._world("arm-supported", execution_mode="channel")
        self._assert_held("arm-supported", self._claim("arm-supported"))
        self._world("arm-supported-twin")
        self._assert_steered("arm-supported-twin", self._claim("arm-supported-twin"))

    def test_a_run_for_another_runtime_is_held(self):
        self._world("arm-runtime", requested_runtime="claude-code")
        self._assert_held("arm-runtime", self._claim("arm-runtime"))
        self._world("arm-runtime-twin")
        self._assert_steered("arm-runtime-twin", self._claim("arm-runtime-twin"))


class ExplicitQueueSkipWitness(_ClaimWorld):
    """Selection under the hold: with a steerable run queued, the claim delivers that run and skips an EARLIER
    run that is not steerable, which waits for the turn to end. Its twin, the same queue with the turn closed,
    delivers the earlier run first."""

    def _two_runs(self, agent_id: str, spec: TurnSpecimen) -> None:
        self._world(agent_id, spec=spec)                                  # r1 steerable, at 2001
        self._queue(f"r0-{agent_id}", agent_id, at="2000-01-01T00:00:00Z", steer_if_busy=0)

    def test_a_busy_claim_skips_the_earlier_unsteerable_run_and_the_closed_twin_takes_it(self):
        self._two_runs("skip", BUSY)
        body = self._claim("skip")
        self.assertEqual(body["run"]["id"], "r1-skip", body)
        self.assertEqual(self._statuses("skip")["r0-skip"], "queued", "the explicit wait is kept until the turn ends")

        self._two_runs("skip-twin", BUSY.closed())
        body = self._claim("skip-twin")
        self.assertEqual(body["run"]["id"], "r0-skip-twin", body)
        self.assertEqual(self._statuses("skip-twin")["r0-skip-twin"], "claimed")
