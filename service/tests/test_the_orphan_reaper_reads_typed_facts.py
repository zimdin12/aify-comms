"""The orphaned-run reaper decides from the operational view's typed facts, never its displayed word (SW5 review R1).

The reviewer's histories (REVISE_SOURCE_SW5_CE04D801, PARENT-OBSERVATIONS-v5, the orphan rows), each through the real
selection and the whole reconciler body, `_close_orphaned_managed_runs`, over a run that the selection takes (running,
no owning bridge, no progress for 45 minutes):

  * fresh idle: row 5 promotes the word to `working` BECAUSE of this run, and the word-reading reaper protected the
    run from itself forever. Known idle is the one state in which the run's lack of progress is its own: it is failed;
  * fresh busy beside a stale holder: eligibility is unknown and busy is true. The word read `unknown`, and the run
    was failed and its legacy turn cleared. It is protected;
  * no record at all: unknown. It was failed; it is protected (decision R3's rule: never sooner);
  * fresh busy, and a target aify-env reports offline: the controls, unchanged.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

from service.clock import ISO_SECONDS
from service.db import get_db
from service.reconcilers.orphaned_managed_runs import _close_orphaned_managed_runs
from service.tests import env_records
from service.tests._base import FastApiTestCase
from service.tests.frozen_clock import frozen_service_clock

AGED = 45  # minutes: past the no-progress window and the wall-clock ceiling


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(ISO_SECONDS)


class TheOrphanReaperReadsTypedFacts(FastApiTestCase):
    def _target(self, agent_id: str) -> None:
        answer = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "hermes", "sessionMode": "managed",
            "machineId": env_records.TEST_HOST, "capabilities": ["managed-run"]})
        self.assertEqual(answer.status_code, 200, answer.text)
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, message_type, "
                "subject, body, priority, status, require_reply, requested_at, claimed_at, started_at, claim_bridge_id) "
                "VALUES (?, 'dashboard', ?, 'start_if_possible', 'managed', 'request', 'long', 'b', 'normal', 'running',"
                " 1, ?, ?, ?, '')", (f"run-{agent_id}", agent_id, _ago(AGED), _ago(AGED), _ago(AGED)))
            conn.execute("INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, "
                         "turn_updated_at) VALUES (?, 1, ?, '', 'hermes', ?)", (agent_id, f"run-{agent_id}", _ago(1)))
            conn.commit()
        finally:
            conn.close()

    def _reap(self, agent_id: str) -> tuple:
        async def run():
            db = await get_db()
            try:
                closed = await _close_orphaned_managed_runs(db, limit=50)
                await db.commit()
                return closed
            finally:
                await db.close()
        closed = [c["runId"] for c in asyncio.run(run()) if c["agentId"] == agent_id]
        conn = sqlite3.connect(str(self._db_path))
        try:
            status, error = conn.execute("SELECT status, COALESCE(error_text, '') FROM dispatch_runs WHERE id = ?",
                                         (f"run-{agent_id}",)).fetchone()
            [turn_busy] = conn.execute("SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", (agent_id,)).fetchone()
        finally:
            conn.close()
        return closed, status, error, turn_busy

    def _assert_protected(self, agent_id: str) -> None:
        self.assertEqual(self._reap(agent_id), ([], "running", "", 1), "the run must be left for a later cycle")

    def test_a_fresh_idle_target_does_not_protect_its_own_stuck_run_through_row_5(self):
        self._target("o-idle")
        env_records.report(self, "o-idle")
        self.assertEqual(self.client.get("/api/v1/agents/o-idle").json()["agent"]["status"], "working",
                         "CONTROL: the displayed word is row 5's promotion by this very run")
        closed, status, error, turn_busy = self._reap("o-idle")
        self.assertEqual((closed, status, turn_busy), (["run-o-idle"], "failed", 0))
        self.assertIn("is idle (aify-env reports no open turn)", error)

    def test_fresh_busy_beside_a_stale_holder_is_protected(self):
        self._target("o-mixed")
        with frozen_service_clock((datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(ISO_SECONDS)):
            env_records.report(self, "o-mixed", instance="old-env")  # three intervals ago and more: stale
        env_records.report(self, "o-mixed", busy=True)
        self._assert_protected("o-mixed")

    def test_no_record_is_protected(self):
        self._target("o-none")
        self._assert_protected("o-none")

    def test_CONTROL_fresh_busy_is_protected(self):
        self._target("o-busy")
        env_records.report(self, "o-busy", busy=True)
        self._assert_protected("o-busy")

    def test_CONTROL_a_target_aify_env_reports_offline_is_failed_naming_it(self):
        self._target("o-off")
        env_records.report(self, "o-off", state="offline")
        closed, status, error, turn_busy = self._reap("o-off")
        self.assertEqual((closed, status, turn_busy), (["run-o-off"], "failed", 0))
        self.assertIn("is offline; run cannot be delivered", error)
