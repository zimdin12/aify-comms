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
        INPUT_ADAPTER.apply(self, agent_id, spec)
        self._queue(f"r1-{agent_id}", agent_id, at="2001-01-01T00:00:00Z", **run)
        self._queue(f"canary-{agent_id}", agent_id, at="2002-01-01T00:00:00Z", execution_mode="managed")

    def _claim(self, agent_id: str, modes=MODES) -> dict:
        answer = self.client.post("/api/v1/dispatch/claim", json={
            "agentId": agent_id, "bridgeId": f"bridge-{agent_id}", "machineId": MACHINE, "executionModes": modes})
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def _statuses(self, agent_id: str) -> dict:
        """Every run of this target: its status and the bridge that claimed it ('' while unclaimed)."""
        return {row["id"]: (row["status"], row["claim_bridge_id"] or "") for row in self._rows(
            "SELECT id, status, claim_bridge_id FROM dispatch_runs WHERE target_agent = ?", (agent_id,))}

    def _assert_held(self, agent_id: str, body: dict) -> None:
        self.assertEqual(body, {"ok": True, "run": None}, "the hold answers no run and nothing else")
        self.assertEqual(self._statuses(agent_id),
                         {f"r1-{agent_id}": ("queued", ""), f"canary-{agent_id}": ("queued", "")},
                         "a held claim must not reach selection: nothing in the queue may change")

    def _assert_r1_claimed(self, agent_id: str, body: dict) -> None:
        self.assertEqual((body.get("run") or {}).get("id"), f"r1-{agent_id}", body)
        self.assertEqual(self._statuses(agent_id),
                         {f"r1-{agent_id}": ("claimed", f"bridge-{agent_id}"), f"canary-{agent_id}": ("queued", "")})


class SteerBypassWitness(_ClaimWorld):
    """One world per bypass arm, each beside the all-true twin that is delivered into the turn."""

    def test_a_target_without_steer_is_held(self):
        self._world("arm-capability", steer=False)
        self._assert_held("arm-capability", self._claim("arm-capability"))
        self._world("arm-capability-twin")
        self._assert_r1_claimed("arm-capability-twin", self._claim("arm-capability-twin"))

    def test_an_explicit_queue_run_is_held_and_released_when_the_turn_ends(self):
        """The explicit queue means "after this turn": held while busy, and the SAME run, flag kept, is claimed
        once the turn is closed (the obligation of the removed regression
        `test_explicit_queue_to_busy_steerable_target_waits_for_turn_end`).

        The last step retries the ORIGINAL run after the held claim, rather than a fresh world: only that shows
        the held claim left the run exactly as it found it (a write on the held path that damaged the run would
        pass every fresh-world twin above it)."""
        self._world("arm-queue", queue_if_busy=1)
        self._assert_held("arm-queue", self._claim("arm-queue"))
        self._world("arm-queue-released", queue_if_busy=1, spec=BUSY.closed())
        self._assert_r1_claimed("arm-queue-released", self._claim("arm-queue-released"))
        self._world("arm-queue-twin")
        self._assert_r1_claimed("arm-queue-twin", self._claim("arm-queue-twin"))

        INPUT_ADAPTER.apply(self, "arm-queue", BUSY.closed())     # the turn ends; the queue is untouched
        self._assert_r1_claimed("arm-queue", self._claim("arm-queue"))
        flag = self._rows("SELECT queue_if_busy FROM dispatch_runs WHERE id = 'r1-arm-queue'")[0]["queue_if_busy"]
        self.assertEqual(flag, 1, "the explicit-queue flag is kept on the run that was claimed")

    def test_a_run_that_does_not_ask_to_steer_is_held(self):
        self._world("arm-steer-flag", steer_if_busy=0)
        self._assert_held("arm-steer-flag", self._claim("arm-steer-flag"))
        self._world("arm-steer-flag-twin")
        self._assert_r1_claimed("arm-steer-flag-twin", self._claim("arm-steer-flag-twin"))

    def test_a_managed_mode_run_is_held(self):
        self._world("arm-mode", execution_mode="managed")
        self._assert_held("arm-mode", self._claim("arm-mode"))
        self._world("arm-mode-twin")
        self._assert_r1_claimed("arm-mode-twin", self._claim("arm-mode-twin"))

    def test_a_run_in_a_mode_this_bridge_does_not_claim_is_held(self):
        self._world("arm-supported", execution_mode="channel")
        self._assert_held("arm-supported", self._claim("arm-supported"))
        self._world("arm-supported-twin")
        self._assert_r1_claimed("arm-supported-twin", self._claim("arm-supported-twin"))

    def test_a_run_for_another_runtime_is_held(self):
        self._world("arm-runtime", requested_runtime="claude-code")
        self._assert_held("arm-runtime", self._claim("arm-runtime"))
        self._world("arm-runtime-twin")
        self._assert_r1_claimed("arm-runtime-twin", self._claim("arm-runtime-twin"))


class ExplicitQueueSkipWitness(_ClaimWorld):
    """Selection under the hold (`claim_run_selection`): with a steerable run r1 queued, a busy claim delivers r1
    and skips an EARLIER run r0 that must wait for the turn to end. r0 waits for either of two reasons, one
    witness each: it is an explicit queue (steering otherwise allowed), or it does not ask to steer. Each twin is
    the same queue, r0's flag kept, with the turn closed: r0 is delivered first."""

    def _two_runs(self, agent_id: str, spec: TurnSpecimen, **r0) -> None:
        self._world(agent_id, spec=spec)                                  # r1 steerable, at 2001
        self._queue(f"r0-{agent_id}", agent_id, at="2000-01-01T00:00:00Z", **r0)

    def _assert_skips_then_releases(self, name: str, **r0) -> None:
        self._two_runs(name, BUSY, **r0)
        body = self._claim(name)
        self.assertEqual(body["run"]["id"], f"r1-{name}", body)
        self.assertEqual(self._statuses(name)[f"r0-{name}"], ("queued", ""), "r0 waits until the turn ends")
        self.assertEqual(self._statuses(name)[f"r1-{name}"], ("claimed", f"bridge-{name}"))

        twin = f"{name}-released"
        self._two_runs(twin, BUSY.closed(), **r0)
        body = self._claim(twin)
        self.assertEqual(body["run"]["id"], f"r0-{twin}", body)
        self.assertEqual(self._statuses(twin)[f"r0-{twin}"], ("claimed", f"bridge-{twin}"))

    def test_an_earlier_explicit_queue_run_waits_and_is_taken_once_the_turn_closes(self):
        self._assert_skips_then_releases("skip-queue", queue_if_busy=1, steer_if_busy=1)

    def test_an_earlier_run_that_does_not_ask_to_steer_waits_and_is_taken_once_the_turn_closes(self):
        self._assert_skips_then_releases("skip-steer", queue_if_busy=0, steer_if_busy=0)
