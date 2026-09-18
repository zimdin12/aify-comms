"""A managed agent's status follows what its host sees on the worker's screen.

WHY. A managed agent's status came from hook events, the turn bookkeeping and the console lease. A
lost turn-end left an agent reading `working` while its screen sat at an idle prompt, and the Herdr
pane dot, reading that screen, was right. aify-env now evaluates Herdr's per-runtime screen rules
against the headless screen it keeps per PTY and reports `activity: {state, rule, observedAt}` on
its terminal liveness frames.

WHAT THESE PIN.
  - derive(): a FRESH observation for a managed agent with a live worker decides the live states --
    working -> working, blocked -> blocked, idle -> online -- working and blocked ahead of `in_turn`,
    idle only when no turn is held (review 2026-09-15: delivery holds on that turn). Stale, absent, no
    worker, an unreachable environment or a disabled agent: exactly today's answer.
  - The route: a liveness frame carrying `activity` records it on the terminal and changes nothing
    else about the frame's semantics -- no status, no output, no sequence.
  - The whole path: the frame moves the status an API caller reads, and an observation that is no
    longer refreshed stops deciding it.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import sqlite3
import unittest

from service.api_core.host_activity import HOST_ACTIVITY_FRESH_SECONDS
from service.clock import now as _now
from service.db import get_db
from service.status_engine import StatusInputs, derive
from service.tests._base import FastApiTestCase


def _inputs(**kw) -> StatusInputs:
    base = dict(mode="managed", alive=True, in_turn=False, awaiting_input=False,
                worker_present=True, env_reachable=True, disabled=False,
                bridge_stale=False, has_live_session=True)
    base.update(kw)
    return StatusInputs(**base)


class DeriveReadsTheHostObservation(unittest.TestCase):
    def test_a_fresh_observation_maps_to_the_live_statuses(self):
        self.assertEqual(derive(_inputs(host_activity="working", host_activity_fresh=True)), "working")
        self.assertEqual(derive(_inputs(host_activity="blocked", host_activity_fresh=True)), "blocked")
        self.assertEqual(derive(_inputs(host_activity="idle", host_activity_fresh=True)), "online")
        self.assertEqual(derive(_inputs(host_activity="shell", host_activity_fresh=True)), "shell")

    def test_a_STALE_observation_changes_nothing(self):
        for state in ("working", "blocked", "idle", "shell"):
            for in_turn in (False, True):
                with self.subTest(state=state, in_turn=in_turn):
                    stale = derive(_inputs(host_activity=state, host_activity_fresh=False, in_turn=in_turn))
                    self.assertEqual(stale, derive(_inputs(in_turn=in_turn)))

    def test_an_ABSENT_observation_changes_nothing(self):
        self.assertEqual(derive(_inputs(host_activity="", host_activity_fresh=True)), "online")
        self.assertEqual(derive(_inputs(host_activity="", host_activity_fresh=True, in_turn=True)), "working")
        self.assertEqual(derive(_inputs(host_activity="unknown", host_activity_fresh=True)), "online")

    def test_a_positive_sighting_OUTRANKS_in_turn_and_an_idle_screen_does_not_end_one(self):
        """Working and blocked are seen, so they outrank the bookkeeping. An idle screen does NOT end a
        held turn: delivery waits on that turn, so `online` would show a free agent whose sends queue,
        and idle is also Herdr's answer when no rule matched at all."""
        self.assertEqual(derive(_inputs(in_turn=True, host_activity="idle", host_activity_fresh=True)), "working")
        self.assertEqual(
            derive(_inputs(in_turn=True, awaiting_input=True, host_activity="idle", host_activity_fresh=True)),
            "blocked")
        # CONTROL: with no turn held the same idle observation does decide, so the refusal above is the
        # held turn and not an idle report that never counts.
        self.assertEqual(derive(_inputs(in_turn=False, host_activity="idle", host_activity_fresh=True)), "online")
        self.assertEqual(
            derive(_inputs(in_turn=True, awaiting_input=True, host_activity="working", host_activity_fresh=True)),
            "working")
        self.assertEqual(derive(_inputs(in_turn=False, host_activity="blocked", host_activity_fresh=True)), "blocked")

    def test_a_background_shell_does_not_end_a_held_turn_either(self):
        """`shell` is an idle prompt that also shows background shells, so it confirms a finished turn
        exactly as `idle` does and never ends one the service still holds."""
        self.assertEqual(derive(_inputs(in_turn=True, host_activity="shell", host_activity_fresh=True)), "working")
        self.assertEqual(derive(_inputs(in_turn=False, host_activity="shell", host_activity_fresh=True)), "shell")

    def test_it_never_outranks_the_states_that_say_the_worker_cannot_be_doing_anything(self):
        fresh = dict(host_activity="working", host_activity_fresh=True)
        self.assertEqual(derive(_inputs(disabled=True, **fresh)), "stopped")
        self.assertEqual(derive(_inputs(env_reachable=False, **fresh)), "offline")
        self.assertEqual(derive(_inputs(worker_present=False, alive=False, **fresh)), "available")

    def test_a_RESIDENT_agent_ignores_it(self):
        resident = dict(mode="resident", worker_present=True, has_live_session=True, bridge_stale=False)
        self.assertEqual(derive(_inputs(**resident, host_activity="working", host_activity_fresh=True)),
                         derive(_inputs(**resident)))


