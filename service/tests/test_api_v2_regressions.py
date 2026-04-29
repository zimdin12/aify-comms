import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import get_db, init_db
from service.routers.api_v2 import router


class _DummyWS:
    async def broadcast(self, *_args, **_kwargs):
        return None

    async def notify_agent(self, *_args, **_kwargs):
        return None


class ApiV2RegressionTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test.db"
        asyncio.run(init_db(self._db_path))

        app = FastAPI()
        app.state.ws_manager = _DummyWS()
        app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        app.include_router(router, prefix="/api/v1")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def _register(self, agent_id: str, *, role: str = "coder", **extra):
        payload = {"agentId": agent_id, "role": role}
        payload.update(extra)
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _send_message(self, **payload):
        response = self.client.post("/api/v1/messages/send", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _dispatch(self, **payload):
        response = self.client.post("/api/v1/dispatch", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _heartbeat_environment(self, **extra):
        payload = {
            "id": "linux:test-host:default",
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
                    "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                }
            ],
            "metadata": {},
        }
        payload.update(extra)
        response = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

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

    def _execute(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def test_channel_history_excludes_inbox_fanout_rows(self):
        self._register("alice")
        self._register("bob")

        response = self.client.post(
            "/api/v1/channels",
            json={"name": "room", "description": "", "createdBy": "alice"},
        )
        self.assertEqual(response.status_code, 200, response.text)

        response = self.client.post("/api/v1/channels/room/join", json={"agentId": "bob"})
        self.assertEqual(response.status_code, 200, response.text)

        response = self.client.post(
            "/api/v1/channels/room/send",
            json={"from_agent": "alice", "channel": "room", "body": "hello", "priority": "high", "trigger": False},
        )
        self.assertEqual(response.status_code, 200, response.text)

        channel = self.client.get("/api/v1/channels/room")
        self.assertEqual(channel.status_code, 200, channel.text)
        data = channel.json()

        self.assertEqual(data["totalMessages"], 2)
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual([message["body"] for message in data["messages"]], ["bob joined the channel", "hello"])
        self.assertEqual(data["messages"][1]["priority"], "high")
        self.assertTrue(all(not message["id"].endswith("-bob") for message in data["messages"]))

        channels = self.client.get("/api/v1/channels")
        self.assertEqual(channels.status_code, 200, channels.text)
        listed = {item["name"]: item for item in channels.json()["channels"]}
        self.assertEqual(listed["room"]["messageCount"], 2)

    def test_environment_heartbeat_upserts_persistent_record(self):
        payload = {
            "id": "wsl:test-host:default",
            "label": "WSL on test-host",
            "machineId": "wsl-Ubuntu:test-host",
            "os": "linux",
            "kind": "wsl",
            "bridgeId": "bridge-1",
            "bridgeVersion": "3.7.0",
            "cwdRoots": ["/mnt/c/Docker"],
            "runtimes": [
                {
                    "runtime": "codex",
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                }
            ],
            "metadata": {"pid": 123},
        }

        first = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        first_env = first.json()["environment"]
        self.assertEqual(first_env["id"], "wsl:test-host:default")
        self.assertEqual(first_env["label"], "WSL on test-host")
        self.assertEqual(first_env["machineId"], "wsl-Ubuntu:test-host")
        self.assertEqual(first_env["cwdRoots"], ["/mnt/c/Docker"])
        self.assertEqual(first_env["runtimes"][0]["runtime"], "codex")
        self.assertEqual(first_env["metadata"]["pid"], 123)
        self.assertEqual(first_env["metadata"]["advertisedCwdRoots"], ["/mnt/c/Docker"])
        self.assertEqual(first_env["status"], "online")

        second_payload = {
            **payload,
            "label": "Updated bridge",
            "bridgeId": "bridge-2",
            "cwdRoots": ["/mnt/c/Docker", "/home/test"],
            "metadata": {"pid": 456},
        }
        second = self.client.post("/api/v1/environments/heartbeat", json=second_payload)
        self.assertEqual(second.status_code, 200, second.text)
        second_env = second.json()["environment"]
        self.assertEqual(second_env["label"], "Updated bridge")
        self.assertEqual(second_env["bridgeId"], "bridge-2")
        self.assertEqual(second_env["cwdRoots"], ["/mnt/c/Docker", "/home/test"])
        self.assertEqual(second_env["registeredAt"], first_env["registeredAt"])

        rows = self._fetchall("SELECT id, label, cwd_roots, metadata FROM environments")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "Updated bridge")
        self.assertEqual(rows[0]["cwd_roots"], '["/mnt/c/Docker", "/home/test"]')

    def test_environment_roots_override_survives_heartbeat_until_reset(self):
        first = self._heartbeat_environment(cwdRoots=["/workspace"])
        self.assertEqual(first["cwdRoots"], ["/workspace"])

        updated = self.client.patch(
            "/api/v1/environments/linux%3Atest-host%3Adefault/roots",
            json={"roots": ["/workspace", "/extra"], "requestedBy": "dashboard"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["environment"]["cwdRoots"], ["/workspace", "/extra"])
        self.assertTrue(updated.json()["environment"]["metadata"]["manualRoots"])

        heartbeat = self._heartbeat_environment(cwdRoots=["/workspace"])
        self.assertEqual(heartbeat["cwdRoots"], ["/workspace", "/extra"])
        self.assertEqual(heartbeat["metadata"]["advertisedCwdRoots"], ["/workspace"])
        self.assertTrue(heartbeat["metadata"]["manualRoots"])

        reset = self.client.patch(
            "/api/v1/environments/linux%3Atest-host%3Adefault/roots",
            json={"resetToBridgeAdvertised": True, "requestedBy": "dashboard"},
        )
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(reset.json()["environment"]["cwdRoots"], ["/workspace"])
        self.assertFalse(reset.json()["environment"]["metadata"]["manualRoots"])

    def test_environment_stop_control_is_claimed_by_matching_bridge(self):
        self._heartbeat_environment(id="wsl:test-host:default", bridgeId="bridge-stop")

        requested = self.client.post(
            "/api/v1/environments/wsl%3Atest-host%3Adefault/control",
            json={"action": "stop", "requestedBy": "dashboard"},
        )
        self.assertEqual(requested.status_code, 200, requested.text)
        self.assertEqual(requested.json()["action"], "stop")

        claim = self.client.post(
            "/api/v1/environments/controls/claim",
            json={"environmentId": "wsl:test-host:default", "bridgeId": "bridge-stop", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        control = claim.json()["control"]
        self.assertIsNotNone(control)
        self.assertEqual(control["action"], "stop")

        completed = self.client.patch(
            f"/api/v1/environments/controls/{control['id']}",
            json={"status": "completed"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)

    def test_stale_environment_stop_control_does_not_kill_new_bridge(self):
        self._heartbeat_environment(id="windows:test-host:default", bridgeId="bridge-new", metadata={"bridgeStartedAt": "2999-01-01T00:00:00Z"})
        self._execute(
            """
            INSERT INTO environment_controls (
                id, environment_id, bridge_id, machine_id, action, status, requested_by, requested_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            ("envctl-stale", "windows:test-host:default", "", "win32:test-host", "stop", "pending", "dashboard", "2020-01-01T00:00:00Z"),
        )

        claim = self.client.post(
            "/api/v1/environments/controls/claim",
            json={"environmentId": "windows:test-host:default", "bridgeId": "bridge-new", "machineId": "win32:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertIsNone(claim.json()["control"])
        control = self._fetchone("SELECT status, error FROM environment_controls WHERE id = ?", ("envctl-stale",))
        self.assertEqual(control["status"], "failed")
        self.assertIn("Stale stop control ignored", control["error"])

    def test_environment_list_api_and_dashboard_render_surface(self):
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": "linux:test-host:default",
                "label": "Linux on test-host",
                "machineId": "linux:test-host",
                "os": "linux",
                "kind": "linux",
                "bridgeId": "bridge-api",
                "cwdRoots": ["/workspace"],
                "runtimes": [{"runtime": "opencode", "modes": ["managed-warm"], "capabilities": {"streaming": True}}],
                "metadata": {},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        listed = self.client.get("/api/v1/environments")
        self.assertEqual(listed.status_code, 200, listed.text)
        payload = listed.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["environments"]), 1)
        self.assertEqual(payload["environments"][0]["id"], "linux:test-host:default")
        self.assertEqual(payload["environments"][0]["bridgeId"], "bridge-api")
        self.assertEqual(payload["environments"][0]["runtimes"][0]["runtime"], "opencode")

        dashboard = self.client.get("/api/v1/dashboard")
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        self.assertIn("Environments", dashboard.text)
        self.assertIn("/environments", dashboard.text)
        self.assertIn("data-dashboard-action", dashboard.text)
        self.assertNotIn('onclick="runDashboardAction(', dashboard.text)
        self.assertIn("Advanced run control", dashboard.text)
        self.assertIn("Normal users and agents should send messages, not dispatches.", dashboard.text)
        self.assertIn("chat-channel-add-member", dashboard.text)
        self.assertIn("Add member", dashboard.text)
        self.assertIn("data-channel-member-select", dashboard.text)
        self.assertIn("chat-online-only", dashboard.text)
        self.assertIn("Online only", dashboard.text)
        self.assertIn("Copy CLI resume", dashboard.text)
        self.assertIn("data-agent-edit-env", dashboard.text)
        self.assertIn("Edit workspace roots", dashboard.text)
        self.assertIn("Edit identity ID", dashboard.text)
        self.assertNotIn("assignAgentEnvironment", dashboard.text)

    def test_environment_list_marks_missing_heartbeat_offline_and_orders_stably(self):
        self._heartbeat_environment(
            id="wsl:test-host:default",
            label="WSL on test-host",
            bridgeId="bridge-wsl",
        )
        self._heartbeat_environment(
            id="windows:test-host:default",
            label="Windows on test-host",
            os="windows",
            kind="windows",
            bridgeId="bridge-windows",
        )
        self._execute(
            "UPDATE environments SET last_seen = '2020-01-01T00:00:00Z' WHERE id = ?",
            ("wsl:test-host:default",),
        )

        listed = self.client.get("/api/v1/environments")
        self.assertEqual(listed.status_code, 200, listed.text)
        environments = listed.json()["environments"]
        self.assertEqual([env["id"] for env in environments], ["windows:test-host:default", "wsl:test-host:default"])
        by_id = {env["id"]: env for env in environments}
        self.assertEqual(by_id["windows:test-host:default"]["status"], "online")
        self.assertEqual(by_id["wsl:test-host:default"]["status"], "offline")

    def test_environment_shutdown_heartbeat_marks_offline_only_for_current_bridge(self):
        self._heartbeat_environment(id="wsl:test-host:default", bridgeId="bridge-current")

        stale = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": "wsl:test-host:default",
                "bridgeId": "bridge-old",
                "status": "offline",
            },
        )
        self.assertEqual(stale.status_code, 200, stale.text)
        self.assertEqual(stale.json()["environment"]["status"], "online")

        current = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": "wsl:test-host:default",
                "bridgeId": "bridge-current",
                "status": "offline",
            },
        )
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["environment"]["status"], "offline")

    def test_environment_newer_bridge_wins_old_heartbeats_do_not_flap_row(self):
        first = self._heartbeat_environment(
            id="wsl:test-host:default",
            bridgeId="bridge-old",
            metadata={"pid": 111, "bridgeStartedAt": "2026-04-28T10:00:00Z"},
        )
        self.assertEqual(first["bridgeId"], "bridge-old")

        newer = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": "wsl:test-host:default",
                "label": "WSL on test-host",
                "machineId": "wsl-Ubuntu:test-host",
                "os": "linux",
                "kind": "wsl",
                "bridgeId": "bridge-new",
                "cwdRoots": ["/workspace"],
                "runtimes": [{"runtime": "codex", "modes": ["managed-warm"], "capabilities": {}}],
                "metadata": {"pid": 222, "bridgeStartedAt": "2026-04-28T10:05:00Z"},
            },
        )
        self.assertEqual(newer.status_code, 200, newer.text)
        self.assertEqual(newer.json()["environment"]["bridgeId"], "bridge-new")

        old_again = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": "wsl:test-host:default",
                "label": "Old stale bridge",
                "machineId": "wsl-Ubuntu:test-host",
                "os": "linux",
                "kind": "wsl",
                "bridgeId": "bridge-old",
                "cwdRoots": ["/old"],
                "runtimes": [{"runtime": "opencode", "modes": ["managed-warm"], "capabilities": {}}],
                "metadata": {"pid": 111, "bridgeStartedAt": "2026-04-28T10:00:00Z"},
            },
        )
        self.assertEqual(old_again.status_code, 200, old_again.text)
        environment = old_again.json()["environment"]
        self.assertEqual(environment["bridgeId"], "bridge-new")
        self.assertEqual(environment["metadata"]["pid"], 222)
        self.assertEqual(environment["cwdRoots"], ["/workspace"])
        controls = self._fetchall(
            "SELECT bridge_id, action, status, requested_by FROM environment_controls WHERE environment_id = ?",
            ("wsl:test-host:default",),
        )
        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]["bridge_id"], "bridge-old")
        self.assertEqual(controls[0]["action"], "stop")
        self.assertEqual(controls[0]["status"], "pending")
        self.assertEqual(controls[0]["requested_by"], "server:superseded-bridge")

        claim_old = self.client.post(
            "/api/v1/environments/controls/claim",
            json={"environmentId": "wsl:test-host:default", "bridgeId": "bridge-old", "machineId": "wsl-Ubuntu:test-host"},
        )
        self.assertEqual(claim_old.status_code, 200, claim_old.text)
        control = claim_old.json()["control"]
        self.assertEqual(control["action"], "stop")
        self.assertEqual(control["requestedBy"], "server:superseded-bridge")
        self.assertEqual(control["currentEnvironment"]["bridgeId"], "bridge-new")
        self.assertEqual(control["currentEnvironment"]["metadata"]["pid"], 222)

    def test_forget_environment_hides_target_but_preserves_agent_session_and_spec(self):
        self._heartbeat_environment(id="linux:test-host:default")
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "preserved-agent",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "sessionHandle": "thread-1"},
        )
        self.assertEqual(running.status_code, 200, running.text)

        forgotten = self.client.post(
            "/api/v1/environments/linux%3Atest-host%3Adefault/control",
            json={"action": "forget", "requestedBy": "dashboard"},
        )
        self.assertEqual(forgotten.status_code, 200, forgotten.text)

        listed = self.client.get("/api/v1/environments")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["environments"], [])
        agent = self._fetchone("SELECT * FROM agents WHERE id = ?", ("preserved-agent",))
        session = self._fetchone("SELECT * FROM agent_sessions WHERE agent_id = ?", ("preserved-agent",))
        spec = self._fetchone("SELECT * FROM spawn_specs WHERE agent_id = ?", ("preserved-agent",))
        self.assertIsNotNone(agent)
        self.assertIsNotNone(session)
        self.assertIsNotNone(spec)
        self.assertEqual(self._fetchone("SELECT status FROM environments WHERE id = ?", ("linux:test-host:default",))["status"], "forgotten")

    def test_assign_agent_environment_retargets_saved_managed_config(self):
        self._heartbeat_environment(id="linux:old-host:default", bridgeId="bridge-old")
        self._heartbeat_environment(id="linux:new-host:default", bridgeId="bridge-new", cwdRoots=["/newroot"])
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:old-host:default",
                "agentId": "move-me",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:old-host:default", "bridgeId": "bridge-old", "machineId": "linux:test-host"},
        )
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-old", "sessionHandle": "thread-1"},
        )
        self.assertEqual(running.status_code, 200, running.text)

        assigned = self.client.post(
            "/api/v1/agents/move-me/environment",
            json={"environmentId": "linux:new-host:default", "runtime": "codex", "workspace": "/newroot/project"},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(assigned.json()["environmentId"], "linux:new-host:default")

        agent = self._fetchone("SELECT cwd, launch_mode, session_mode, status FROM agents WHERE id = ?", ("move-me",))
        session = self._fetchone("SELECT environment_id, workspace, status FROM agent_sessions WHERE agent_id = ?", ("move-me",))
        spec = self._fetchone("SELECT environment_id, workspace FROM spawn_specs WHERE agent_id = ?", ("move-me",))
        self.assertEqual(agent["cwd"], "/newroot/project")
        self.assertEqual(agent["launch_mode"], "none")
        self.assertEqual(agent["session_mode"], "managed")
        self.assertEqual(agent["status"], "offline")
        self.assertEqual(session["environment_id"], "linux:new-host:default")
        self.assertEqual(session["workspace"], "/newroot/project")
        self.assertEqual(session["status"], "lost")
        self.assertEqual(spec["environment_id"], "linux:new-host:default")
        self.assertEqual(spec["workspace"], "/newroot/project")

    def test_assign_agent_environment_adopts_resident_agent_with_session_record(self):
        self._heartbeat_environment(id="linux:new-host:default", bridgeId="bridge-new", cwdRoots=["/newroot"])
        self._register(
            "resident-manager",
            role="manager",
            runtime="codex",
            cwd="/newroot/project",
            sessionMode="resident",
            sessionHandle="thread-old",
            launchMode="codex-live",
        )

        assigned = self.client.post(
            "/api/v1/agents/resident-manager/environment",
            json={"environmentId": "linux:new-host:default", "runtime": "codex", "workspace": "/newroot/project"},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)

        agent = self._fetchone("SELECT cwd, launch_mode, session_mode, session_handle, status FROM agents WHERE id = ?", ("resident-manager",))
        session = self._fetchone("SELECT environment_id, runtime, workspace, status, spawn_spec_id FROM agent_sessions WHERE agent_id = ?", ("resident-manager",))
        spec = self._fetchone("SELECT environment_id, runtime, workspace FROM spawn_specs WHERE agent_id = ?", ("resident-manager",))
        self.assertEqual(agent["session_mode"], "managed")
        self.assertEqual(agent["launch_mode"], "none")
        self.assertEqual(agent["session_handle"], "")
        self.assertEqual(agent["status"], "offline")
        self.assertIsNotNone(session)
        self.assertEqual(session["environment_id"], "linux:new-host:default")
        self.assertEqual(session["runtime"], "codex")
        self.assertEqual(session["workspace"], "/newroot/project")
        self.assertEqual(session["status"], "stopped")
        self.assertTrue(session["spawn_spec_id"])
        self.assertEqual(spec["environment_id"], "linux:new-host:default")
        self.assertEqual(spec["workspace"], "/newroot/project")

    def test_rename_agent_identity_cascades_history_and_blocks_stale_old_id(self):
        self._heartbeat_environment(cwdRoots=["/workspace"])
        self._register("manager", role="manager")
        self._register("peer", role="coder")
        spawn = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "old-agent",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(spawn.status_code, 200, spawn.text)
        spawn_id = spawn.json()["spawnRequest"]["id"]
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "sessionHandle": "thread-old"},
        )
        self.assertEqual(running.status_code, 200, running.text)
        self._send_message(from_agent="old-agent", to="peer", type="info", subject="from old", body="hello", trigger=False)
        self._send_message(from_agent="peer", to="old-agent", type="info", subject="to old", body="hello", trigger=False)
        created = self.client.post("/api/v1/channels", json={"name": "rename-room", "description": "", "createdBy": "old-agent"})
        self.assertEqual(created.status_code, 200, created.text)
        joined = self.client.post("/api/v1/channels/rename-room/join", json={"agentId": "old-agent"})
        self.assertEqual(joined.status_code, 200, joined.text)
        dispatched = self._dispatch(from_agent="manager", to="old-agent", type="request", subject="work", body="do work")
        self.assertTrue(dispatched["runs"])

        renamed = self.client.post(
            "/api/v1/agents/old-agent/rename",
            json={"newAgentId": "new-agent", "requestedBy": "dashboard"},
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertTrue(renamed.json()["changed"])

        self.assertEqual(self.client.get("/api/v1/agents/new-agent").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/agents/old-agent").status_code, 410)
        self.assertIsNotNone(self._fetchone("SELECT * FROM agent_tombstones WHERE agent_id = ?", ("old-agent",)))
        for table in ("agent_sessions", "spawn_specs", "spawn_requests", "bridge_instances", "channel_members"):
            self.assertEqual(self._fetchall(f"SELECT * FROM {table} WHERE agent_id = ?", ("old-agent",)), [])
            self.assertTrue(self._fetchall(f"SELECT * FROM {table} WHERE agent_id = ?", ("new-agent",)))
        self.assertEqual(self._fetchall("SELECT * FROM messages WHERE from_agent = ? OR to_agent = ?", ("old-agent", "old-agent")), [])
        self.assertTrue(self._fetchall("SELECT * FROM messages WHERE from_agent = ? OR to_agent = ?", ("new-agent", "new-agent")))
        self.assertEqual(self._fetchall("SELECT * FROM dispatch_runs WHERE target_agent = ?", ("old-agent",)), [])
        self.assertTrue(self._fetchall("SELECT * FROM dispatch_runs WHERE target_agent = ?", ("new-agent",)))
        self.assertEqual(self._fetchone("SELECT created_by FROM channels WHERE name = ?", ("rename-room",))["created_by"], "new-agent")

    def test_managed_dispatch_claim_rejects_stale_environment_bridge(self):
        self._heartbeat_environment(id="linux:test-host:default", bridgeId="bridge-current")
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "managed-stale-bridge",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "sessionHandle": "thread-1"},
        )
        self.assertEqual(running.status_code, 200, running.text)
        dispatched = self._dispatch(
            from_agent="dashboard",
            to="managed-stale-bridge",
            type="request",
            subject="work",
            body="do it",
            requireReply=False,
        )
        self.assertEqual(dispatched["runs"][0]["status"], "queued")

        # A newer environment bridge has replaced the one stored in the agent's
        # old runtime_state. The stale managed bridge must not claim new runs.
        self._heartbeat_environment(id="linux:test-host:default", bridgeId="bridge-new")
        stale_claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "managed-stale-bridge",
                "bridgeId": "bridge-current",
                "machineId": "linux:test-host",
                "executionModes": ["managed"],
            },
        )
        self.assertEqual(stale_claim.status_code, 200, stale_claim.text)
        payload = stale_claim.json()
        self.assertIsNone(payload["run"])
        self.assertEqual(payload["blockedBy"]["reason"], "environment_bridge_not_current")

        current_claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "managed-stale-bridge",
                "bridgeId": "bridge-new",
                "machineId": "linux:test-host",
                "executionModes": ["managed"],
            },
        )
        self.assertEqual(current_claim.status_code, 200, current_claim.text)
        self.assertEqual(current_claim.json()["run"]["id"], dispatched["runs"][0]["runId"])

    def test_replacement_bridge_does_not_immediately_fail_recent_active_run(self):
        self._register("manager", role="manager")
        self._register(
            "claude-worker",
            role="coder",
            runtime="claude-code",
            sessionMode="managed",
            launchMode="managed",
        )
        dispatched = self._dispatch(
            from_agent="manager",
            to="claude-worker",
            type="request",
            subject="active",
            body="do work",
        )
        run_id = dispatched["runs"][0]["runId"]
        first_claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "claude-worker", "bridgeId": "bridge-old", "machineId": "win32:test-host", "executionModes": ["managed"]},
        )
        self.assertEqual(first_claim.status_code, 200, first_claim.text)
        self.assertEqual(first_claim.json()["run"]["id"], run_id)

        replacement_claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "claude-worker", "bridgeId": "bridge-new", "machineId": "win32:test-host", "executionModes": ["managed"]},
        )
        self.assertEqual(replacement_claim.status_code, 200, replacement_claim.text)
        payload = replacement_claim.json()
        self.assertIsNone(payload["run"])
        self.assertEqual(payload["blockedBy"]["reason"], "active_run_owned_by_previous_bridge")
        run = self._fetchone("SELECT status, summary, error_text FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(run["status"], "claimed")
        self.assertEqual(run["summary"], "")

    def test_dispatch_claim_includes_scoped_direct_conversation_context(self):
        self._register("dashboard", role="manager")
        self._register("worker", runtime="claude-code", sessionMode="managed", launchMode="managed")
        self._register("other", role="coder")

        self._send_message(
            from_agent="dashboard",
            to="worker",
            type="info",
            subject="previous question",
            body="Can you check the last thing?",
            trigger=False,
        )
        self._send_message(
            from_agent="worker",
            to="dashboard",
            type="response",
            subject="previous answer",
            body="I said I could not check messages yet.",
            trigger=False,
        )
        self._send_message(
            from_agent="other",
            to="worker",
            type="info",
            subject="unrelated",
            body="This should not be included.",
            trigger=False,
        )
        dispatched = self._send_message(
            from_agent="dashboard",
            to="worker",
            type="info",
            subject="current",
            body="Can you now?",
            trigger=True,
        )

        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "worker", "bridgeId": "bridge-1", "machineId": "win32:test-host", "executionModes": ["managed"]},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        run = claim.json()["run"]
        self.assertEqual(run["messageId"], dispatched["messageId"])
        context = run["conversationContext"]
        self.assertEqual([item["subject"] for item in context], ["previous question", "previous answer"])
        self.assertNotIn("current", [item["subject"] for item in context])
        self.assertNotIn("unrelated", [item["subject"] for item in context])

    def test_spawn_request_targets_environment_and_matching_bridge_claims(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "worker-env",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
                "initialMessage": "Start here",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_request = created.json()["spawnRequest"]
        self.assertEqual(spawn_request["status"], "queued")
        self.assertEqual(spawn_request["environmentId"], "linux:test-host:default")
        self.assertEqual(spawn_request["workspaceRoot"], "/workspace")
        self.assertEqual(spawn_request["spawnSpec"]["runtime"], "codex")

        stale_claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-stale", "machineId": "linux:test-host"},
        )
        self.assertEqual(stale_claim.status_code, 200, stale_claim.text)
        self.assertIsNone(stale_claim.json()["spawnRequest"])
        self.assertEqual(stale_claim.json()["blockedBy"]["reason"], "bridge_not_current")

        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        claimed = claim.json()["spawnRequest"]
        self.assertEqual(claimed["id"], spawn_request["id"])
        self.assertEqual(claimed["status"], "claimed")
        self.assertEqual(claimed["claimedByBridgeId"], "bridge-current")

    def test_initial_dispatch_failure_marks_running_spawn_request_failed(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "brief-fails",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
                "initialMessage": "Start here",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "sessionHandle": "thread-1"},
        )
        self.assertEqual(running.status_code, 200, running.text)
        run = self._fetchone("SELECT id FROM dispatch_runs WHERE target_agent = ?", ("brief-fails",))
        self.assertIsNotNone(run)
        failed = self.client.patch(
            f"/api/v1/dispatch/runs/{run['id']}",
            json={"status": "failed", "error": "runtime unavailable"},
        )
        self.assertEqual(failed.status_code, 200, failed.text)

        listed = self.client.get("/api/v1/spawn-requests")
        self.assertEqual(listed.status_code, 200, listed.text)
        spawn = next(item for item in listed.json()["spawnRequests"] if item["id"] == spawn_id)
        self.assertEqual(spawn["status"], "failed")
        self.assertIn("Initial brief failed", spawn["error"])

    def test_spawn_request_rejects_non_live_modes(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "worker-run-once",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
                "mode": "run-once",
            },
        )
        self.assertEqual(created.status_code, 400, created.text)
        self.assertIn("Unsupported spawn mode", created.text)

    def test_spawn_request_running_auto_registers_agent_session_and_initial_dispatch(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "spawned-coder",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/repo",
                "initialMessage": "Implement a small task",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)

        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={
                "status": "running",
                "bridgeId": "bridge-current",
                "processId": "1234",
                "runtimeState": {"environmentId": "linux:test-host:default"},
                "capabilities": {"persistent": True, "bridgeResume": True},
            },
        )
        self.assertEqual(running.status_code, 200, running.text)
        self.assertEqual(running.json()["spawnRequest"]["status"], "running")
        self.assertTrue(running.json()["spawnRequest"]["sessionId"])

        agent = self.client.get("/api/v1/agents/spawned-coder")
        self.assertEqual(agent.status_code, 200, agent.text)
        agent_payload = agent.json()["agent"]
        self.assertEqual(agent_payload["sessionMode"], "managed")
        self.assertEqual(agent_payload["runtime"], "codex")
        self.assertEqual(agent_payload["cwd"], "/workspace/repo")
        self.assertEqual(agent_payload["runtimeState"]["bridgeInstanceId"], "bridge-current")

        sessions = self.client.get("/api/v1/sessions?agentId=spawned-coder")
        self.assertEqual(sessions.status_code, 200, sessions.text)
        self.assertEqual(len(sessions.json()["sessions"]), 1)
        self.assertEqual(sessions.json()["sessions"][0]["spawnRequestId"], spawn_id)

        self._heartbeat_environment()
        after_heartbeat = self.client.get("/api/v1/spawn-requests")
        self.assertEqual(after_heartbeat.status_code, 200, after_heartbeat.text)
        self.assertEqual(after_heartbeat.json()["spawnRequests"][0]["id"], spawn_id)
        sessions_after_heartbeat = self.client.get("/api/v1/sessions?agentId=spawned-coder")
        self.assertEqual(sessions_after_heartbeat.status_code, 200, sessions_after_heartbeat.text)
        self.assertEqual(len(sessions_after_heartbeat.json()["sessions"]), 1)

        runs = self._fetchall("SELECT target_agent, status, body FROM dispatch_runs WHERE target_agent = ?", ("spawned-coder",))
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "queued")
        self.assertEqual(runs[0]["body"], "Implement a small task")

    def test_session_stop_interrupts_active_run_and_marks_session_stopped(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "session-coder",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/repo",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "processId": "1234"},
        )
        self.assertEqual(running.status_code, 200, running.text)
        session_id = running.json()["spawnRequest"]["sessionId"]

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="session-coder",
            type="request",
            subject="work",
            body="do it",
            mode="start_if_possible",
            createMessage=True,
        )
        claim_run = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "session-coder", "machineId": "linux:test-host", "bridgeId": "bridge-current", "executionModes": ["managed"]},
        )
        self.assertEqual(claim_run.status_code, 200, claim_run.text)
        self.assertEqual(claim_run.json()["run"]["id"], dispatched["runs"][0]["runId"])

        stopped = self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "stop", "from_agent": "dashboard", "body": "stop now"},
        )
        self.assertEqual(stopped.status_code, 200, stopped.text)
        payload = stopped.json()
        self.assertEqual(payload["session"]["status"], "stopped")
        self.assertIsNone(payload["spawnRequest"])
        self.assertTrue(payload["interruptControlId"])

        controls = self._fetchall(
            "SELECT action, status, body FROM dispatch_controls WHERE run_id = ?",
            (dispatched["runs"][0]["runId"],),
        )
        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]["action"], "interrupt")
        self.assertEqual(controls[0]["status"], "pending")
        self.assertEqual(controls[0]["body"], "stop now")

    def test_session_restart_queues_spawn_request_from_stored_spec(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "restart-coder",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/repo",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        spec_id = created.json()["spawnRequest"]["spawnSpecId"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "processId": "1234"},
        )
        self.assertEqual(running.status_code, 200, running.text)
        session_id = running.json()["spawnRequest"]["sessionId"]

        restarted = self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "restart", "from_agent": "dashboard", "subject": "restart worker", "body": "continue from the dashboard"},
        )
        self.assertEqual(restarted.status_code, 200, restarted.text)
        payload = restarted.json()
        self.assertEqual(payload["session"]["status"], "restarting")
        self.assertEqual(payload["spawnRequest"]["status"], "queued")
        self.assertEqual(payload["spawnRequest"]["spawnSpecId"], spec_id)
        self.assertEqual(payload["spawnRequest"]["environmentId"], "linux:test-host:default")
        self.assertEqual(payload["spawnRequest"]["workspace"], "/workspace/repo")
        self.assertEqual(payload["spawnRequest"]["initialMessage"], "continue from the dashboard")

    def test_recovered_session_running_ends_previous_recovering_session(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "recover-coder",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/repo",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "processId": "1234"},
        )
        self.assertEqual(running.status_code, 200, running.text)
        old_session_id = running.json()["spawnRequest"]["sessionId"]

        recover = self.client.post(
            f"/api/v1/sessions/{old_session_id}/control",
            json={"action": "recover", "from_agent": "dashboard", "subject": "recover worker"},
        )
        self.assertEqual(recover.status_code, 200, recover.text)
        self.assertEqual(recover.json()["session"]["status"], "recovering")
        recover_spawn_id = recover.json()["spawnRequest"]["id"]
        claim_recover = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim_recover.status_code, 200, claim_recover.text)
        recovered_running = self.client.patch(
            f"/api/v1/spawn-requests/{recover_spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "processId": "5678"},
        )
        self.assertEqual(recovered_running.status_code, 200, recovered_running.text)
        new_session_id = recovered_running.json()["spawnRequest"]["sessionId"]
        self.assertNotEqual(new_session_id, old_session_id)

        old_session = self._fetchone("SELECT status, ended_at FROM agent_sessions WHERE id = ?", (old_session_id,))
        new_session = self._fetchone("SELECT status, ended_at FROM agent_sessions WHERE id = ?", (new_session_id,))
        self.assertEqual(old_session["status"], "ended")
        self.assertTrue(old_session["ended_at"])
        self.assertEqual(new_session["status"], "running")
        self.assertIsNone(new_session["ended_at"])

    def test_session_recover_rejects_duplicate_pending_spawn(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "single-recover-coder",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/repo",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "processId": "1234"},
        )
        self.assertEqual(running.status_code, 200, running.text)
        session_id = running.json()["spawnRequest"]["sessionId"]

        first_recover = self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "recover", "from_agent": "dashboard", "subject": "recover worker"},
        )
        self.assertEqual(first_recover.status_code, 200, first_recover.text)
        pending_spawn_id = first_recover.json()["spawnRequest"]["id"]

        duplicate_recover = self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "recover", "from_agent": "dashboard", "subject": "recover worker again"},
        )
        self.assertEqual(duplicate_recover.status_code, 409, duplicate_recover.text)
        self.assertIn(pending_spawn_id, duplicate_recover.json()["detail"])
        pending_spawns = self._fetchall(
            "SELECT id FROM spawn_requests WHERE agent_id = ? AND status IN ('queued','claimed','starting')",
            ("single-recover-coder",),
        )
        self.assertEqual([row["id"] for row in pending_spawns], [pending_spawn_id])

    def test_runtime_state_update_refreshes_current_managed_session(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "fresh-backed-coder",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/repo",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={
                "status": "running",
                "bridgeId": "bridge-current",
                "processId": "1234",
                "runtimeState": {
                    "bridgeInstanceId": "bridge-current",
                    "environmentId": "linux:test-host:default",
                    "spawnRequestId": spawn_id,
                },
            },
        )
        self.assertEqual(running.status_code, 200, running.text)
        session_id = running.json()["spawnRequest"]["sessionId"]
        self._execute(
            "UPDATE agent_sessions SET last_seen = ?, status = 'recovering' WHERE id = ?",
            ("2026-04-28T10:00:00Z", session_id),
        )

        updated = self.client.patch(
            "/api/v1/agents/fresh-backed-coder/runtime-state",
            json={
                "runtimeState": {
                    "bridgeInstanceId": "bridge-current",
                    "environmentId": "linux:test-host:default",
                    "spawnRequestId": spawn_id,
                    "threadId": "thread-current",
                }
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        row = self._fetchone("SELECT status, last_seen, session_handle FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(row["status"], "running")
        self.assertNotEqual(row["last_seen"], "2026-04-28T10:00:00Z")
        self.assertEqual(row["session_handle"], "thread-current")

        self._execute(
            "UPDATE agent_sessions SET last_seen = ? WHERE id = ?",
            ("2026-04-28T10:00:00Z", session_id),
        )
        self._execute(
            "UPDATE agents SET last_seen = ? WHERE id = ?",
            ("2026-04-29T00:00:00Z", "fresh-backed-coder"),
        )
        listed = self.client.get("/api/v1/sessions?agentId=fresh-backed-coder")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["sessions"][0]["lastSeen"], "2026-04-29T00:00:00Z")
        self.assertEqual(listed.json()["sessions"][0]["sessionHandle"], "thread-current")

    def test_session_cli_takeover_pauses_dashboard_delivery(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "takeover-coder",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/repo",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "processId": "1234"},
        )
        self.assertEqual(running.status_code, 200, running.text)
        session_id = running.json()["spawnRequest"]["sessionId"]

        takeover = self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "cli_takeover", "from_agent": "dashboard", "subject": "take over"},
        )
        self.assertEqual(takeover.status_code, 200, takeover.text)
        self.assertEqual(takeover.json()["session"]["status"], "cli-takeover")
        agent = self.client.get("/api/v1/agents").json()["agents"]["takeover-coder"]
        self.assertEqual(agent["statusRaw"], "stopped")
        self.assertEqual(agent["launchMode"], "none")
        self.assertIn("Paused for direct CLI takeover", agent["statusNote"])

        sent = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "to": "takeover-coder",
                "type": "request",
                "subject": "should not queue",
                "body": "hello",
                "trigger": True,
            },
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        sent_payload = sent.json()
        self.assertFalse(sent_payload["ok"])
        self.assertIn("agent status is", sent_payload["notStarted"][0]["reason"])

    def test_list_sessions_repairs_superseded_recovering_rows(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "repair-recover-coder",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/repo",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "processId": "1234"},
        )
        self.assertEqual(running.status_code, 200, running.text)
        old_session_id = running.json()["spawnRequest"]["sessionId"]
        spec_id = running.json()["spawnRequest"]["spawnSpecId"]
        self._execute(
            "UPDATE agent_sessions SET status = 'recovering', ended_at = ?, last_seen = ? WHERE id = ?",
            ("2026-04-28T10:00:00Z", "2026-04-28T10:00:00Z", old_session_id),
        )
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode, process_id,
                session_handle, app_server_url, spawn_spec_id, spawn_request_id,
                capabilities, telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_newer_running",
                "repair-recover-coder",
                "linux:test-host:default",
                "codex",
                "/workspace/repo",
                "managed-warm",
                "5678",
                "",
                "",
                spec_id,
                spawn_id,
                "{}",
                "{}",
                "running",
                "2026-04-28T10:00:01Z",
                "2026-04-28T10:00:01Z",
                None,
            ),
        )

        listed = self.client.get("/api/v1/sessions?agentId=repair-recover-coder")
        self.assertEqual(listed.status_code, 200, listed.text)
        by_id = {session["id"]: session for session in listed.json()["sessions"]}
        self.assertEqual(by_id[old_session_id]["status"], "ended")
        self.assertEqual(by_id["sess_newer_running"]["status"], "running")

    def test_session_stop_cancels_pending_recovery_and_late_bridge_running_is_rejected(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "cancel-recover-coder",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/repo",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        claim = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "processId": "1234"},
        )
        self.assertEqual(running.status_code, 200, running.text)
        session_id = running.json()["spawnRequest"]["sessionId"]

        recover = self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "recover", "from_agent": "dashboard", "subject": "recover worker"},
        )
        self.assertEqual(recover.status_code, 200, recover.text)
        pending_spawn_id = recover.json()["spawnRequest"]["id"]
        claim_recover = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claim_recover.status_code, 200, claim_recover.text)
        self.assertEqual(claim_recover.json()["spawnRequest"]["id"], pending_spawn_id)

        stopped = self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "stop", "from_agent": "dashboard", "body": "stop recovery"},
        )
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(stopped.json()["cancelledSpawns"], 1)
        cancelled_spawn = self._fetchone("SELECT status FROM spawn_requests WHERE id = ?", (pending_spawn_id,))
        self.assertEqual(cancelled_spawn["status"], "cancelled")

        late_running = self.client.patch(
            f"/api/v1/spawn-requests/{pending_spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "processId": "5678"},
        )
        self.assertEqual(late_running.status_code, 409, late_running.text)
        sessions = self._fetchall("SELECT id FROM agent_sessions WHERE agent_id = ?", ("cancel-recover-coder",))
        self.assertEqual(len(sessions), 1)

    def test_resident_agent_stop_control_interrupts_active_and_disables_wake(self):
        self._register("lead", role="manager", runtime="codex", sessionMode="resident", sessionHandle="lead-thread")
        self._register("resident", runtime="codex", sessionMode="resident", sessionHandle="resident-thread", bridgeId="bridge-current")

        first = self._dispatch(
            from_agent="lead",
            to="resident",
            type="request",
            subject="active",
            body="do active work",
            mode="start_if_possible",
            createMessage=True,
        )
        active_run_id = first["runs"][0]["runId"]
        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "resident", "bridgeId": "bridge-current", "executionModes": ["resident"]},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual(claim.json()["run"]["id"], active_run_id)

        second = self._dispatch(
            from_agent="lead",
            to="resident",
            type="request",
            subject="queued",
            body="do queued work",
            mode="start_if_possible",
            createMessage=True,
        )
        queued_run_id = second["runs"][0]["runId"]

        stopped = self.client.post(
            "/api/v1/agents/resident/control",
            json={"action": "stop", "from_agent": "dashboard", "body": "stop resident"},
        )
        self.assertEqual(stopped.status_code, 200, stopped.text)
        payload = stopped.json()
        self.assertEqual(payload["agent"]["statusRaw"], "stopped")
        self.assertEqual(payload["agent"]["launchMode"], "none")
        self.assertEqual(payload["agent"]["wakeMode"], "disabled")
        self.assertEqual(payload["cancelledQueued"], 1)
        self.assertTrue(payload["controlId"])

        controls = self._fetchall(
            "SELECT action, body, status FROM dispatch_controls WHERE run_id = ?",
            (active_run_id,),
        )
        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]["action"], "interrupt")
        self.assertEqual(controls[0]["body"], "stop resident")

        queued_run = self.client.get(f"/api/v1/dispatch/runs/{queued_run_id}")
        self.assertEqual(queued_run.status_code, 200, queued_run.text)
        self.assertEqual(queued_run.json()["run"]["status"], "cancelled")

        resumed = self.client.post("/api/v1/agents/resident/control", json={"action": "resume", "from_agent": "dashboard"})
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["agent"]["launchMode"], "detached")

    def test_message_read_state_is_scoped_to_recipient(self):
        self._register("manager", role="manager")
        self._register("worker")
        self._register("other")

        manager_msg = self._send_message(
            from_agent="worker",
            to="manager",
            type="info",
            subject="for manager",
            body="manager only",
        )["messageId"]
        other_msg = self._send_message(
            from_agent="worker",
            to="other",
            type="info",
            subject="for other",
            body="other only",
        )["messageId"]

        manager_before = self.client.get("/api/v1/messages/inbox/manager?filter=all&peek=true")
        self.assertEqual(manager_before.status_code, 200, manager_before.text)
        self.assertFalse(manager_before.json()["messages"][0]["read"])

        marked = self.client.post(
            f"/api/v1/messages/{manager_msg}/read",
            json={"agentId": "manager", "read": True},
        )
        self.assertEqual(marked.status_code, 200, marked.text)
        self.assertTrue(marked.json()["read"])

        manager_after = self.client.get("/api/v1/messages/inbox/manager?filter=all&peek=true")
        self.assertEqual(manager_after.status_code, 200, manager_after.text)
        self.assertTrue(manager_after.json()["messages"][0]["read"])

        wrong_recipient = self.client.post(
            f"/api/v1/messages/{other_msg}/read",
            json={"agentId": "manager", "read": True},
        )
        self.assertEqual(wrong_recipient.status_code, 403, wrong_recipient.text)

        other_after = self.client.get("/api/v1/messages/inbox/other?filter=all&peek=true")
        self.assertEqual(other_after.status_code, 200, other_after.text)
        self.assertFalse(other_after.json()["messages"][0]["read"])

    def test_spawn_request_rejects_workspace_outside_advertised_roots(self):
        self._heartbeat_environment()
        response = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "bad-workspace",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/tmp/not-allowed",
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("outside the roots", response.text)

    def test_channel_fanout_suppresses_duplicate_direct_delivery(self):
        self._register("alice", runtime="codex", sessionMode="managed")
        self._register("bob", runtime="codex", sessionMode="managed")

        response = self.client.post(
            "/api/v1/channels",
            json={"name": "review", "description": "", "createdBy": "alice"},
        )
        self.assertEqual(response.status_code, 200, response.text)

        response = self.client.post("/api/v1/channels/review/join", json={"agentId": "bob"})
        self.assertEqual(response.status_code, 200, response.text)

        direct = self._send_message(
            from_agent="alice",
            to="bob",
            subject="[REVIEW] chunk change",
            body="Same review body",
            type="review",
            trigger=True,
        )
        direct_message_id = direct["messageId"]
        direct_run_id = direct["dispatchRuns"][0]["runId"]

        channel = self.client.post(
            "/api/v1/channels/review/send",
            json={"from_agent": "alice", "channel": "review", "body": "Same review body", "type": "review", "trigger": True},
        )
        self.assertEqual(channel.status_code, 200, channel.text)
        channel_payload = channel.json()
        self.assertEqual(channel_payload["suppressedDuplicates"], ["bob"])
        self.assertEqual(channel_payload["recipients"], [])
        self.assertEqual(channel_payload["dispatchRuns"], [])

        inbox_rows = self._fetchall(
            "SELECT id, source, channel FROM messages WHERE to_agent = ? AND body = ? ORDER BY timestamp ASC",
            ("bob", "Same review body"),
        )
        self.assertEqual(len(inbox_rows), 1)
        self.assertEqual(inbox_rows[0]["id"], direct_message_id)
        self.assertEqual(inbox_rows[0]["source"], "direct")

        canonical_channel_row = self._fetchone(
            "SELECT id FROM messages WHERE channel = ? AND to_agent IS NULL AND body = ?",
            ("review", "Same review body"),
        )
        self.assertIsNotNone(canonical_channel_row)

        run_rows = self._fetchall(
            "SELECT id FROM dispatch_runs WHERE target_agent = ? AND from_agent = ? AND subject LIKE ?",
            ("bob", "alice", "%review%"),
        )
        self.assertEqual([row["id"] for row in run_rows], [direct_run_id])

    def test_channel_unread_is_scoped_to_viewer_and_mark_read(self):
        self._register("alice")
        self._register("bob")
        self._register("carol")
        created = self.client.post(
            "/api/v1/channels",
            json={"name": "team", "description": "", "createdBy": "alice"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(self.client.post("/api/v1/channels/team/join", json={"agentId": "bob"}).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/channels/team/join", json={"agentId": "carol"}).status_code, 200)

        sent = self.client.post(
            "/api/v1/channels/team/send",
            json={"from_agent": "alice", "channel": "team", "body": "hello team", "type": "info", "trigger": False},
        )
        self.assertEqual(sent.status_code, 200, sent.text)

        bob_channels = self.client.get("/api/v1/channels?agentId=bob")
        self.assertEqual(bob_channels.status_code, 200, bob_channels.text)
        self.assertEqual(bob_channels.json()["channels"][0]["unreadCount"], 1)
        alice_channels = self.client.get("/api/v1/channels?agentId=alice")
        self.assertEqual(alice_channels.status_code, 200, alice_channels.text)
        self.assertEqual(alice_channels.json()["channels"][0]["unreadCount"], 0)

        bob_detail = self.client.get("/api/v1/channels/team?agentId=bob")
        self.assertEqual(bob_detail.status_code, 200, bob_detail.text)
        message = [m for m in bob_detail.json()["messages"] if m["from"] == "alice"][0]
        self.assertFalse(message["read"])
        self.assertTrue(message["fanoutMessageId"].endswith("-bob"))

        marked = self.client.post("/api/v1/channels/team/read", json={"agentId": "bob"})
        self.assertEqual(marked.status_code, 200, marked.text)
        self.assertEqual(marked.json()["read"], 1)

        bob_channels_after = self.client.get("/api/v1/channels?agentId=bob")
        self.assertEqual(bob_channels_after.status_code, 200, bob_channels_after.text)
        self.assertEqual(bob_channels_after.json()["channels"][0]["unreadCount"], 0)
        carol_channels_after = self.client.get("/api/v1/channels?agentId=carol")
        self.assertEqual(carol_channels_after.status_code, 200, carol_channels_after.text)
        self.assertEqual(carol_channels_after.json()["channels"][0]["unreadCount"], 1)

    def test_unsending_canonical_channel_message_removes_member_fanout(self):
        self._register("alice")
        self._register("bob")
        self.client.post("/api/v1/channels", json={"name": "ops", "description": "", "createdBy": "alice"})
        self.client.post("/api/v1/channels/ops/join", json={"agentId": "bob"})
        sent = self.client.post(
            "/api/v1/channels/ops/send",
            json={"from_agent": "alice", "channel": "ops", "body": "remove this", "type": "info", "trigger": False},
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        canonical_id = sent.json()["messageId"]
        rows_before = self._fetchall("SELECT id FROM messages WHERE id = ? OR id LIKE ? ORDER BY id", (canonical_id, f"{canonical_id}-%"))
        self.assertEqual(len(rows_before), 2)

        deleted = self.client.delete(f"/api/v1/messages/{canonical_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["deleted"], 2)
        rows_after = self._fetchall("SELECT id FROM messages WHERE id = ? OR id LIKE ?", (canonical_id, f"{canonical_id}-%"))
        self.assertEqual(rows_after, [])

    def test_clear_direct_conversation_removes_only_that_dm_pair(self):
        self._register("manager")
        self._register("alice")
        self._register("bob")

        first = self._send_message(from_agent="manager", to="alice", subject="a", body="hello alice")
        second = self._send_message(from_agent="alice", to="manager", subject="b", body="hello manager")
        kept = self._send_message(from_agent="manager", to="bob", subject="c", body="hello bob")

        cleared = self.client.post(
            "/api/v1/messages/conversation/clear",
            json={"agentId": "manager", "peerId": "alice"},
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["deleted"], 2)

        removed_rows = self._fetchall(
            "SELECT id FROM messages WHERE id IN (?, ?)",
            (first["messageId"], second["messageId"]),
        )
        self.assertEqual(removed_rows, [])
        kept_row = self._fetchone("SELECT id FROM messages WHERE id = ?", (kept["messageId"],))
        self.assertIsNotNone(kept_row)

    def test_binary_artifact_upload_is_readable_from_shared_store(self):
        payload = b"\x89PNG\r\n\x1a\nfake-image-bytes"
        uploaded = self.client.post(
            "/api/v1/shared",
            data={"from_agent": "dashboard", "name": "dash.png", "description": "test image"},
            files={"file": ("dash.png", payload, "image/png")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertTrue(uploaded.json()["isBinary"])

        listed = self.client.get("/api/v1/shared")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["files"][0]["name"], "dash.png")

        read = self.client.get("/api/v1/shared/dash.png")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.content, payload)

    def test_recent_messages_returns_direct_and_canonical_channels_without_fanout(self):
        self._register("manager")
        self._register("alice")
        self._register("bob")

        direct = self._send_message(from_agent="manager", to="alice", subject="direct title", body="hello alice")
        self.client.post("/api/v1/channels", json={"name": "ops", "description": "", "createdBy": "manager"})
        self.client.post("/api/v1/channels/ops/join", json={"agentId": "alice"})
        self.client.post("/api/v1/channels/ops/join", json={"agentId": "bob"})
        channel = self.client.post(
            "/api/v1/channels/ops/send",
            json={"from_agent": "manager", "channel": "ops", "body": "channel body", "type": "info", "trigger": False},
        )
        self.assertEqual(channel.status_code, 200, channel.text)

        recent = self.client.get("/api/v1/messages/recent?limit=10")
        self.assertEqual(recent.status_code, 200, recent.text)
        messages = recent.json()["messages"]
        ids = [message["id"] for message in messages]

        self.assertIn(direct["messageId"], ids)
        self.assertIn(channel.json()["messageId"], ids)
        self.assertFalse(any(message["source"] == "channel" and message.get("to") for message in messages))

    def test_clear_inbox_detaches_threaded_replies_before_delete(self):
        self._register("alice")
        self._register("bob")

        parent = self._send_message(
            from_agent="alice",
            to="bob",
            subject="parent",
            body="hello",
            type="info",
        )
        parent_id = parent["messageId"]

        self._send_message(
            from_agent="bob",
            to="alice",
            subject="reply",
            body="done",
            type="response",
            inReplyTo=parent_id,
        )

        cleared = self.client.post("/api/v1/clear", json={"target": "inbox", "agentId": "bob"})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["deletedMessages"], 1)
        self.assertEqual(cleared.json()["cleared"]["messages"], 1)

        parent_row = self._fetchone("SELECT id FROM messages WHERE id = ?", (parent_id,))
        self.assertIsNone(parent_row)

        reply_row = self._fetchone("SELECT in_reply_to FROM messages WHERE subject = 'reply'")
        self.assertIsNotNone(reply_row)
        self.assertIsNone(reply_row["in_reply_to"])

    def test_delete_channel_detaches_replies_to_channel_messages(self):
        self._register("alice")
        self._register("bob")

        response = self.client.post(
            "/api/v1/channels",
            json={"name": "ops", "description": "", "createdBy": "alice"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post("/api/v1/channels/ops/join", json={"agentId": "bob"})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(
            "/api/v1/channels/ops/send",
            json={"from_agent": "alice", "channel": "ops", "body": "deploy now", "trigger": False},
        )
        self.assertEqual(response.status_code, 200, response.text)

        channel_message = self._fetchone(
            "SELECT id FROM messages WHERE channel = ? AND to_agent IS NULL AND body = ?",
            ("ops", "deploy now"),
        )
        self.assertIsNotNone(channel_message)

        self._send_message(
            from_agent="bob",
            to="alice",
            subject="ack",
            body="done",
            type="response",
            inReplyTo=channel_message["id"],
        )

        deleted = self.client.delete("/api/v1/channels/ops")
        self.assertEqual(deleted.status_code, 200, deleted.text)

        reply_row = self._fetchone("SELECT in_reply_to FROM messages WHERE subject = 'ack'")
        self.assertIsNotNone(reply_row)
        self.assertIsNone(reply_row["in_reply_to"])

    def test_rejects_cross_os_codex_live_cwd_registration(self):
        linux_bad = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "linux-codex",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "machineId": "linux:test-box",
                "cwd": "C:/repo/project",
                "runtimeConfig": {"appServerUrl": "ws://127.0.0.1:9000"},
            },
        )
        self.assertEqual(linux_bad.status_code, 400, linux_bad.text)
        self.assertIn("Invalid cwd", linux_bad.text)

        windows_bad = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "windows-codex",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "machineId": "win32:test-box",
                "cwd": "/mnt/c/repo/project",
                "runtimeConfig": {"appServerUrl": "ws://127.0.0.1:9000"},
            },
        )
        self.assertEqual(windows_bad.status_code, 400, windows_bad.text)
        self.assertIn("Invalid cwd", windows_bad.text)

    def test_dispatch_requires_reply_and_auto_mirrors_completed_run_handoff(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="slice",
            body="implement it",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]

        initial = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertTrue(initial.json()["run"]["requireReply"])
        self.assertEqual(initial.json()["run"]["replyState"], "awaiting")
        self.assertFalse(initial.json()["run"]["replyPending"])

        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "done"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        final = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(final.status_code, 200, final.text)
        self.assertTrue(final.json()["run"]["requireReply"])
        self.assertEqual(final.json()["run"]["replyState"], "sent")
        self.assertFalse(final.json()["run"]["replyPending"])
        self.assertTrue(final.json()["run"]["resultMessageId"])

    def test_dashboard_dispatch_auto_handoff_uses_clean_chat_body(self):
        self._register("coder", runtime="codex", sessionMode="managed")

        created = self._dispatch(
            from_agent="dashboard",
            to="coder",
            type="request",
            subject="hello",
            body="say hi",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]

        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "hi back"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        final = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(final.status_code, 200, final.text)
        result_message_id = final.json()["run"]["resultMessageId"]
        self.assertTrue(result_message_id)

        inbox = self.client.get(f"/api/v1/messages/inbox/dashboard?messageId={result_message_id}")
        self.assertEqual(inbox.status_code, 200, inbox.text)
        message = inbox.json()["messages"][0]
        self.assertEqual(message["body"], "hi back")

    def test_completed_run_late_reply_links_result_message_id(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="slice",
            body="implement it",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        source_message_id = created["messageId"]

        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "done"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        reply = self._send_message(
            from_agent="coder",
            to="lead",
            type="response",
            subject="done",
            body="ship it",
            inReplyTo=source_message_id,
            trigger=False,
        )

        final = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(final.status_code, 200, final.text)
        self.assertEqual(final.json()["run"]["status"], "completed")
        self.assertEqual(final.json()["run"]["resultMessageId"], reply["messageId"])
        self.assertEqual(final.json()["run"]["replyState"], "sent")
        self.assertFalse(final.json()["run"]["replyPending"])

    def test_reply_during_running_run_records_handoff_without_finishing_run(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="slice",
            body="implement it",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        source_message_id = created["messageId"]

        started = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "running"},
        )
        self.assertEqual(started.status_code, 200, started.text)

        reply = self._send_message(
            from_agent="coder",
            to="lead",
            type="response",
            subject="status",
            body="still working",
            inReplyTo=source_message_id,
            trigger=False,
        )

        mid_run = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(mid_run.status_code, 200, mid_run.text)
        self.assertEqual(mid_run.json()["run"]["status"], "running")
        self.assertEqual(mid_run.json()["run"]["resultMessageId"], reply["messageId"])
        self.assertEqual(mid_run.json()["run"]["replyState"], "sent")
        self.assertFalse(mid_run.json()["run"]["replyPending"])

    def test_unthreaded_response_links_latest_pending_run_for_pair(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="slice",
            body="implement it",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]

        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "done"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        reply = self._send_message(
            from_agent="coder",
            to="lead",
            type="response",
            subject="done",
            body="finished",
            trigger=False,
        )

        final = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(final.status_code, 200, final.text)
        self.assertEqual(final.json()["run"]["resultMessageId"], reply["messageId"])
        self.assertEqual(final.json()["run"]["replyState"], "sent")
        self.assertFalse(final.json()["run"]["replyPending"])

    def test_triggered_response_send_does_not_require_another_reply(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        request_send = self._send_message(
            from_agent="lead",
            to="coder",
            type="request",
            subject="work",
            body="please do it",
            trigger=True,
        )
        self.assertTrue(request_send["dispatchRuns"][0]["requireReply"])

        response_send = self._send_message(
            from_agent="coder",
            to="lead",
            type="response",
            subject="done",
            body="finished",
            trigger=True,
        )
        self.assertFalse(response_send["dispatchRuns"][0]["requireReply"])

    def test_triggered_info_send_requires_reply_by_default(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        sent = self._send_message(
            from_agent="lead",
            to="coder",
            type="info",
            subject="heads up",
            body="ack this",
            trigger=True,
        )
        self.assertTrue(sent["dispatchRuns"][0]["requireReply"])

    def test_triggered_review_and_error_sends_expect_reply_by_default(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("reviewer", runtime="codex", sessionMode="managed")
        self._register("debugger", runtime="codex", sessionMode="managed")

        for message_type, target in (("review", "reviewer"), ("error", "debugger")):
            sent = self._send_message(
                from_agent="lead",
                to=target,
                type=message_type,
                subject=f"{message_type} handoff",
                body="please respond when handled",
                trigger=True,
            )
            self.assertTrue(sent["dispatchRuns"][0]["requireReply"])

    def test_triggered_send_to_offline_agent_is_not_written(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")
        self._execute("UPDATE agents SET last_seen = ? WHERE id = ?", ("2000-01-01T00:00:00Z", "coder"))

        sent = self._send_message(
            from_agent="lead",
            to="coder",
            type="request",
            subject="offline work",
            body="please do it",
            trigger=True,
        )

        self.assertFalse(sent["ok"])
        self.assertEqual(sent["error"], "Message was not sent because one or more recipients cannot start live work now.")
        self.assertEqual(sent["notStarted"][0]["reason"], 'agent status is "offline"')
        self.assertEqual(sent["notStarted"][0]["recipientStatus"], "offline")
        self.assertEqual(sent["dispatchRuns"], [])
        self.assertNotIn("messageId", sent)
        stored = self._fetchone("SELECT id FROM messages WHERE to_agent = ? AND subject = ?", ("coder", "offline work"))
        self.assertIsNone(stored)

    def test_blocked_and_completed_agent_statuses_do_not_block_live_send(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("blocked-agent", runtime="codex", sessionMode="managed", status="blocked")
        self._register("completed-agent", runtime="codex", sessionMode="managed", status="completed")

        for target in ("blocked-agent", "completed-agent"):
            sent = self._send_message(
                from_agent="lead",
                to=target,
                type="request",
                subject=f"next for {target}",
                body="please continue",
                trigger=True,
            )
            self.assertTrue(sent["ok"])
            self.assertEqual(sent["notStarted"], [])
            self.assertEqual(sent["dispatchRuns"][0]["targetAgentId"], target)

    def test_reply_dispatch_links_result_message_id_and_suppresses_mirror_unread(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="slice",
            body="implement it",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        source_message_id = created["messageId"]

        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "done"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        mirror_id = "mirror-msg"
        self._execute(
            """
            INSERT INTO messages (
                id, from_agent, to_agent, source, type, subject, body, priority,
                dispatch_requested, in_reply_to, timestamp
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                mirror_id,
                "coder",
                "lead",
                "direct",
                "response",
                "Re: slice",
                "Auto-mirrored dispatch result because no explicit reply message was sent during the run.\n\nRun completed.",
                "normal",
                0,
                source_message_id,
                1776900000000,
            ),
        )

        reply_dispatch = self._dispatch(
            from_agent="coder",
            to="lead",
            type="response",
            subject="done",
            body="ship it",
            inReplyTo=source_message_id,
            mode="start_if_possible",
            createMessage=True,
            requireReply=False,
        )

        final = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(final.status_code, 200, final.text)
        self.assertEqual(final.json()["run"]["resultMessageId"], reply_dispatch["messageId"])
        self.assertEqual(final.json()["run"]["replyState"], "sent")

        mirror_receipt = self._fetchone(
            "SELECT read_at FROM read_receipts WHERE message_id = ? AND agent_id = ?",
            (mirror_id, "lead"),
        )
        self.assertIsNotNone(mirror_receipt)
        self.assertTrue(mirror_receipt["read_at"])

    def test_multi_recipient_send_tracks_per_recipient_message_ids(self):
        self._register("lead", role="manager", runtime="codex", sessionMode="managed")
        self._register("alice", runtime="codex", sessionMode="managed")
        self._register("bob", runtime="codex", sessionMode="managed")

        sent = self._send_message(
            from_agent="lead",
            toRole="coder",
            type="request",
            subject="work",
            body="do it",
            trigger=True,
        )
        alice_message = self._fetchone(
            "SELECT id FROM messages WHERE to_agent = ? ORDER BY timestamp DESC LIMIT 1",
            ("alice",),
        )["id"]
        bob_message = self._fetchone(
            "SELECT id FROM messages WHERE to_agent = ? ORDER BY timestamp DESC LIMIT 1",
            ("bob",),
        )["id"]

        reply = self._send_message(
            from_agent="alice",
            to="lead",
            type="response",
            subject="done",
            body="ship it",
            inReplyTo=alice_message,
            trigger=False,
        )

        runs_by_target = {}
        for run in sent["dispatchRuns"]:
            payload = self.client.get(f"/api/v1/dispatch/runs/{run['runId']}")
            self.assertEqual(payload.status_code, 200, payload.text)
            runs_by_target[run["targetAgentId"]] = payload.json()["run"]

        self.assertEqual(runs_by_target["alice"]["messageId"], alice_message)
        self.assertEqual(runs_by_target["alice"]["resultMessageId"], reply["messageId"])
        self.assertEqual(runs_by_target["alice"]["replyState"], "sent")
        self.assertEqual(runs_by_target["bob"]["messageId"], bob_message)
        self.assertEqual(runs_by_target["bob"]["replyState"], "awaiting")

    def test_triggered_send_rejects_existing_future_queue_without_writing_message(self):
        self._register("lead", role="manager", runtime="codex", sessionMode="managed")
        self._register("worker", runtime="codex", sessionMode="managed", restoreDeleted=True)

        first = self._dispatch(
            from_agent="lead",
            to="worker",
            type="request",
            subject="first",
            body="one",
            mode="start_if_possible",
            createMessage=True,
        )
        first_message_id = first["messageId"]

        second = self._send_message(
            from_agent="lead",
            to="worker",
            type="request",
            subject="second",
            body="two",
            trigger=True,
        )
        self.assertFalse(second["ok"])
        self.assertEqual(second["dispatchRuns"], [])
        self.assertEqual(second["notStarted"][0]["reason"], "agent already has queued work")
        self.assertNotIn("messageId", second)
        second_message = self._fetchone("SELECT id FROM messages WHERE subject = ?", ("second",))
        self.assertIsNone(second_message)

        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "worker", "machineId": "", "bridgeId": "bridge-1", "executionModes": ["managed"]},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual(claim.json()["run"]["id"], first["runs"][0]["runId"])

        receipts = self._fetchall(
            "SELECT message_id FROM read_receipts WHERE agent_id = ? ORDER BY message_id",
            ("worker",),
        )
        self.assertEqual({row["message_id"] for row in receipts}, {first_message_id})

    def test_codex_claim_rejects_stale_bridge_not_matching_current_runtime_state(self):
        self._register("lead", role="manager", runtime="codex", sessionMode="managed")
        self._register("worker", runtime="codex", sessionMode="managed", bridgeId="bridge-current")
        state = self.client.patch(
            "/api/v1/agents/worker/runtime-state",
            json={"runtimeState": {"bridgeInstanceId": "bridge-current"}},
        )
        self.assertEqual(state.status_code, 200, state.text)

        created = self._dispatch(
            from_agent="lead",
            to="worker",
            type="request",
            subject="work",
            body="do it",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]

        stale_claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "worker", "bridgeId": "bridge-old", "executionModes": ["managed"]},
        )
        self.assertEqual(stale_claim.status_code, 200, stale_claim.text)
        stale_payload = stale_claim.json()
        self.assertIsNone(stale_payload["run"])
        self.assertEqual(stale_payload["blockedBy"]["reason"], "bridge_not_current")

        run = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["run"]["status"], "queued")

        current_claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "worker", "bridgeId": "bridge-current", "executionModes": ["managed"]},
        )
        self.assertEqual(current_claim.status_code, 200, current_claim.text)
        self.assertEqual(current_claim.json()["run"]["id"], run_id)

    def test_claude_channel_claim_is_not_rejected_by_stdio_bridge_id(self):
        self._register("lead", role="manager")
        self._register("worker", runtime="claude-code", sessionMode="resident", bridgeId="stdio-current")
        state = self.client.patch(
            "/api/v1/agents/worker/runtime-state",
            json={"runtimeState": {"bridgeInstanceId": "stdio-current"}},
        )
        self.assertEqual(state.status_code, 200, state.text)

        created = self._dispatch(
            from_agent="lead",
            to="worker",
            type="request",
            subject="work",
            body="do it",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]

        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "worker", "bridgeId": "channel-test-machine", "executionModes": ["resident"]},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual(claim.json()["run"]["id"], run_id)

    def test_multi_recipient_dispatch_tracks_per_recipient_message_ids(self):
        self._register("lead", role="manager", runtime="codex", sessionMode="managed")
        self._register("alice", runtime="codex", sessionMode="managed")
        self._register("bob", runtime="codex", sessionMode="managed")

        created = self._dispatch(
            from_agent="lead",
            toRole="coder",
            type="request",
            subject="work",
            body="do it",
            mode="start_if_possible",
            createMessage=True,
        )
        alice_message = self._fetchone(
            "SELECT id FROM messages WHERE to_agent = ? ORDER BY timestamp DESC LIMIT 1",
            ("alice",),
        )["id"]
        bob_message = self._fetchone(
            "SELECT id FROM messages WHERE to_agent = ? ORDER BY timestamp DESC LIMIT 1",
            ("bob",),
        )["id"]

        reply = self._dispatch(
            from_agent="alice",
            to="lead",
            type="response",
            subject="done",
            body="ship it",
            inReplyTo=alice_message,
            mode="start_if_possible",
            createMessage=True,
            requireReply=False,
        )

        runs_by_target = {}
        for run in created["runs"]:
            payload = self.client.get(f"/api/v1/dispatch/runs/{run['runId']}")
            self.assertEqual(payload.status_code, 200, payload.text)
            runs_by_target[run["targetAgentId"]] = payload.json()["run"]

        self.assertEqual(runs_by_target["alice"]["messageId"], alice_message)
        self.assertEqual(runs_by_target["alice"]["resultMessageId"], reply["messageId"])
        self.assertEqual(runs_by_target["alice"]["replyState"], "sent")
        self.assertEqual(runs_by_target["bob"]["messageId"], bob_message)
        self.assertEqual(runs_by_target["bob"]["replyState"], "awaiting")

    def test_unregister_agent_cancels_nonterminal_runs_before_recreate(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("worker", runtime="codex", sessionMode="managed", restoreDeleted=True)

        created = self._dispatch(
            from_agent="lead",
            to="worker",
            type="request",
            subject="work",
            body="do it",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]

        deleted = self.client.delete("/api/v1/agents/worker")
        self.assertEqual(deleted.status_code, 200, deleted.text)

        run = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(run.status_code, 200, run.text)
        payload = run.json()["run"]
        self.assertEqual(payload["status"], "cancelled")
        self.assertIn("removed", payload["summary"])

        self._register("worker", runtime="codex", sessionMode="managed", restoreDeleted=True)
        claim = self.client.post("/api/v1/dispatch/claim", json={"agentId": "worker"})
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertIsNone(claim.json()["run"])

    def test_inbox_headers_mode_and_message_id_lookup(self):
        self._register("alice")
        self._register("bob")

        sent = self._send_message(
            from_agent="alice",
            to="bob",
            subject="hello",
            body="body line 1\nbody line 2\nbody line 3",
            type="info",
        )
        message_id = sent["messageId"]

        headers = self.client.get("/api/v1/messages/inbox/bob?mode=headers&limit=1")
        self.assertEqual(headers.status_code, 200, headers.text)
        headers_payload = headers.json()
        self.assertEqual(headers_payload["total"], 1)
        self.assertEqual(headers_payload["messages"][0]["id"], message_id)
        self.assertIn("preview", headers_payload["messages"][0])
        self.assertNotIn("body", headers_payload["messages"][0])

        body_lookup = self.client.get(f"/api/v1/messages/inbox/bob?messageId={message_id}")
        self.assertEqual(body_lookup.status_code, 200, body_lookup.text)
        body_payload = body_lookup.json()
        self.assertEqual(body_payload["total"], 1)
        self.assertEqual(body_payload["messages"][0]["id"], message_id)
        self.assertEqual(body_payload["messages"][0]["body"], "body line 1\nbody line 2\nbody line 3")

    def test_dispatch_rejects_message_only_mode(self):
        self._register("alice", runtime="codex", sessionMode="managed")
        self._register("bob", runtime="codex", sessionMode="managed")

        response = self.client.post(
            "/api/v1/dispatch",
            json={
                "from_agent": "alice",
                "to": "bob",
                "type": "request",
                "subject": "hello",
                "body": "world",
                "mode": "message_only",
                "createMessage": True,
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("mode='message_only'", response.text)
        self.assertIn("comms_send", response.text)

    def test_dispatch_rejects_create_message_false(self):
        self._register("alice", runtime="codex", sessionMode="managed")
        self._register("bob", runtime="codex", sessionMode="managed")

        response = self.client.post(
            "/api/v1/dispatch",
            json={
                "from_agent": "alice",
                "to": "bob",
                "type": "request",
                "subject": "hello",
                "body": "world",
                "mode": "start_if_possible",
                "createMessage": False,
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("Input should be True", response.text)

    def test_repair_pending_handoffs_mirrors_terminal_run_result(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="slice",
            body="implement it",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "ready for review"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        repair = self.client.post("/api/v1/dispatch/handoffs/repair")
        self.assertEqual(repair.status_code, 200, repair.text)
        self.assertEqual(repair.json()["mirrored"], 0)

        final = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(final.status_code, 200, final.text)
        result_message_id = final.json()["run"]["resultMessageId"]
        self.assertTrue(result_message_id)
        self.assertEqual(final.json()["run"]["replyState"], "sent")

        inbox = self.client.get(f"/api/v1/messages/inbox/lead?messageId={result_message_id}")
        self.assertEqual(inbox.status_code, 200, inbox.text)
        message = inbox.json()["messages"][0]
        self.assertIn("Auto-mirrored dispatch result", message["body"])
        self.assertIn("ready for review", message["body"])

    def test_claude_delivery_only_runs_do_not_count_as_pending_handoffs(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("tester", runtime="claude-code", sessionMode="resident")

        created = self._dispatch(
            from_agent="lead",
            to="tester",
            type="request",
            subject="test it",
            body="run checks",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        delivered = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={
                "status": "completed",
                "summary": "Delivered to Claude resident session",
                "runtime": "claude-code",
            },
        )
        self.assertEqual(delivered.status_code, 200, delivered.text)

        run = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["run"]["replyState"], "awaiting")
        self.assertFalse(run.json()["run"]["replyPending"])

        stats = self.client.get("/api/v1/stats")
        self.assertEqual(stats.status_code, 200, stats.text)
        self.assertEqual(stats.json()["dispatch_reply_pending"], 0)

    def test_deleted_agent_tombstone_blocks_auto_reregister_until_explicit_restore(self):
        self._register("worker", runtime="codex", sessionMode="resident", bridgeId="bridge-1")

        deleted = self.client.delete("/api/v1/agents/worker")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["ok"])

        get_deleted = self.client.get("/api/v1/agents/worker")
        self.assertEqual(get_deleted.status_code, 410, get_deleted.text)

        auto = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "worker",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "bridgeId": "bridge-1",
                "autoRegister": True,
            },
        )
        self.assertEqual(auto.status_code, 410, auto.text)

        restored = self._register(
            "worker",
            runtime="codex",
            sessionMode="resident",
            bridgeId="bridge-1",
            restoreDeleted=True,
        )
        self.assertTrue(restored["ok"])

        get_restored = self.client.get("/api/v1/agents/worker")
        self.assertEqual(get_restored.status_code, 200, get_restored.text)

    def test_clear_agents_can_remove_one_agent_and_tombstone_it(self):
        self._register("alice")
        self._register("bob")

        cleared = self.client.post("/api/v1/clear", json={"target": "agents", "agentId": "alice"})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["cleared"]["agents"], 1)

        alice = self.client.get("/api/v1/agents/alice")
        self.assertEqual(alice.status_code, 410, alice.text)

        bob = self.client.get("/api/v1/agents/bob")
        self.assertEqual(bob.status_code, 200, bob.text)
