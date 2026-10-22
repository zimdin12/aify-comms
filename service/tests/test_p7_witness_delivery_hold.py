"""P7 witnesses: the send-time queue (ledger B9) and the claim hold (B17), over every turn specimen.

Both consumers asked `_turn_busy_holds_delivery` before the 0.9.8 switch; after it they read the operational view
(eligibility first, then aify-env's `busy`). These drive the shipping consumers, not the helper: `POST /messages/send` with `queueIfBusy` (whose launch loop
is `dispatch_launch._launch_recipients_for_dispatch`) and `POST /dispatch/claim`. The helper's own law is
unit-tested in `test_turn_busy_delivery_ceiling.py`; what is pinned here is what each consumer DOES with it.

Every specimen runs beside its twin: the same facts with the turn closed, in an otherwise identical world. A
held case passes only if its twin really is delivered, so a hold cannot be explained by some other gate that
makes the target unreachable. Outcomes are read back from the database, not from the response alone.

The expected answer comes from `delivery_witnesses.HOLD_LAW` under the adapter's law. Where P-1 changes it,
the specimen says so there.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from service.api_core import dispatch_launch, operational_view
from service.api_core.liveness import TURN_BUSY_BACKSTOP_SECONDS
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define
from service.tests.delivery_witnesses import HOLD_LAW, INPUT_ADAPTER, P1_CHANGES, STRICT, TurnSpecimen, p1_holds

ENV_ID = "linux:test-host:default"
MACHINE = "linux:test-host"


class _World(FastApiTestCase):
    """Database helpers both consumers' witnesses share."""

    def _rows(self, query: str, params=()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(query, params).fetchall())
        finally:
            conn.close()

    def _exec(self, query: str, params=()) -> None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(query, params)
            conn.commit()
        finally:
            conn.close()

    def _register(self, agent_id: str, **fields) -> None:
        answer = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder", **fields})
        self.assertEqual(answer.status_code, 200, answer.text)

    def _agent_names(self, spec: TurnSpecimen) -> tuple[str, str]:
        slug = spec.name.replace("/", "-")
        return f"w-{slug}", f"w-{slug}-twin"


class TheLawTableMeansWhatItSays(unittest.TestCase):
    """The table is the oracle, so its meaning is checked, not just its use."""

    def test_the_subjects_strict_timer_is_the_one_the_specimens_were_frozen_against(self):
        """The fixture's window is a literal so the specimens cannot follow a timer edit. This is the one place
        the two meet: if the service's timer moves, this fails by name instead of the matrix moving with it."""
        self.assertEqual(TURN_BUSY_BACKSTOP_SECONDS, STRICT)

    def test_the_P1_column_is_the_rulings_rule_applied_to_the_facts(self):
        for case in HOLD_LAW:
            with self.subTest(case.spec.name):
                self.assertEqual(case.p1, p1_holds(case.spec), case.why)

    def test_P1_changes_exactly_the_specimens_the_operator_approved(self):
        changed = {case.spec.name for case in HOLD_LAW if case.legacy != case.p1}
        self.assertEqual(changed, P1_CHANGES)
        for case in HOLD_LAW:
            if case.spec.name in P1_CHANGES:
                self.assertIn("P-1 CHANGES THIS", case.why, "a changed answer must say so where it is written")


