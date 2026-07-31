"""Bug D regression tests (commit ddda949, 2026-07-02).

Live repro: a managed worker that registered its wrapper-child bridge and then
CRASHED at boot leaves a fresh-but-dead heartbeat row; the send-path coldstart
is suppressed, the first send queues straight into the 180s backstop, and a
resend created a DUPLICATE spawn_request whose kill-prior could murder the
booting worker. Three server-side fixes are pinned here:

1. ``report_terminal_dead`` supersedes the agent's ``managed-wrapper-child``
   bridge rows (``superseded_by = 'terminal-dead:<terminalId>'``) the moment
   the PTY is host-reported dead — so ``_has_live_managed_wrapper_child`` flips
   False immediately instead of after ACTIVE_RUN_BRIDGE_STALE_SECONDS.
2. ``_coldstart_spawn_request_for_dispatch`` coalesces against a RECENT
   ``running`` spawn_request (5-min window, ``_has_pending_or_booting_spawn_request``)
   — no duplicate spawn while a worker is mid-boot; an OLD ``running`` orphan
   does NOT block a fresh coldstart.
3. The queued-run backstop (``_reap_undeliverable_queued_runs``) SELF-HEALS
   before failing: one ``coldstart_rescue`` event + spawn_request, run stays
   queued for one fresh window; a second pass after the window fails the run.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from service.db import get_db
from service.routers import api_v2

from service.tests._base import FastApiTestCase


ENV_ID = "linux:test-host:default"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _minutes_ago(minutes: int) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(minutes=minutes))


class BugDColdstartSelfHealTests(FastApiTestCase):
    DB_NAME = "aify-bugd-test.db"

    # ------------------------------------------------------------------
    # Harness helpers (mirroring test_api_v2_regressions.py patterns)
    # ------------------------------------------------------------------

    def _register(self, agent_id: str, *, role: str = "coder", **extra):
        payload = {"agentId": agent_id, "role": role}
        payload.update(extra)
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _heartbeat_environment(self, **extra):
        payload = {
            "id": ENV_ID,
            "label": "Linux on test-host",
            "machineId": "linux:test-host",
            "os": "linux",
            "kind": "linux",
            "bridgeId": "bridge-current",
            "cwdRoots": ["/workspace"],
            "runtimes": [
                {
                    "runtime": "codex",
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "interrupt": True},
                }
            ],
            "metadata": {},
        }
        payload.update(extra)
        response = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def _execute(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _fetchone(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchone()
            finally:
                await db.close()

        return asyncio.run(_run())

    def _fetchall(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchall()
            finally:
                await db.close()

        return asyncio.run(_run())

    def _has_live_managed_wrapper_child(self, agent_id: str) -> bool:
        async def _run():
            db = await get_db()
            try:
                return await api_v2._has_live_managed_wrapper_child(db, agent_id)
            finally:
                await db.close()

        return asyncio.run(_run())

    def _run_coldstart(self, agent_id: str, *, runtime: str = "codex") -> bool:
        async def _run():
            db = await get_db()
            try:
                settings = await api_v2._load_settings(db)
                created = await api_v2._coldstart_spawn_request_for_dispatch(
                    db, agent_id, runtime=runtime, settings=settings, requested_by="test",
                )
                await db.commit()
                return created
            finally:
                await db.close()

        return asyncio.run(_run())

    def _run_backstop_reaper(self):
        async def _run():
            db = await get_db()
            try:
                reaped = await api_v2._reap_undeliverable_queued_runs(db)
                await db.commit()
                return reaped
            finally:
                await db.close()

        return asyncio.run(_run())

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def _seed_managed_agent_with_terminal(self, agent_id: str, terminal_id: str, *, pid: str = "4242"):
        """Managed codex agent + running session + attached console PTY row."""
        now = api_v2._now()
        self._heartbeat_environment()
        self._register(agent_id, runtime="codex", sessionMode="managed")
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, terminal_id, terminal_status,
                spawn_spec_id, spawn_request_id, status,
                started_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"sess_{agent_id}", agent_id, ENV_ID, "codex", "/workspace/repo",
                "managed-warm", "managed", terminal_id, "attached",
                None, None, "running", now, now,
            ),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, process_id, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                terminal_id, f"sess_{agent_id}", agent_id, ENV_ID,
                "bridge-current", "codex", "/workspace/repo",
                "codex-aify --aify-agent " + agent_id, "", "attached", pid,
                "dashboard", now, now, None, "",
            ),
        )

    def _seed_wrapper_child_bridge(self, bridge_id: str, agent_id: str, *, last_seen: str = ""):
        self._execute(
            """
            INSERT OR REPLACE INTO bridge_instances (
                id, agent_id, machine_id, runtime, session_mode, session_handle,
                terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                bridge_id, agent_id, "linux:test-host", "codex", "managed", "",
                "", "managed-wrapper-child", api_v2._now(),
                last_seen or api_v2._now(), "", None,
            ),
        )

    def _seed_channel_sidecar(self, bridge_id: str, agent_id: str, *, session_mode: str = "managed",
                              last_seen: str = ""):
        """A channel-sidecar row. NOTE: terminal_id is deliberately EMPTY — measured 2026-07-31,
        every channel-sidecar row on the live fleet has one, which is why the fix cannot scope by
        terminal and must scope by session_mode instead."""
        self._execute(
            """
            INSERT OR REPLACE INTO bridge_instances (
                id, agent_id, machine_id, runtime, session_mode, session_handle,
                terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                bridge_id, agent_id, "linux:test-host", "codex", session_mode, "",
                "", "channel-sidecar", api_v2._now(),
                last_seen or api_v2._now(), "", None,
            ),
        )

    def _seed_spawn_request(self, request_id: str, agent_id: str, *, status: str,
                            created_at: str = "", updated_at: str = "", session_id: str = ""):
        now = api_v2._now()
        spec_id = f"spec_{request_id}"
        self._execute(
            """
            INSERT INTO spawn_specs (
                id, agent_id, environment_id, runtime, workspace, model, profile, mode,
                system_prompt, standing_instructions, env_vars, channel_ids, budget_policy,
                context_policy, restart_policy, metadata, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                spec_id, agent_id, ENV_ID, "codex", "/workspace", "", "", "managed-warm",
                "", "", "{}", "[]", "{}", "{}", "{}", "{}", now, now,
            ),
        )
        self._execute(
            """
            INSERT INTO spawn_requests (
                id, spawn_spec_id, created_by, environment_id, agent_id, role, name, runtime,
                workspace, workspace_root, initial_message, priority, subject, mode,
                resume_policy, status, session_handle, session_id, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                request_id, spec_id, "test", ENV_ID, agent_id, "coder", agent_id,
                "codex", "/workspace", "", "", "normal", "seeded", "managed-warm",
                "native_first", status, "", session_id, created_at or now, updated_at or now,
            ),
        )

    def _seed_queued_managed_run(self, run_id: str, *, target_agent: str,
                                 from_agent: str = "bugd-sender", requested_at: str = ""):
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, runtime, message_type, subject, body, priority,
                status, require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, None, from_agent, target_agent, "start_if_possible",
                "managed", "codex", "request", "work", "body", "normal",
                "queued", 1, requested_at or api_v2._now(),
            ),
        )

    # ------------------------------------------------------------------
    # Fix 1: report_terminal_dead supersedes managed-wrapper-child rows
    # ------------------------------------------------------------------

    def test_report_dead_supersedes_wrapper_child_bridge_rows(self):
        # A worker that crashed at boot leaves a FRESH wrapper-child heartbeat
        # row; _has_live_managed_wrapper_child would stay True for the whole
        # stale window and suppress the send-path coldstart. The host-reported
        # dead-PTY signal must supersede those rows immediately.
        agent_id = "bugd-dead-pty"
        terminal_id = "term_bugd_dead"
        self._seed_managed_agent_with_terminal(agent_id, terminal_id, pid="4242")
        self._seed_wrapper_child_bridge("bugd-mwc-1", agent_id)
        self.assertTrue(
            self._has_live_managed_wrapper_child(agent_id),
            "precondition: a fresh non-superseded wrapper-child bridge reads live",
        )

        resp = self.client.post(
            f"/api/v1/terminals/{terminal_id}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "4242", "reason": "host pid not alive"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json().get("changed"), resp.text)

        bridge = self._fetchone(
            "SELECT superseded_by FROM bridge_instances WHERE id = ?", ("bugd-mwc-1",)
        )
        self.assertEqual(
            bridge["superseded_by"], f"terminal-dead:{terminal_id}",
            "report-dead must supersede the agent's managed-wrapper-child rows",
        )
        self.assertFalse(
            self._has_live_managed_wrapper_child(agent_id),
            "a fresh-but-dead wrapper-child must no longer suppress the coldstart",
        )

    def test_report_dead_pid_mismatch_leaves_wrapper_child_live(self):
        # The pid guard rejects stale reports (a restarted console owns a NEW
        # pid); a rejected report must NOT supersede the wrapper-child rows.
        agent_id = "bugd-pid-guard"
        terminal_id = "term_bugd_pid_guard"
        self._seed_managed_agent_with_terminal(agent_id, terminal_id, pid="9001")
        self._seed_wrapper_child_bridge("bugd-mwc-2", agent_id)

        resp = self.client.post(
            f"/api/v1/terminals/{terminal_id}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "1234", "reason": "stale report"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json().get("ignored"), "pid-mismatch", resp.text)

        bridge = self._fetchone(
            "SELECT superseded_by FROM bridge_instances WHERE id = ?", ("bugd-mwc-2",)
        )
        self.assertEqual(bridge["superseded_by"], "", "pid-mismatched report must not supersede")
        self.assertTrue(self._has_live_managed_wrapper_child(agent_id))

    # ------------------------------------------------------------------
    # RESTART FIX (2026-07-31): the same treatment for the CHANNEL SIDECAR
    #
    # Live repro on ef-manager. A managed worker's channel sidecar runs INSIDE the worker, so it
    # dies with the PTY — but nothing superseded its row, so it stayed claim-eligible for the whole
    # 120s stale window. During a Restart it claimed the initial-brief run for its own REPLACEMENT
    # one second before dying, the run then aged out, the spawn_request failed, no `start` control
    # was ever issued, and the agent ended with a live bridge and no worker (reading `available`).
    # ------------------------------------------------------------------

    def test_report_dead_supersedes_managed_channel_sidecar(self):
        agent_id = "restart-sidecar-managed"
        terminal_id = "term_restart_sidecar"
        self._seed_managed_agent_with_terminal(agent_id, terminal_id, pid="5150")
        self._seed_channel_sidecar("restart-cs-1", agent_id, session_mode="managed")

        resp = self.client.post(
            f"/api/v1/terminals/{terminal_id}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "5150", "reason": "host pid not alive"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        bridge = self._fetchone(
            "SELECT superseded_by FROM bridge_instances WHERE id = ?", ("restart-cs-1",)
        )
        self.assertEqual(
            bridge["superseded_by"], f"terminal-dead:{terminal_id}",
            "a managed worker's channel sidecar dies WITH the PTY and must be superseded at the "
            "death site — otherwise it stays claim-eligible for the full 120s stale window",
        )

    def test_report_dead_leaves_a_RESIDENT_channel_sidecar_alone(self):
        """THE REGRESSION GUARD. This is the trap the fix had to avoid.

        Every channel-sidecar row on the live fleet has an EMPTY terminal_id (11/11 measured), so
        reusing the wrapper-child scoping (`terminal_id = '' OR terminal_id = ?`) would have matched
        a RESIDENT sidecar too. A resident sidecar does NOT die with a managed terminal — it is the
        agent's own MCP session — and superseding it would break that agent's delivery path.

        The sibling wrapper-child fix could absorb that false-positive because it only costs one
        redundant coldstart check. Here it costs delivery, so `session_mode = 'managed'` is
        load-bearing. If someone ever "simplifies" the predicate, this test is what stops them.
        """
        agent_id = "restart-sidecar-resident"
        terminal_id = "term_restart_resident"
        self._seed_managed_agent_with_terminal(agent_id, terminal_id, pid="5151")
        self._seed_channel_sidecar("restart-cs-resident", agent_id, session_mode="resident")

        resp = self.client.post(
            f"/api/v1/terminals/{terminal_id}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "5151", "reason": "host pid not alive"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        bridge = self._fetchone(
            "SELECT superseded_by FROM bridge_instances WHERE id = ?", ("restart-cs-resident",)
        )
        self.assertEqual(
            bridge["superseded_by"], "",
            "a RESIDENT channel sidecar does not die with a managed terminal; superseding it would "
            "break that agent's delivery path",
        )

    def test_report_dead_does_not_touch_another_agents_sidecar(self):
        """Agent scoping. The UPDATE has no terminal filter, so agent_id is the only thing keeping
        one agent's death from reaching another's live sidecar."""
        agent_id = "restart-sidecar-scoped"
        other_id = "restart-sidecar-bystander"
        terminal_id = "term_restart_scoped"
        self._seed_managed_agent_with_terminal(agent_id, terminal_id, pid="5152")
        # The bystander needs a real agent row of its own (bridge_instances.agent_id is an FK), and
        # its own LIVE terminal — this models the case that actually matters: one agent restarting
        # while another is happily running on the same host.
        self._seed_managed_agent_with_terminal(other_id, "term_restart_bystander", pid="5199")
        self._seed_channel_sidecar("restart-cs-own", agent_id, session_mode="managed")
        self._seed_channel_sidecar("restart-cs-other", other_id, session_mode="managed")

        resp = self.client.post(
            f"/api/v1/terminals/{terminal_id}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "5152", "reason": "host pid not alive"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        self.assertEqual(
            self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id = ?", ("restart-cs-own",))["superseded_by"],
            f"terminal-dead:{terminal_id}",
        )
        self.assertEqual(
            self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id = ?", ("restart-cs-other",))["superseded_by"],
            "",
            "another agent's sidecar must be untouched",
        )

    def test_report_dead_pid_mismatch_leaves_channel_sidecar_live(self):
        """Same pid guard as the wrapper-child case: a stale dead-report for an OLD terminal must
        not supersede the sidecar of the NEW live worker. Without this the fix would become a way
        for a late report to kill a healthy agent."""
        agent_id = "restart-sidecar-pid"
        terminal_id = "term_restart_pid"
        self._seed_managed_agent_with_terminal(agent_id, terminal_id, pid="9001")
        self._seed_channel_sidecar("restart-cs-pid", agent_id, session_mode="managed")

        resp = self.client.post(
            f"/api/v1/terminals/{terminal_id}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "1234", "reason": "stale report"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json().get("ignored"), "pid-mismatch", resp.text)

        bridge = self._fetchone(
            "SELECT superseded_by FROM bridge_instances WHERE id = ?", ("restart-cs-pid",)
        )
        self.assertEqual(bridge["superseded_by"], "", "a pid-mismatched report must not supersede")

    def test_report_dead_does_not_reclaim_an_already_superseded_sidecar(self):
        """`COALESCE(superseded_by,'') = ''` — an already-superseded row keeps its ORIGINAL cause.
        Overwriting it would destroy the audit trail of which death actually retired the bridge."""
        agent_id = "restart-sidecar-idem"
        terminal_id = "term_restart_idem"
        self._seed_managed_agent_with_terminal(agent_id, terminal_id, pid="5153")
        self._seed_channel_sidecar("restart-cs-idem", agent_id, session_mode="managed")
        self._execute(
            "UPDATE bridge_instances SET superseded_by = ? WHERE id = ?",
            ("reaper:stale-orphan", "restart-cs-idem"),
        )

        resp = self.client.post(
            f"/api/v1/terminals/{terminal_id}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "5153", "reason": "host pid not alive"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        bridge = self._fetchone(
            "SELECT superseded_by FROM bridge_instances WHERE id = ?", ("restart-cs-idem",)
        )
        self.assertEqual(bridge["superseded_by"], "reaper:stale-orphan", "must not overwrite the original cause")

    # ------------------------------------------------------------------
    # Fix 2: coldstart coalesces against a RECENT `running` spawn_request
    # ------------------------------------------------------------------

    def test_coldstart_coalesces_against_recent_running_spawn_request(self):
        # A `running` spawn_request younger than 5 minutes = a worker mid-boot.
        # A second coldstart must NOT create a duplicate (whose kill-prior could
        # murder the booting worker).
        agent_id = "bugd-coalesce"
        self._heartbeat_environment()
        self._register(agent_id, runtime="codex", sessionMode="managed")
        self._seed_spawn_request("spawn_bugd_running", agent_id, status="running")

        created = self._run_coldstart(agent_id)
        self.assertFalse(created, "a recent `running` spawn_request must suppress the coldstart")
        rows = self._fetchall(
            "SELECT id FROM spawn_requests WHERE agent_id = ?", (agent_id,)
        )
        self.assertEqual(len(rows), 1, "no duplicate spawn_request may be created")

    def test_coldstart_proceeds_when_running_spawn_request_is_old(self):
        # A `running` request older than the 5-min window is a stuck orphan and
        # must NOT block future autostarts.
        agent_id = "bugd-old-running"
        self._heartbeat_environment()
        self._register(agent_id, runtime="codex", sessionMode="managed")
        old = _minutes_ago(10)
        self._seed_spawn_request(
            "spawn_bugd_old", agent_id, status="running", created_at=old, updated_at=old,
        )

        created = self._run_coldstart(agent_id)
        self.assertTrue(created, "an old `running` orphan must not suppress the coldstart")
        rows = self._fetchall(
            "SELECT id, status FROM spawn_requests WHERE agent_id = ? ORDER BY created_at",
            (agent_id,),
        )
        self.assertEqual(len(rows), 2, "a NEW spawn_request must be created")
        fresh = self._fetchone(
            "SELECT status FROM spawn_requests WHERE agent_id = ? AND id != 'spawn_bugd_old'",
            (agent_id,),
        )
        self.assertEqual(fresh["status"], "queued")

    def test_coldstart_still_coalesces_against_queued_spawn_request(self):
        # Pre-existing contract kept intact: a queued/claimed request already
        # backs the agent — no duplicate.
        agent_id = "bugd-queued-coalesce"
        self._heartbeat_environment()
        self._register(agent_id, runtime="codex", sessionMode="managed")
        self._seed_spawn_request("spawn_bugd_queued", agent_id, status="queued")

        created = self._run_coldstart(agent_id)
        self.assertFalse(created)
        rows = self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ?", (agent_id,))
        self.assertEqual(len(rows), 1)

    # ------------------------------------------------------------------
    # Fix 3: queued-run backstop self-heals (one coldstart_rescue), then fails
    # ------------------------------------------------------------------

    def test_backstop_self_heals_queued_run_with_coldstart_rescue(self):
        # A queued run past the backstop window for a managed target with NO
        # live claimer and NO spawn_request must be RESCUED once: one
        # coldstart_rescue event + a spawn_request, run stays queued.
        agent_id = "bugd-rescue"
        self._heartbeat_environment()
        self._register(agent_id, runtime="codex", sessionMode="managed")
        self._seed_queued_managed_run(
            "bugd-run-1", target_agent=agent_id, requested_at=_minutes_ago(10),
        )

        reaped = self._run_backstop_reaper()
        self.assertEqual(reaped, [], "the run must be rescued, not failed")

        run = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("bugd-run-1",))
        self.assertEqual(run["status"], "queued", "the rescued run stays queued")
        events = self._fetchall(
            "SELECT id FROM dispatch_events WHERE run_id = ? AND event_type = 'coldstart_rescue'",
            ("bugd-run-1",),
        )
        self.assertEqual(len(events), 1, "exactly ONE coldstart_rescue event")
        spawn = self._fetchone(
            "SELECT status, created_by FROM spawn_requests WHERE agent_id = ?", (agent_id,)
        )
        self.assertIsNotNone(spawn, "the rescue must create a spawn_request")
        self.assertEqual(spawn["status"], "queued")
        self.assertEqual(spawn["created_by"], "queued-run-backstop")

        # A second pass INSIDE the granted window is a no-op: the fresh
        # coldstart_rescue event excludes the run from the reaper query.
        reaped2 = self._run_backstop_reaper()
        self.assertEqual(reaped2, [])
        run = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("bugd-run-1",))
        self.assertEqual(run["status"], "queued")
        events = self._fetchall(
            "SELECT id FROM dispatch_events WHERE run_id = ? AND event_type = 'coldstart_rescue'",
            ("bugd-run-1",),
        )
        self.assertEqual(len(events), 1, "the rescue is one-shot — no second event")
        spawns = self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ?", (agent_id,))
        self.assertEqual(len(spawns), 1, "no duplicate spawn_request from a second pass")

    def test_backstop_fails_run_after_rescue_window_expires(self):
        # The rescue grants ONE fresh window. When the coldstart_rescue event
        # ages past the backstop window and the target STILL has no live
        # claimer, the run must fail exactly as before (mirrored to the sender).
        agent_id = "bugd-rescue-expired"
        self._heartbeat_environment()
        self._register(agent_id, runtime="codex", sessionMode="managed")
        self._seed_queued_managed_run(
            "bugd-run-2", target_agent=agent_id, requested_at=_minutes_ago(20),
        )

        # Pass 1: rescue.
        self.assertEqual(self._run_backstop_reaper(), [])
        run = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("bugd-run-2",))
        self.assertEqual(run["status"], "queued")

        # Age the rescue event past the backstop window (default 180s).
        self._execute(
            "UPDATE dispatch_events SET created_at = ? WHERE run_id = ? AND event_type = 'coldstart_rescue'",
            (_minutes_ago(10), "bugd-run-2"),
        )
        # The rescue-created spawn_request never got a worker: mark it running
        # long ago so it neither counts as pending/booting nor blocks anything.
        self._execute(
            "UPDATE spawn_requests SET status = 'running', created_at = ?, updated_at = ? WHERE agent_id = ?",
            (_minutes_ago(10), _minutes_ago(10), agent_id),
        )

        # Pass 2: already_rescued → no second rescue; the run fails.
        reaped = self._run_backstop_reaper()
        self.assertEqual(reaped, [{"runId": "bugd-run-2", "agentId": agent_id}])

        run = self._fetchone(
            "SELECT status, error_text, finished_at FROM dispatch_runs WHERE id = ?",
            ("bugd-run-2",),
        )
        self.assertEqual(run["status"], "failed")
        self.assertIn("queued-run backstop", run["error_text"] or "")
        self.assertTrue(run["finished_at"], "a failed run must be stamped finished")

        # One-shot invariant holds: still exactly one rescue event, plus the
        # terminal 'failed' event.
        rescues = self._fetchall(
            "SELECT id FROM dispatch_events WHERE run_id = ? AND event_type = 'coldstart_rescue'",
            ("bugd-run-2",),
        )
        self.assertEqual(len(rescues), 1)
        failed_events = self._fetchall(
            "SELECT id FROM dispatch_events WHERE run_id = ? AND event_type = 'failed'",
            ("bugd-run-2",),
        )
        self.assertEqual(len(failed_events), 1)

        # The failure is mirrored back to the original sender.
        mirror = self._fetchone(
            "SELECT from_agent, subject, type FROM messages WHERE to_agent = ?",
            ("bugd-sender",),
        )
        self.assertIsNotNone(mirror, "the failed run must be mirrored to the sender")
        self.assertEqual(mirror["from_agent"], agent_id)
        self.assertEqual(mirror["type"], "error")
        self.assertIn("[NOT DELIVERED]", mirror["subject"])

    # ------------------------------------------------------------------
    # Review fixes (2026-07-02): phantom `running` row + terminal-scoped supersede
    # ------------------------------------------------------------------

    def test_report_dead_stamps_running_spawn_requests_so_coldstart_proceeds(self):
        # `running` is the terminal SUCCESS state of a spawn_request and its
        # timestamps freeze at boot — so for 5 minutes after boot a now-DEAD
        # worker would read as "mid-boot" and suppress the very respawn its
        # death requires. report_terminal_dead must stamp the death.
        agent_id = "bugd-phantom-running"
        terminal_id = "term_bugd_phantom"
        self._seed_managed_agent_with_terminal(agent_id, terminal_id, pid="4242")
        self._seed_spawn_request("spawn_bugd_phantom", agent_id, status="running")

        created = self._run_coldstart(agent_id)
        self.assertFalse(created, "precondition: a fresh running row suppresses the coldstart")

        resp = self.client.post(
            f"/api/v1/terminals/{terminal_id}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "4242", "reason": "host pid not alive"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        row = self._fetchone(
            "SELECT finished_at FROM spawn_requests WHERE id = ?", ("spawn_bugd_phantom",)
        )
        self.assertTrue(row["finished_at"], "report-dead must stamp finished_at on running rows")
        created = self._run_coldstart(agent_id)
        self.assertTrue(
            created,
            "a known-dead worker's running row must not suppress the respawn its death requires",
        )

    def test_report_dead_finished_stamp_is_scoped_to_the_dead_session(self):
        # A stale dead-report for an OLD terminal must NOT finish a NEW live worker's
        # still-booting spawn (bound to a different session) — else the next send
        # coldstarts a DUPLICATE whose registration supersedes and fails the live worker
        # (review 2026-07-03). The dead terminal's OWN-session spawn IS finalized.
        agent_id = "bugd-session-scope"
        dead_terminal = "term_bugd_scope_dead"
        self._seed_managed_agent_with_terminal(agent_id, dead_terminal, pid="4242")
        # The dead terminal's session (seeded by _seed_managed_agent_with_terminal as
        # sess_<agent_id>) + its running spawn; and a SECOND live worker on a different
        # session with its own running spawn.
        self._seed_spawn_request("spawn_scope_dead", agent_id, status="running",
                                 session_id=f"sess_{agent_id}")
        self._seed_spawn_request("spawn_scope_live", agent_id, status="running",
                                 session_id="sess_new_live_worker")

        resp = self.client.post(
            f"/api/v1/terminals/{dead_terminal}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "4242", "reason": "host pid not alive"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        dead = self._fetchone("SELECT status, finished_at FROM spawn_requests WHERE id = ?", ("spawn_scope_dead",))
        live = self._fetchone("SELECT status, finished_at FROM spawn_requests WHERE id = ?", ("spawn_scope_live",))
        self.assertEqual(dead["status"], "failed", "the dead terminal's own spawn is finalized")
        self.assertTrue(dead["finished_at"])
        self.assertEqual(live["status"], "running", "a different session's live spawn must be untouched")
        self.assertFalse(live["finished_at"] or "", "the live worker's spawn must not be finished")

    def test_duplicate_running_patch_preserves_live_console(self):
        # bughunt 2026-07-03 (HIGH): a duplicate/retried 'running' PATCH must NOT
        # cascade-delete the live terminal. The old INSERT OR REPLACE deleted the
        # agent_sessions row on the reused session_id and FK-cascaded away the live
        # terminal_sessions + events + controls; the UPSERT must preserve them.
        agent_id = "bugd-dup-running"
        terminal_id = "term_bugd_dup"
        self._seed_managed_agent_with_terminal(agent_id, terminal_id, pid="4242")
        # Bind the spawn_request to the SAME session id the seeded terminal hangs off,
        # so the 'running' PATCH's UPSERT conflicts on it (the real rotation shape).
        self._seed_spawn_request("spawn_dup_running", agent_id, status="claimed",
                                 session_id=f"sess_{agent_id}")

        def patch_running():
            return self.client.patch(
                "/api/v1/spawn-requests/spawn_dup_running",
                json={"status": "running", "processId": "4242", "bridgeId": "bridge-current"},
            )

        r1 = patch_running()
        self.assertEqual(r1.status_code, 200, r1.text)
        # The retried PATCH (routine on slow hosts) must not nuke the console.
        r2 = patch_running()
        self.assertEqual(r2.status_code, 200, r2.text)

        term = self._fetchone("SELECT id FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertIsNotNone(term, "a duplicate 'running' PATCH must NOT cascade-delete the live terminal")

    def test_report_dead_supersede_is_scoped_to_the_dead_terminal(self):
        # A stale dead-report for an OLD terminal must not kill the NEW live
        # worker's wrapper-child row (that would coldstart a duplicate whose
        # registration supersedes and fails the live worker's runs).
        agent_id = "bugd-scoped-supersede"
        dead_terminal = "term_bugd_old_dead"
        self._seed_managed_agent_with_terminal(agent_id, dead_terminal, pid="4242")
        # OLD worker's row, bound to the dead terminal; NEW live worker's row, bound
        # to its own terminal; plus a legacy flag-only row with no terminal binding.
        self._seed_wrapper_child_bridge("bugd-mwc-old", agent_id)
        self._seed_wrapper_child_bridge("bugd-mwc-new", agent_id)
        self._execute(
            "UPDATE bridge_instances SET terminal_id = ? WHERE id = ?",
            (dead_terminal, "bugd-mwc-old"),
        )
        self._execute(
            "UPDATE bridge_instances SET terminal_id = ? WHERE id = ?",
            ("term_bugd_new_live", "bugd-mwc-new"),
        )
        self._seed_wrapper_child_bridge("bugd-mwc-legacy", agent_id)

        resp = self.client.post(
            f"/api/v1/terminals/{dead_terminal}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "4242", "reason": "host pid not alive"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        rows = {
            r["id"]: r["superseded_by"]
            for r in self._fetchall(
                "SELECT id, superseded_by FROM bridge_instances WHERE agent_id = ?", (agent_id,)
            )
        }
        self.assertEqual(rows["bugd-mwc-old"], f"terminal-dead:{dead_terminal}")
        self.assertEqual(rows["bugd-mwc-legacy"], f"terminal-dead:{dead_terminal}",
                         "flag-only rows (no terminal binding) are still covered")
        self.assertEqual(rows["bugd-mwc-new"], "",
                         "the NEW live worker's row must survive a stale dead-report")
        self.assertTrue(self._has_live_managed_wrapper_child(agent_id))


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()