def seed_observed_managed_agent(case) -> None:
    """A managed claude agent with a live worker: console terminal, session and channel sidecar.

    Shared with the cross-repo test, which replays aify-env's own frames against it.
    """
    beat = case.client.post("/api/v1/environments/heartbeat", json={
        "id": case.ENV, "kind": "linux", "os": "linux", "machineId": "linux:activity-host",
        "bridgeId": "bridge-1", "cwdRoots": ["/work"],
        "runtimes": [{"runtime": "claude-code", "available": True}],
        "metadata": {"bridgeStartedAt": "2026-09-14T05:00:00Z"},
    })
    case.assertEqual(beat.status_code, 200, beat.text)
    registered = case.client.post("/api/v1/agents", json={
        "agentId": case.AGENT, "role": "coder", "runtime": "claude-code",
        "sessionMode": "managed", "machineId": "linux:activity-host", "bridgeId": "bridge-1",
    })
    case.assertEqual(registered.status_code, 200, registered.text)
    fresh = _now()
    conn = sqlite3.connect(str(case._db_path))
    try:
        conn.execute(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, owner_mode,"
            " terminal_id, terminal_status, started_at, last_seen, workspace) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (case.SESSION, case.AGENT, case.ENV, "claude-code", "running", "console",
             case.TERMINAL, "attached", fresh, fresh, "/work"))
        conn.execute(
            "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, runtime,"
            " bridge_id, command, workspace, status, output, error, output_seq, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (case.TERMINAL, case.AGENT, case.SESSION, case.ENV, "claude-code", "bridge-1",
             "claude-aify --aify-agent sc-observed", "/work", "attached", "", "", 7, fresh, fresh))
        # A managed claude worker is LIVE only with its channel sidecar claiming beside the console
        # (`_worker_liveness_for`); without it the agent reads "console booting", not a worker.
        conn.execute(
            "INSERT INTO bridge_instances (id, agent_id, machine_id, runtime, session_mode, terminal_id,"
            " bridge_kind, registered_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
            ("sidecar-observed", case.AGENT, "linux:activity-host", "claude-code", "managed",
             case.TERMINAL, "channel-sidecar", fresh, fresh))
        conn.commit()
    finally:
        conn.close()


