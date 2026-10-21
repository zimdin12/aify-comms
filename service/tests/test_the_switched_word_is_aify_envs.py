"""After the 0.9.8 switch every reader asks the operational view (plan 2026-10-20-s4-hard-switch, SW5).

Witnesses of the readers SW5 rewired, each driven through the route or entry point that serves it, and each red on
the tree before the switch (36028554), where the same readers answered from comms' own derivation:

  * the word: aify-env's record decides it; no record reads `unknown` with why; a stale record reads `unknown` with
    A7's `lastKnown` beside it; an operator stop of an undefined agent reads `stopped` (D8 row 1);
  * the cache: an applied publication expires the words it changed, and a cached word built from a fresh record is
    re-read once that record would go stale;
  * the claim gate: unknown withholds, a steerable run included; busy holds a run that cannot steer;
  * live counts: `unknown` is never counted live.

The truth table itself is unit-witnessed in test_the_operational_view_reads_env_busy_without_timing_turns.py, the
holds at every consumer in test_p7_witness_*.py, and the send-time hold in test_a_held_send_still_ensures_backing.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest import mock

from service.api_core import partial_status_shadow as shadow, switched_view
from service.clock import ISO_SECONDS
from service.db import get_db
from service.tests import env_records
from service.tests._base import FastApiTestCase
from service.tests.frozen_clock import frozen_service_clock

MACHINE = env_records.TEST_HOST


def _later(stamp: str, seconds: int) -> str:
    return (datetime.strptime(stamp, ISO_SECONDS) + timedelta(seconds=seconds)).strftime(ISO_SECONDS)


class _World(FastApiTestCase):
    def _register(self, agent_id: str, **fields) -> None:
        body = {"agentId": agent_id, "role": "coder", "machineId": MACHINE, **fields}
        answer = self.client.post("/api/v1/agents", json=body)
        self.assertEqual(answer.status_code, 200, answer.text)

    def _agent(self, agent_id: str) -> dict:
        answer = self.client.get(f"/api/v1/agents/{agent_id}")
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()["agent"]

    def _listed(self, agent_id: str) -> dict:
        answer = self.client.get("/api/v1/agents")
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()["agents"][agent_id]


class TheWordIsAifyEnvs(_World):
    def test_the_record_decides_the_word_on_both_reads(self):
        self._register("w-idle")
        self._register("w-busy")
        env_records.report(self, "w-idle")
        env_records.report(self, "w-busy", busy=True)
        self.assertEqual((self._agent("w-idle")["status"], self._listed("w-idle")["status"]), ("online", "online"))
        self.assertEqual((self._agent("w-busy")["status"], self._listed("w-busy")["status"]), ("working", "working"))

    def test_no_record_is_unknown_and_says_why_with_no_last_known(self):
        self._register("w-none", runtime="codex", sessionMode="managed")
        agent = self._agent("w-none")
        self.assertEqual((agent["status"], agent["statusNote"], agent["lastKnown"]),
                         ("unknown", "aify-env has no current record of this agent", None))

    def test_an_operator_stop_of_an_undefined_agent_reads_stopped(self):
        self._register("w-stop", runtime="codex", sessionMode="managed")
        env_records.report(self, "w-stop")
        stopped = self.client.post("/api/v1/agents/w-stop/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(self._agent("w-stop")["status"], "stopped")
        resumed = self.client.post("/api/v1/agents/w-stop/control", json={"action": "resume", "from_agent": "dashboard"})
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(self._agent("w-stop")["status"], "online", "resume hands the word back to aify-env at once")


class TheCachedWordFollowsThePublications(_World):
    def test_an_applied_publication_expires_the_cached_word_at_once(self):
        self._register("c-flip")
        env_records.report(self, "c-flip")
        self.assertEqual(self._agent("c-flip")["status"], "online")
        env_records.report(self, "c-flip", busy=True)
        self.assertEqual(self._agent("c-flip")["status"], "working", "the cached idle word outlived its publication")
        env_records.report(self, "c-flip")
        self.assertEqual(self._listed("c-flip")["status"], "online")

    def test_a_word_from_a_fresh_record_is_reread_once_the_record_goes_stale_and_keeps_it_as_last_known(self):
        self._register("c-stale")
        with frozen_service_clock() as reported_at:
            env_records.report(self, "c-stale")
            self.assertEqual(self._agent("c-stale")["status"], "online")
        silent = _later(reported_at, 3 * env_records.EVERY_MS // 1000 + 1)  # three missed intervals (decision 2)
        with frozen_service_clock(silent), mock.patch.object(shadow, "clock", return_value=silent):
            agent = self._agent("c-stale")
        self.assertEqual(agent["status"], "unknown", "a cached word outlived the record it was built from")
        self.assertEqual(agent["lastKnown"], {"status": "online", "appliedAt": reported_at}, "A7")
        self.assertTrue(agent["statusNote"].startswith("aify-env's state for this agent is not current"),
                        agent["statusNote"])


    def test_a_cached_word_from_a_fresh_record_is_due_no_later_than_that_record_goes_stale(self):
        """Nothing marks the moment a record goes stale, so the cached word must ask again by then by itself, whatever
        later time the legacy inputs would have allowed."""
        self._register("c-due")
        with frozen_service_clock() as reported_at:
            env_records.report(self, "c-due")

            async def word():
                db = await get_db()
                try:
                    row = await (await db.execute("SELECT * FROM agents WHERE id = ?", ("c-due",))).fetchone()
                    return await switched_view.word_into(db, row, {"status": "online",
                                                                   "refresh_after": "2999-01-01T00:00:00Z"})
                finally:
                    await db.close()

            cache = asyncio.run(word())
        self.assertEqual(cache["refresh_after"], _later(reported_at, 3 * env_records.EVERY_MS // 1000))


class AReadabilityChangeReachesEveryAgentOnItsMachine(_World):
    """SW5 review R2, the reviewer's default/second history: a publisher's readability decides every agent on its
    machine, held by it or not, because an unreadable publisher could hold any of them unseen. The cached word of an
    agent the changing publisher does not name must follow, in both directions."""

    def _two_publishers(self):
        self._register("d-a")
        self._register("d-b")
        env_records.report(self, "d-a")                       # the default publisher holds A
        env_records.report(self, "d-b", instance="second")    # a second publisher on the same machine holds B
        self.assertEqual((self._agent("d-a")["status"], self._listed("d-a")["status"]), ("online", "online"))

    def test_a_second_publisher_going_unavailable_and_retiring_moves_a_word_it_never_held(self):
        self._two_publishers()
        env_records.publish_state(self, MACHINE, "second", "unavailable", "cannot read its sessions")
        self.assertEqual((self._agent("d-a")["status"], self._listed("d-a")["status"]), ("unknown", "unknown"),
                         "A's cached word outlived the second publisher becoming unreadable")
        env_records.publish_state(self, MACHINE, "second", "retired", "shutdown")
        self.assertEqual((self._agent("d-a")["status"], self._listed("d-a")["status"]), ("online", "online"),
                         "A's cached unknown outlived the unreadable publisher's retirement")

    def test_CONTROL_the_same_holder_changing_moves_the_word(self):
        self._two_publishers()
        env_records.report(self, "d-a", busy=True)
        self.assertEqual(self._listed("d-a")["status"], "working")

    def test_CONTROL_a_publisher_on_another_machine_changes_nothing_here(self):
        self._two_publishers()
        other = "other-host"
        self.client.post("/api/v1/agents", json={"agentId": "d-elsewhere", "role": "coder", "machineId": other})
        env_records.report(self, "d-elsewhere")
        env_records.publish_state(self, other, env_records.INSTANCE, "unavailable", "cannot read its sessions")
        self.assertEqual((self._agent("d-a")["status"], self._listed("d-a")["status"]), ("online", "online"))
        self.assertEqual(self._agent("d-elsewhere")["status"], "unknown", "the control: the other machine's agent moved")


class TheClaimGateReadsEligibilityFirst(_World):
    """A resident codex with `steer` and one queued steerable resident run: the run a busy agent may be steered with."""

    def _resident(self, agent_id: str, *, steer: bool = True) -> None:
        self._register(agent_id, runtime="codex", sessionMode="resident", bridgeId=f"bridge-{agent_id}",
                       sessionHandle=f"thread-{agent_id}",
                       capabilities=["resident-run", "resume", "interrupt"] + (["steer"] if steer else []),
                       runtimeConfig={"appServerUrl": "ws://127.0.0.1:9"})
        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, message_type, "
                "subject, body, priority, status, queue_if_busy, steer_if_busy, requested_at) "
                "VALUES (?, 'dashboard', ?, 'start_if_possible', 'resident', 'info', 's', 'b', 'normal', 'queued', 0, 1,"
                " datetime('now'))", (f"run-{agent_id}", agent_id))
            conn.commit()
        finally:
            conn.close()

    def _claim(self, agent_id: str) -> dict:
        answer = self.client.post("/api/v1/dispatch/claim", json={
            "agentId": agent_id, "bridgeId": f"bridge-{agent_id}", "machineId": MACHINE,
            "executionModes": ["resident"]})
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def test_unknown_withholds_even_a_steerable_run_and_a_fresh_idle_record_releases_it(self):
        self._resident("g-unknown")
        self.assertEqual(self._claim("g-unknown"), {"ok": True, "run": None}, "unknown must withhold before steer")
        env_records.report(self, "g-unknown")
        self.assertEqual((self._claim("g-unknown").get("run") or {}).get("id"), "run-g-unknown")

    def test_busy_steers_a_steerable_run_and_holds_one_that_cannot_steer(self):
        self._resident("g-steer")
        self._resident("g-hold", steer=False)
        env_records.report(self, "g-steer", "g-hold", busy=True)
        self.assertEqual((self._claim("g-steer").get("run") or {}).get("id"), "run-g-steer")
        self.assertEqual(self._claim("g-hold"), {"ok": True, "run": None})


class UnknownIsNeverCountedLive(_World):
    def test_the_live_count_counts_aify_envs_live_words_and_not_unknown(self):
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": f"{MACHINE}:default", "machineId": MACHINE, "os": "linux", "kind": "linux", "bridgeId": "bridge-e",
            "cwdRoots": ["/workspace"], "metadata": {},
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}]})
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        for agent_id in ("l-reported", "l-silent"):
            self._register(agent_id, runtime="claude-code", sessionMode="managed", bridgeId="bridge-e",
                           capabilities=["managed-run"])
        env_records.report(self, "l-reported")
        self.assertEqual(self._agent("l-silent")["status"], "unknown", "the control: a managed agent with no record")
        for path in ("/api/v1/analytics", "/api/v1/analytics/pulse"):
            with self.subTest(path):
                self.assertEqual(self.client.get(path).json()["onlineAgents"], 1)
