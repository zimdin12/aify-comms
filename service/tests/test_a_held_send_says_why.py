"""A send whose run is queued with nothing that will start it says so, and why (SW5 review R3).

After the 0.9.8 switch an undefined agent aify-env holds no record of reads `unknown`, which no send refuses: on a
native-managed route the run is queued for the claim gate and no cold start is attempted (D8: nothing starts an
undefined agent). The reviewer's parent run showed the answer saying nothing at all: no notStarted, no notice. The
operator's rule of 2026-10-16 is that the sender is told what happened to the wake, so the answer now lists it under
`held`, with the run that waits and why, and the notice says it. It is NOT listed under notStarted: that list means
"stored without a run", and the run is kept.

Controls: a defined agent with no record keeps its run and is not held (its host starts it, page 1); an agent
aify-env reports offline is stored unwoken, as the 10-16 rule always said.
"""
from __future__ import annotations

import sqlite3

from service.api_core import switched_view
from service.tests import env_records
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define

MACHINE = "linux:test-host"
ENV_ID = f"{MACHINE}:default"


class AHeldSendSaysWhy(FastApiTestCase):
    # The reviewer's route: console insertion off and codex NOT wrapper-backed, so the backing loop reaches the native
    # managed branch, which leaves the run for the native adapter's claim and attempts no cold start.
    LEGACY_SETTINGS = {"insert_messages_via_console": False, "managed_via_wrapper": ["hermes"]}
    def setUp(self):
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV_ID, "machineId": MACHINE, "os": "linux", "kind": "linux", "bridgeId": "bridge-current",
            "cwdRoots": ["/workspace"], "metadata": {},
            "runtimes": [{"runtime": "codex", "modes": ["managed-warm"], "capabilities": {"nativeResume": True}}]})
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        self._register("lead")

    def _register(self, agent_id: str) -> None:
        answer = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "codex", "sessionMode": "managed", "machineId": MACHINE,
            "bridgeId": "bridge-current", "capabilities": ["managed-run", "resume", "interrupt"]})
        self.assertEqual(answer.status_code, 200, answer.text)

    def _send(self, to: str) -> dict:
        answer = self.client.post("/api/v1/messages/send", json={
            "from_agent": "lead", "to": to, "type": "request", "subject": "work", "body": "please", "trigger": True})
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def _runs(self, agent_id: str) -> list:
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute("SELECT id, status FROM dispatch_runs WHERE target_agent = ?", (agent_id,)).fetchall()
        finally:
            conn.close()

    def test_an_undefined_recordless_recipient_is_queued_and_the_answer_says_it_is_held_and_why(self):
        self._register("undefined-coder")
        sent = self._send("undefined-coder")
        [(run_id, status)] = self._runs("undefined-coder")
        self.assertEqual(status, "queued", "the message's run is kept")
        self.assertEqual(sent["notStarted"], [], "notStarted means no run; this one has a run")
        self.assertEqual(sent["held"], [{"targetAgentId": "undefined-coder", "runId": run_id,
                                         "reason": switched_view.HELD_RECORDLESS}])
        self.assertIn("undefined-coder was not woken and its message is held", sent["notice"])

    def test_CONTROL_a_defined_recordless_recipient_keeps_its_run_and_is_not_held(self):
        self._register("defined-coder")
        define(self, "defined-coder", environment_id=ENV_ID, machine_id=MACHINE, bridge_id="bridge-current",
               runtime="codex", workspace="/workspace")
        sent = self._send("defined-coder")
        self.assertEqual((sent["held"], sent["notStarted"]), ([], []), sent)
        self.assertEqual(len(self._runs("defined-coder")), 1)

    def test_CONTROL_a_recipient_aify_env_reports_offline_is_stored_unwoken(self):
        self._register("offline-coder")
        env_records.report(self, "offline-coder", state="offline")
        sent = self._send("offline-coder")
        self.assertEqual((sent["held"], self._runs("offline-coder")), ([], []))
        self.assertEqual([h["targetAgentId"] for h in sent["notStarted"]], ["offline-coder"])
        self.assertIn("Wake failed for offline-coder", sent["notice"])