class SendTimeQueueWitness(_World):
    """B9: a `queueIfBusy` send to a held target is stored and waits, and still ensures its backing.

    The world: a defined managed claude whose sessions are all dead, on an online environment, so the eligible
    action is a cold-start spawn request (test_dispatch_claude_coldstart.py proves that path on its own). The
    other two terms of the queue decision, an active run and an earlier queued run, are false for every
    specimen and asserted false before the send, so only the turn decides.

    SW4 (the hard-switch plan's ordering rule): a hold withholds delivery and never skips backing, because a
    recipient with no worker never publishes the record that would release its hold. So held and released
    sends here both cold-start and both leave the run queued; the claim (B17) delivers. The hold's decision is
    read from the answer the launch loop got from `operational_view.send_time_hold`, and its one send-time
    consequence, no console delivery, is witnessed in test_a_held_send_still_ensures_backing.py, where a console
    exists.
    """

    def setUp(self):
        super().setUp()
        answer = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV_ID, "label": "Linux on test-host", "machineId": MACHINE, "os": "linux", "kind": "linux",
            "bridgeId": "bridge-current", "cwdRoots": ["/workspace"], "metadata": {},
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"],
                          "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True}}]})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.cold_starts: list[str] = []
        self.pty_ensures: list[str] = []
        self.queue_holds: list[bool] = []
        real_hold = operational_view.send_time_hold

        def observe(view, queue_if_busy, occupied):
            held = real_hold(view, queue_if_busy, occupied)
            self.queue_holds.append(held)
            return held

        observer = mock.patch.object(operational_view, "send_time_hold", new=observe)
        observer.start()
        self.addCleanup(observer.stop)
        real_cold, real_pty = (dispatch_launch._coldstart_spawn_request_for_dispatch,
                               dispatch_launch._ensure_managed_pty_for_dispatch)

        async def cold(db, agent_id, **kw):
            self.cold_starts.append(agent_id)
            return await real_cold(db, agent_id, **kw)

        async def pty(db, agent_id, **kw):
            self.pty_ensures.append(agent_id)
            return await real_pty(db, agent_id, **kw)

        for name, spy in (("_coldstart_spawn_request_for_dispatch", cold), ("_ensure_managed_pty_for_dispatch", pty)):
            patcher = mock.patch.object(dispatch_launch, name, new=spy)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _dead_managed_claude(self, agent_id: str) -> None:
        self._register(agent_id, runtime="claude-code", sessionMode="managed", machineId=MACHINE,
                       bridgeId="bridge-current", capabilities=["managed-run", "resume", "interrupt"],
                       runtimeConfig={"channelEnabled": True})
        self._exec(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode, owner_mode, "
            "terminal_id, terminal_status, status, started_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"session-{agent_id}", agent_id, ENV_ID, "claude-code", "/workspace", "managed-warm", "managed",
             "", "", "stopped", "2026-06-12T00:00:00Z", "2026-06-12T00:00:00Z"))
        define(self, agent_id, environment_id=ENV_ID, machine_id=MACHINE, bridge_id="bridge-current",
               runtime="claude-code", workspace="/workspace")

    def _queue_send(self, agent_id: str, spec: TurnSpecimen) -> dict:
        self._dead_managed_claude(agent_id)
        INPUT_ADAPTER.apply(self, agent_id, spec)
        self.assertEqual(self._rows("SELECT id FROM dispatch_runs WHERE target_agent = ?", (agent_id,)), [],
                         "precondition: no active and no earlier queued run, so only the turn can hold")
        observed = len(self.queue_holds)
        answer = self.client.post("/api/v1/messages/send", json={
            "from_agent": "dashboard", "to": agent_id, "trigger": True, "queueIfBusy": True,
            "type": "info", "subject": f"queued for {agent_id}", "body": "after this turn"})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json().get("notStarted") or [], [], answer.json())
        runs = self._rows("SELECT id, status, queue_if_busy FROM dispatch_runs WHERE target_agent = ?", (agent_id,))
        self.assertEqual(len(runs), 1, "the send is stored as exactly one run for this recipient")
        self.assertEqual((runs[0]["status"], runs[0]["queue_if_busy"]), ("queued", 1))
        self.assertEqual(len(self.queue_holds), observed + 1, "the launch loop decides the queue once per send")
        return {"held": self.queue_holds[-1],
                "spawns": self._rows("SELECT id, runtime, environment_id FROM spawn_requests WHERE agent_id = ?",
                                     (agent_id,))}

    def _assert_waits(self, agent_id: str, outcome: dict) -> None:
        """Held: the queue observation says so, the run waits queued (`_queue_send`), and backing is ensured."""
        self.assertTrue(outcome["held"], "the send is held")
        self._assert_woken(agent_id, outcome)

    def _assert_woken(self, agent_id: str, outcome: dict) -> None:
        self.assertIn(agent_id, self.cold_starts)
        self.assertEqual([(s["runtime"], s["environment_id"]) for s in outcome["spawns"]], [("claude-code", ENV_ID)],
                         "the eligible twin must really be woken: one spawn request, on this environment")

    def test_each_specimen_holds_or_releases_the_queue_as_its_law_says(self):
        for case in HOLD_LAW:
            with self.subTest(case.spec.name):
                held_agent, twin_agent = self._agent_names(case.spec)
                outcome = self._queue_send(held_agent, case.spec)
                twin = self._queue_send(twin_agent, case.spec.closed())
                self.assertFalse(twin["held"], "the control: the closed twin is not held")
                self._assert_woken(twin_agent, twin)
                if case.expected(INPUT_ADAPTER.LAW):
                    self._assert_waits(held_agent, outcome)
                else:
                    self.assertFalse(outcome["held"])
                    self._assert_woken(held_agent, outcome)

    def test_an_active_run_or_an_earlier_queued_run_also_holds_the_queue(self):
        """The two other terms of B9, which P7 reads as `runOccupied` and queued runs (C6), with the turn closed.

        A worker that is already being started (a queued or running prior run) is reached through the same
        idempotent backing; the hold itself is read from the queue observation."""
        closed = TurnSpecimen("closed", open=False)
        for status in ("running", "queued"):
            with self.subTest(status):
                agent, twin = f"w-occupied-{status}", f"w-occupied-{status}-twin"
                self._dead_managed_claude(agent)
                INPUT_ADAPTER.apply(self, agent, closed)
                self._exec(
                    "INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, "
                    "message_type, subject, body, priority, status, requested_at, started_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
                    (f"run-prior-{agent}", "dashboard", agent, "start_if_possible", "channel", "info", "prior",
                     "prior", "normal", status))
                answer = self.client.post("/api/v1/messages/send", json={
                    "from_agent": "dashboard", "to": agent, "trigger": True, "queueIfBusy": True,
                    "type": "info", "subject": "behind the prior run", "body": "wait"})
                self.assertEqual(answer.status_code, 200, answer.text)
                self.assertTrue(self.queue_holds[-1], "an occupied target holds the queue")
                self.assertIn(agent, self.cold_starts, "the hold still ensures backing")
                twin_outcome = self._queue_send(twin, closed)
                self.assertFalse(twin_outcome["held"])
                self._assert_woken(twin, twin_outcome)


