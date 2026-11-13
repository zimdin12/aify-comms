"""D9c: the existing start, stop, restart and delete routes hand a DEFINED agent to the lifecycle queue.

Each witness drives the real route, then reads the queued lifecycle request and the legacy tables the
route used to write, since queued is not done and a legacy side effect beside the request would be a
second, unfenced actor. An undefined agent is refused a start since D8
(test_a_spawn_defines_its_agent_first.py).
"""
from __future__ import annotations

import asyncio
import sqlite3

import service.api_core.dispatch_start as dispatch_start
import service.api_core.spawn_request_state as spawn_request_state
from service.db import get_db
from service.tests._base import FastApiTestCase
from service.tests.published_state import publish
from service.tests.test_agent_definition_push import A, snapshot_digest, valid

RUNTIMES = [{"runtime": runtime, "available": True} for runtime in ("claude-code", "codex", "hermes")]


class LegacyRoutesDelegateDefinedAgents(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32", "bridgeId": A["bridge"],
            "cwdRoots": ["/work"], "runtimes": RUNTIMES, "metadata": {}}).raise_for_status()
        entries = [valid("coder")]
        self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "s1", "revision": 1,
            "snapshotDigest": snapshot_digest(entries), "entries": entries}).raise_for_status()
        self.sql("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, started_at, last_seen) "
                 "VALUES ('sess-1', 'coder', ?, 'claude-code', 'managed-warm', 'running', 'now', 'now')", (A["env"],))

    def sql(self, query, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute(query, params)]
            conn.commit()
            return rows
        finally:
            conn.close()

    def legacy_effects(self):
        return {table: self.sql(f"SELECT * FROM {table}")
                for table in ("spawn_requests", "terminal_controls", "definition_requests")}

    def queued(self, response, action, lifetime, *, fresh=False):
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["queued"], body["action"]), (True, action), "queued is not done")
        [row] = self.sql("SELECT action, expected_lifetime, fresh_context, machine_id, status FROM agent_lifecycle_requests")
        self.assertEqual(row, {"action": action, "expected_lifetime": lifetime, "fresh_context": int(fresh),
                               "machine_id": A["machine"], "status": "pending"})
        self.assertEqual(self.legacy_effects(), {"spawn_requests": [], "terminal_controls": [], "definition_requests": []})

    def test_each_route_queues_its_action_with_the_lifetime_the_host_published(self):
        routes = [
            ("start", None, lambda: self.client.post("/api/v1/agents/coder/control", json={"action": "start", "from_agent": "dashboard"})),
            ("stop", "L1", lambda: self.client.post("/api/v1/agents/coder/control", json={"action": "stop", "from_agent": "dashboard"})),
            ("kill", "L1", lambda: self.client.post("/api/v1/agents/coder/stop-worker", json={})),
            # Operator, 2026-10-28: every dashboard Stop leaves the agent startable; only the agent control holds it.
            ("kill", "L1", lambda: self.client.post("/api/v1/sessions/sess-1/control", json={"action": "stop", "from_agent": "dashboard"})),
            # Resume clears the hold its host keeps, and starts nothing.
            ("release", None, lambda: self.client.post("/api/v1/agents/coder/control", json={"action": "resume", "from_agent": "dashboard"})),
            ("restart", "L1", lambda: self.client.post("/api/v1/sessions/sess-1/control", json={"action": "restart", "from_agent": "dashboard"})),
            ("delete", "L1", lambda: self.client.delete("/api/v1/agents/coder")),
        ]
        for number, (action, lifetime, call) in enumerate(routes):
            with self.subTest(route=number, action=action):
                self.sql("DELETE FROM agent_lifecycle_requests")
                publish(self, A["machine"], {"coder": (lifetime, "idle" if lifetime else "available")})
                self.queued(call(), action, lifetime)
        self.assertEqual(len(self.sql("SELECT id FROM agents WHERE id = 'coder'")), 1, "its host removes it")

    def test_an_agent_without_the_operator_key_restarts_stops_and_kills(self):
        """Steven, 2026-10-11: the operator key locks what cannot be undone. A manager's restart, stop and kill of
        a defined agent, sent with only the API key, still queue; a killed agent starts again. The delete the key
        does lock is proven on the lifecycle route (test_agent_lifecycle_requests.py): the legacy DELETE records
        its caller as the dashboard, whose name needs the key on its own."""
        self._app.state.config.operator_key = "fixture-key"
        publish(self, A["machine"], {"coder": ("L1", "idle")})
        self.queued(self.client.post("/api/v1/sessions/sess-1/control", json={"action": "restart", "from_agent": "manager"}),
                    "restart", "L1")
        self.sql("DELETE FROM agent_lifecycle_requests")
        self.queued(self.client.post("/api/v1/agents/coder/control", json={"action": "stop", "from_agent": "manager"}),
                    "stop", "L1")
        self.sql("DELETE FROM agent_lifecycle_requests")
        self.queued(self.client.post("/api/v1/agents/coder/stop-worker", json={"requestedBy": "manager"}), "kill", "L1")

    def test_recreate_is_a_restart_into_a_fresh_conversation(self):
        publish(self, A["machine"], {"coder": ("L1", "idle")})
        self.queued(self.client.post("/api/v1/sessions/sess-1/control", json={"action": "recreate", "from_agent": "dashboard"}),
                    "restart", "L1", fresh=True)

    def test_a_repeat_is_the_request_already_waiting_and_a_recreate_is_not_a_restart(self):
        publish(self, A["machine"], {"coder": ("L1", "idle")})
        control = lambda action: self.client.post("/api/v1/sessions/sess-1/control",
                                                  json={"action": action, "from_agent": "dashboard"})
        first = control("restart")
        self.queued(first, "restart", "L1")
        again = control("restart")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual((again.json()["alreadyQueued"], again.json()["request"]["id"]),
                         (True, first.json()["request"]["id"]))
        refused = control("recreate")
        self.assertEqual(refused.status_code, 409, "the waiting restart keeps the conversation a recreate drops")
        self.assertEqual(len(self.sql("SELECT id FROM agent_lifecycle_requests")), 1)

    def test_without_fresh_state_from_its_host_nothing_is_queued_or_done(self):
        for applied_at in (None, "2000-01-01T00:00:00Z"):
            with self.subTest(applied_at=applied_at):
                if applied_at:
                    publish(self, A["machine"], {"coder": ("L1", "idle")}, applied_at=applied_at)
                refused = self.client.post("/api/v1/agents/coder/control", json={"action": "stop", "from_agent": "dashboard"})
                self.assertEqual(refused.status_code, 409, refused.text)
                self.assertEqual(refused.json()["detail"], f"no fresh agent state from {A['machine']}"
                                 ", so the running lifetime cannot be named; try again once its aify-env publishes")
                self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])
                self.assertEqual(self.legacy_effects(), {"spawn_requests": [], "terminal_controls": [], "definition_requests": []})

    def test_two_published_lifetimes_name_no_lifetime(self):
        publish(self, A["machine"], {"coder": ("L1", "idle")})
        publish(self, A["machine"], {"coder": ("L2", "idle")}, instance="scoped")
        refused = self.client.post("/api/v1/agents/coder/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["detail"], "its host publishes more than one running lifetime for this agent")
        self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])

    def test_another_machines_state_names_nothing(self):
        publish(self, "win32:host-elsewhere", {"coder": ("L9", "idle")})
        refused = self.client.post("/api/v1/agents/coder/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(refused.status_code, 409, refused.text)

    def test_an_unusable_definition_is_refused_at_once(self):
        publish(self, A["machine"], {"coder": (None, "available")})
        entries = [valid("coder", revision=2, mode="resident")]
        self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "s1", "revision": 2,
            "snapshotDigest": snapshot_digest(entries), "entries": entries}).raise_for_status()
        refused = self.client.post("/api/v1/agents/coder/control", json={"action": "start", "from_agent": "dashboard"})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn("as resident", refused.json()["detail"])
        self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])

    def agent_restart(self, action="restart"):
        return self.client.post("/api/v1/agents/coder/control", json={"action": action, "from_agent": "manager"})

    def test_agent_restart_ignores_history_order_and_also_works_without_history(self):
        for history in ("tied-stopped", "hidden-newest", "live", "none"):
            with self.subTest(history=history):
                self.sql("DELETE FROM agent_lifecycle_requests")
                self.sql("DELETE FROM agent_sessions")
                if history != "none":
                    for sid, status, started in (("old", "stopped", "2026-11-01T00:00:00Z"),
                                                  ("new", "running" if history == "live" else "ended" if history == "hidden-newest" else "stopped",
                                                   "2026-11-12T00:00:00Z")):
                        self.sql("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, started_at, last_seen) "
                                 "VALUES (?, 'coder', ?, 'claude-code', 'managed-warm', ?, ?, '2026-11-13T14:21:15Z')",
                                 (sid, A["env"], status, started))
                lifetime = "current-worker" if history == "live" else None
                publish(self, A["machine"], {"coder": (lifetime, "idle" if lifetime else "available")})
                for action in ("restart", "recreate"):
                    self.sql("DELETE FROM agent_lifecycle_requests")
                    self.queued(self.agent_restart(action), "restart", lifetime, fresh=action == "recreate")

    def test_agent_restart_refuses_undefined_and_resident_without_writes(self):
        for mode in ("resident", "undefined"):
            with self.subTest(mode=mode):
                if mode == "resident":
                    self.sql("UPDATE agent_definitions SET body = json_set(body, '$.mode', 'resident')")
                else:
                    self.sql("DELETE FROM agent_definitions")
                for action in ("restart", "recreate"):
                    answer = self.agent_restart(action)
                    self.assertEqual(answer.status_code, 409, answer.text)
                    self.assertIn("as resident" if mode == "resident" else "no host defines", answer.json()["detail"])
                    self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])
                    self.assertEqual(self.legacy_effects(), {"spawn_requests": [], "terminal_controls": [], "definition_requests": []})

    def test_starting_is_read_inside_admission_not_from_a_cached_online_word(self):
        from unittest.mock import patch
        from service.api_core import agent_state_mirror as mirror
        from service.api_core import lifecycle_delegation
        from service.reconcilers.status_cache import _live_state_set

        publish(self, A["machine"], {"coder": ("L1", "idle")})
        _live_state_set("coder", {"status": "online", "refresh_after": "2999-01-01T00:00:00Z"})
        original_owner = lifecycle_delegation.owner_of

        async def owner_then_start(db, agent_id):
            owner = await original_owner(db, agent_id)
            mirror.mirror.feed(mirror.Projection((A["machine"], "default"), "snapshot", 2, mirror.clock(),
                               (mirror.Record("coder", "L1", "starting", False),), ()))
            mirror.mirror.renew_report((A["machine"], "default"), mirror.clock(), 1000)
            return owner

        with patch.object(lifecycle_delegation, "owner_of", owner_then_start):
            for action in ("restart", "recreate"):
                refused = self.agent_restart(action)
                self.assertEqual((refused.status_code, self.sql("SELECT id FROM agent_lifecycle_requests")),
                                 (409, []), "starting must be refused without a lifecycle write")
                self.assertIn("starting", refused.json()["detail"])
                self.assertIn("retry once the agent is online", refused.json()["detail"])
                self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])
        mirror.mirror.feed(mirror.Projection((A["machine"], "default"), "snapshot", 3, mirror.clock(),
                           (mirror.Record("coder", "L1", "idle", False),), ()))
        self.queued(self.agent_restart(), "restart", "L1")

    def cold_start(self, reasons):
        async def run():
            db = await get_db()
            try:
                started = await dispatch_start._coldstart_spawn_request_for_dispatch(
                    db, "coder", runtime="claude-code", settings={}, requested_by="test", warnings=reasons)
                await db.commit()
                return started
            finally:
                await db.close()
        return asyncio.run(run())

    def test_a_message_never_starts_an_agent_its_host_publishes_stopped(self):
        self.sql("UPDATE agent_sessions SET status = 'stopped'")
        publish(self, A["machine"], {"coder": (None, "stopped")})
        reasons: list[str] = []
        self.assertFalse(self.cold_start(reasons))
        self.assertEqual(reasons, [f"{dispatch_start.COLDSTART_REFUSED_PREFIX}stopped by the operator; start it explicitly to deliver"])
        self.assertEqual(self.sql("SELECT id FROM spawn_requests"), [])
        publish(self, A["machine"], {"coder": (None, "available")})
        self.assertTrue(self.cold_start([]), "control: started again, a message starts it")

    def test_a_message_waits_for_the_start_its_host_has_queued(self):
        """Review of 0.9, M1: a message sent before the host claims a queued start cold-started a legacy spawn
        beside it. It now waits for that start, and is held as deliverable rather than refused."""
        self.sql("UPDATE agent_sessions SET status = 'stopped'")
        publish(self, A["machine"], {"coder": (None, "available")})
        self.queued(self.client.post("/api/v1/agents/coder/control", json={"action": "start", "from_agent": "dashboard"}),
                    "start", None)
        reasons: list[str] = []
        self.assertFalse(self.cold_start(reasons))
        self.assertIn("ALREADY IN FLIGHT", reasons[0])
        self.assertEqual(self.sql("SELECT id FROM spawn_requests"), [])
        self.assertTrue(self.claimable(), "the queued start is the backing a refused cold start looks for")
        self.sql("UPDATE agent_lifecycle_requests SET status = 'done'")
        self.assertFalse(self.claimable(), "control: a settled start backs nothing")

    def test_an_old_start_still_waiting_is_still_the_worker_coming(self):
        """Pre-tag review of 0.9, F3: a queued start older than a spawn's claim window stopped counting while its host
        could still claim it, so a message started a second worker beside it. Both open statuses, aged an hour."""
        self.sql("UPDATE agent_sessions SET status = 'stopped'")
        publish(self, A["machine"], {"coder": (None, "available")})
        self.queued(self.client.post("/api/v1/agents/coder/control", json={"action": "start", "from_agent": "dashboard"}),
                    "start", None)
        for status, claimed_at in (("pending", ""), ("claimed", "2026-01-01T00:00:00Z")):
            with self.subTest(status=status):
                self.sql("UPDATE agent_lifecycle_requests SET status = ?, created_at = '2026-01-01T00:00:00Z', claimed_at = ?",
                         (status, claimed_at))
                reasons: list[str] = []
                self.assertFalse(self.cold_start(reasons), reasons)
                self.assertEqual(self.sql("SELECT id FROM spawn_requests"), [])

    def test_a_repeat_is_answered_while_its_hosts_state_is_stale_and_a_new_action_is_not(self):
        """Pre-tag review of 0.9, F4: the running lifetime is named only for a NEW request, so a stale mirror refused
        a repeat of the request already waiting."""
        publish(self, A["machine"], {"coder": ("L1", "idle")})
        control = lambda action: self.client.post("/api/v1/sessions/sess-1/control",
                                                  json={"action": action, "from_agent": "dashboard"})
        first = control("restart")
        self.queued(first, "restart", "L1")
        publish(self, A["machine"], {"coder": ("L1", "idle")}, applied_at="2000-01-01T00:00:00Z")
        again = control("restart")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["request"]["id"], first.json()["request"]["id"])
        stop = self.client.post("/api/v1/agents/coder/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(stop.status_code, 409, "a new action still needs the host's fresh state")

    def test_an_undefined_agents_delete_needs_the_operator_key_too(self):
        """Pre-tag review of 0.9, F6: an undefined agent's delete skipped the key a defined agent's delete needs."""
        self.sql("INSERT INTO agents (id, role, name, runtime, registered_at, last_seen) "
                 "VALUES ('loose', 'coder', 'loose', 'claude-code', 'now', 'now')")
        self._app.state.config.operator_key = "fixture-key"
        refused = self.client.delete("/api/v1/agents/loose")
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertIn("X-Aify-Operator-Key", refused.text)
        self.assertEqual(len(self.sql("SELECT id FROM agents WHERE id = 'loose'")), 1, "refused, so still there")
        removed = self.client.delete("/api/v1/agents/loose", headers={"X-Aify-Operator-Key": "fixture-key"})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(self.sql("SELECT id FROM agents WHERE id = 'loose'"), [], "control: the key removes it")

    def test_a_console_never_starts_a_worker_for_an_agent_no_host_defines(self):
        """Pre-tag review of 0.9, F2: typing into the console of an undefined agent whose session reads running started
        `claude-aify --aify-agent <id>` with no definition. The defined agent beside it is the control."""
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32", "bridgeId": A["bridge"],
            "cwdRoots": ["/work"], "runtimes": RUNTIMES,
            "metadata": {"terminal": True, "pty": True, "terminalRuntimes": ["claude-code"]}}).raise_for_status()
        self.sql("INSERT INTO agents (id, role, name, runtime, session_mode, registered_at, last_seen) "
                 "VALUES ('loose', 'coder', 'loose', 'claude-code', 'managed', 'now', 'now')")
        self.sql("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, started_at, last_seen) "
                 "VALUES ('sess-loose', 'loose', ?, 'claude-code', 'managed-warm', 'running', 'now', 'now')", (A["env"],))
        typed = lambda agent: self.client.post(f"/api/v1/agents/{agent}/console/input",
                                               json={"from": agent, "text": "status", "enter": True})
        refused = typed("loose")
        self.assertEqual(refused.status_code, 200, refused.text)
        self.assertFalse(refused.json()["ok"])
        self.assertIn('no host defines "loose"', refused.json()["message"])
        self.assertEqual(self.sql("SELECT agent_id FROM terminal_sessions"), [])
        # CONTROL: a defined agent's session, made by its real cold start, recovers through the same console, and the
        # launch its host reads carries the definition, which is what the host admits a defined start by.
        self.sql("UPDATE agent_sessions SET status = 'stopped' WHERE id = 'sess-1'")
        publish(self, A["machine"], {"coder": (None, "available")})
        self.assertTrue(self.cold_start([]))
        [spawn] = self.sql("SELECT id, definition_store_id FROM spawn_requests")
        self.assertEqual(spawn["definition_store_id"], "s1")
        self.sql("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, spawn_request_id, "
                 "started_at, last_seen) VALUES ('sess-2', 'coder', ?, 'claude-code', 'managed-warm', 'running', ?, 'now', 'now')",
                 (A["env"], spawn["id"]))
        typed("coder")
        [terminal] = self.sql("SELECT id, agent_id FROM terminal_sessions")
        self.assertEqual(terminal["agent_id"], "coder", "control: the defined agent's console starts its worker")
        launch = self.client.get(f"/api/v1/terminals/{terminal['id']}/launch")
        self.assertEqual(launch.status_code, 200, launch.text)
        self.assertEqual(launch.json()["launch"]["definition"]["storeId"], "s1")

    def claimable(self):
        async def run():
            db = await get_db()
            try:
                return await spawn_request_state._has_claimable_spawn_request(db, "coder")
            finally:
                await db.close()
        return asyncio.run(run())