class AHostObservationReachesTheStatus(FastApiTestCase):
    DB_NAME = "aify-test-host-activity.db"
    ENV = "linux:activity-host:default"
    AGENT = "sc-observed"
    SESSION = "sess-observed"
    TERMINAL = "term-observed"

    def setUp(self):
        super().setUp()
        seed_observed_managed_agent(self)

    def _frame(self, activity=None, **extra):
        body = {"output": "", "bridgeId": "bridge-1", **extra}
        if activity is not None:
            body["activity"] = activity
        answer = self.client.post(f"/api/v1/terminals/{self.TERMINAL}/output", json=body)
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer

    def _row(self) -> dict:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return dict(conn.execute("SELECT * FROM terminal_sessions WHERE id = ?", (self.TERMINAL,)).fetchone())
        finally:
            conn.close()

    def _status(self) -> str:
        answer = self.client.get(f"/api/v1/agents/{self.AGENT}")
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()["agent"]["status"]

    def test_the_frame_records_the_observation_and_nothing_else_moves(self):
        before = self._row()
        self._frame({"state": "working", "rule": "osc_title_working", "observedAt": "2026-09-14T10:00:00.000Z"})
        after = self._row()
        self.assertEqual(after["activity_state"], "working")
        self.assertEqual(after["activity_rule"], "osc_title_working")
        self.assertEqual(after["activity_observed_at"], "2026-09-14T10:00:00.000Z")
        self.assertTrue(after["activity_reported_at"], "the service did not record when it heard it")
        for column in ("status", "output", "output_seq", "stopped_at", "bridge_id"):
            self.assertEqual(after[column], before[column], f"a liveness frame with activity moved {column}")

    def test_a_frame_WITHOUT_activity_keeps_the_observation_but_does_not_refresh_it(self):
        self._frame({"state": "blocked", "rule": "r", "observedAt": "2026-09-14T10:00:00.000Z"})
        heard = self._row()["activity_reported_at"]
        self._frame()
        row = self._row()
        self.assertEqual(row["activity_state"], "blocked")
        self.assertEqual(row["activity_reported_at"], heard,
                         "a frame that carried no observation was taken as a fresh one")

    def test_an_OLDER_observation_arriving_late_does_not_replace_a_newer_one(self):
        """A liveness frame and a transition can cross in flight. The host's `observedAt` orders them:
        a frame observed before the stored one is stale news, whatever order it arrived in."""
        self._frame({"state": "idle", "rule": "live_prompt_box", "observedAt": "2026-09-14T10:00:05.000Z"})
        self._frame({"state": "working", "rule": "osc_title_working", "observedAt": "2026-09-14T10:00:00.000Z"})
        row = self._row()
        self.assertEqual(row["activity_state"], "idle", "an observation older than the stored one replaced it")
        self.assertEqual(row["activity_observed_at"], "2026-09-14T10:00:05.000Z")
        # CONTROL: a newer one still lands.
        self._frame({"state": "working", "rule": "osc_title_working", "observedAt": "2026-09-14T10:00:06.000Z"})
        self.assertEqual(self._row()["activity_state"], "working")

    def test_an_unknown_state_is_not_recorded(self):
        self._frame({"state": "idle", "rule": "r", "observedAt": "2026-09-14T10:00:00.000Z"})
        self._frame({"state": "thinking", "rule": "r2", "observedAt": "2026-09-14T10:00:01.000Z"})
        self.assertEqual(self._row()["activity_state"], "idle")

    def test_activity_cannot_reopen_an_ended_terminal(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("UPDATE terminal_sessions SET status='failed', stopped_at=? WHERE id=?", (_now(), self.TERMINAL))
        conn.commit()
        conn.close()
        self._frame({"state": "working", "rule": "r", "observedAt": "2026-09-14T10:00:00.000Z"})
        self.assertEqual(self._row()["status"], "failed")

    def test_the_SERVED_status_expires_when_the_observation_would_go_stale(self):
        """A host that stops reporting sends no event, so the cached status must carry its own deadline
        -- or a stale `working` is served until some unrelated window runs out."""
        from service.api_core.status_inputs import _compute_live_status_cache

        self._frame({"state": "working", "rule": "osc_title_working", "observedAt": "2026-09-14T10:00:00.000Z"})
        heard = self._row()["activity_reported_at"]

        async def cached():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (self.AGENT,))).fetchone()
                return await _compute_live_status_cache(db, row)
            finally:
                await db.close()

        payload = asyncio.run(cached())
        self.assertTrue(payload["status_inputs"].host_activity_fresh, "CONTROL: the observation was not fresh")
        deadline = (_dt.datetime.fromisoformat(heard.replace("Z", "+00:00"))
                    + _dt.timedelta(seconds=HOST_ACTIVITY_FRESH_SECONDS + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertLessEqual(payload["refresh_after"], deadline,
                             "the served status outlives the observation it was derived from")

    def _expire_cached_status(self):
        async def expire():
            from service.reconcilers.status_cache import invalidate_agent_live_state
            db = await get_db()
            try:
                await invalidate_agent_live_state(db, self.AGENT)
            finally:
                await db.close()

        asyncio.run(expire())

    def test_THE_STATUS_FOLLOWS_the_observation_and_falls_back_when_it_goes_stale(self):
        # CONTROL: the fixture is a managed agent with a live worker, or the rows below prove nothing.
        self.assertEqual(self._status(), "online", "the fixture has no live worker, so nothing below can move")

        # No turn, no lease: the observation is the only thing that can say working or blocked.
        self._frame({"state": "working", "rule": "osc_title_working", "observedAt": "2026-09-14T10:00:00.000Z"})
        self.assertEqual(self._status(), "working")
        self._frame({"state": "blocked", "rule": "bash_permission_prompt", "observedAt": "2026-09-14T10:00:01.000Z"})
        self.assertEqual(self._status(), "blocked")
        self._frame({"state": "idle", "rule": "live_prompt_box", "observedAt": "2026-09-14T10:00:02.000Z"})
        self.assertEqual(self._status(), "online")

        # A HELD TURN: the bookkeeping says working while the screen is idle. The turn stands, because
        # delivery is still waiting on it; a positive sighting would move it, an idle screen does not.
        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            "INSERT INTO agent_status_state (agent_id, in_turn, awaiting_input, last_event_at, turn_started_at)"
            " VALUES (?,1,0,?,?)", (self.AGENT, _now(), _now()))
        conn.commit()
        conn.close()
        self._expire_cached_status()
        self.assertEqual(self._status(), "working", "an idle screen ended a turn delivery is still holding")
        self._frame({"state": "blocked", "rule": "bash_permission_prompt", "observedAt": "2026-09-14T10:00:03.000Z"})
        self.assertEqual(self._status(), "blocked", "a positive sighting did not outrank the held turn")

        # STALE: the host stopped refreshing it, so the turn bookkeeping decides again.
        old = (_dt.datetime.now(_dt.timezone.utc)
               - _dt.timedelta(seconds=HOST_ACTIVITY_FRESH_SECONDS + 5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("UPDATE terminal_sessions SET activity_reported_at=? WHERE id=?", (old, self.TERMINAL))
        conn.commit()
        conn.close()
        self._expire_cached_status()
        self.assertEqual(self._status(), "working", "a stale observation still decided the status")


if __name__ == "__main__":
    unittest.main()


class TheFleetRefreshReadsTheObservationInOneQuery(FastApiTestCase):
    """The reconcile sweep reads the observation for every due agent at once, and answers exactly
    what the per-agent query answers. A per-agent read there costs a round-trip per agent on every
    pass (`test_the_live_state_refresh_holds_its_per_agent_cost.py`)."""

    DB_NAME = "aify-test-host-activity-prefetch.db"
    ENV = "linux:activity-host:default"
    AGENT = "sc-observed"
    SESSION = "sess-observed"
    TERMINAL = "term-observed"

    def test_the_prefetch_and_the_query_agree(self):
        from service.api_core.host_activity import host_activity_for
        from service.api_core.status_signal_prefetch import PrefetchedStatusSignals

        seed_observed_managed_agent(self)
        fresh = _now()
        older = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        newer = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("UPDATE terminal_sessions SET activity_state='working', activity_reported_at=? WHERE id=?",
                         (older, self.TERMINAL))
            columns = ("id, agent_id, session_id, environment_id, runtime, bridge_id, command, workspace, status,"
                       " output, error, output_seq, created_at, updated_at, activity_state, activity_reported_at")
            rows = [
                # The NEWEST active one wins over the older active one...
                ("term-second", "blocked", "attached", fresh),
                # ...and an ended terminal never counts, however recent.
                ("term-ended", "idle", "stopped", newer),
            ]
            for tid, state, status, heard in rows:
                conn.execute(f"INSERT INTO terminal_sessions ({columns}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (tid, self.AGENT, self.SESSION, self.ENV, "claude-code", "bridge-1", "claude-aify",
                              "/work", status, "", "", 0, fresh, fresh, state, heard))
            conn.commit()
        finally:
            conn.close()

        async def both():
            db = await get_db()
            try:
                ids = [self.AGENT, "nobody-observed"]
                pre = await PrefetchedStatusSignals.load(db, ids)
                return [(await host_activity_for(db, aid), await host_activity_for(db, aid, status_signals=pre))
                        for aid in ids]
            finally:
                await db.close()

        (observed, observed_pre), (nobody, nobody_pre) = asyncio.run(both())
        self.assertEqual(observed[:2], ("blocked", True), "CONTROL: the query did not pick the newest active row")
        self.assertEqual(observed_pre, observed)
        self.assertEqual(nobody, ("", False, ""))
        self.assertEqual(nobody_pre, nobody)