class ClaimHoldWitness(_World):
    """B17: a claim for a held target that cannot be steered returns no run and leaves the queue alone.

    The world: a resident codex whose capabilities lack `steer`, so the B19 bypass cannot apply, with one
    queued resident run and, behind it, a canary: a steerable `managed` run this resident cannot execute. The
    run alone cannot tell the hold's early return from selection skipping an unsteerable run (both answer no
    run); a claim that reaches selection cancels the canary as a mode mismatch, so the canary can. The held
    answer must be the bare `{run: None}` of the hold, not a `blockedBy`, `stopped` or `release` answer from
    another gate, with both runs untouched.
    """

    def _resident_codex(self, agent_id: str) -> None:
        self._register(agent_id, runtime="codex", sessionMode="resident", machineId=MACHINE,
                       bridgeId=f"bridge-{agent_id}", sessionHandle=f"thread-{agent_id}",
                       capabilities=["resident-run", "resume", "interrupt"],
                       runtimeConfig={"appServerUrl": "ws://127.0.0.1:9"})
        self._exec(
            "INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, message_type, "
            "subject, body, priority, status, queue_if_busy, steer_if_busy, requested_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'queued',0,0,datetime('now'))",
            (f"run-{agent_id}", "dashboard", agent_id, "start_if_possible", "resident", "info", "work", "work",
             "normal"))
        self._exec(
            "INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, message_type, "
            "subject, body, priority, status, queue_if_busy, steer_if_busy, requested_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'queued',0,1,datetime('now', '+1 minute'))",
            (f"canary-{agent_id}", "dashboard", agent_id, "start_if_possible", "managed", "info", "canary", "canary",
             "normal"))

    def _claim(self, agent_id: str, spec: TurnSpecimen) -> dict:
        self._resident_codex(agent_id)
        INPUT_ADAPTER.apply(self, agent_id, spec)
        answer = self.client.post("/api/v1/dispatch/claim", json={
            "agentId": agent_id, "bridgeId": f"bridge-{agent_id}", "machineId": MACHINE,
            "executionModes": ["resident", "managed"]})
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def _assert_held(self, agent_id: str, body: dict) -> None:
        self.assertEqual(body, {"ok": True, "run": None}, "the hold answers no run and nothing else")
        rows = self._rows("SELECT id, status, claim_bridge_id FROM dispatch_runs WHERE target_agent = ?", (agent_id,))
        self.assertEqual({r["id"]: (r["status"], r["claim_bridge_id"] or "") for r in rows},
                         {f"run-{agent_id}": ("queued", ""), f"canary-{agent_id}": ("queued", "")},
                         "a held claim must not reach selection: nothing in the queue may change")

    def _assert_claimed(self, agent_id: str, body: dict) -> None:
        self.assertEqual((body.get("run") or {}).get("id"), f"run-{agent_id}", body)
        rows = self._rows("SELECT id, status, claim_bridge_id FROM dispatch_runs WHERE target_agent = ?", (agent_id,))
        self.assertEqual({r["id"]: (r["status"], r["claim_bridge_id"] or "") for r in rows},
                         {f"run-{agent_id}": ("claimed", f"bridge-{agent_id}"), f"canary-{agent_id}": ("queued", "")})

    def test_each_specimen_holds_or_releases_the_claim_as_its_law_says(self):
        for case in HOLD_LAW:
            with self.subTest(case.spec.name):
                held_agent, twin_agent = self._agent_names(case.spec)
                body = self._claim(held_agent, case.spec)
                self._assert_claimed(twin_agent, self._claim(twin_agent, case.spec.closed()))
                if case.expected(INPUT_ADAPTER.LAW):
                    self._assert_held(held_agent, body)
                else:
                    self._assert_claimed(held_agent, body)
