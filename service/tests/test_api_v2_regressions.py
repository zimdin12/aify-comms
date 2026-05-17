import asyncio
import json
import tempfile
import unittest
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import get_db, init_db
from service.routers import api_v2
from service.routers.api_v2 import router


class _DummyWS:
    def __init__(self):
        self.broadcasts = []
        self.notifications = []

    async def broadcast(self, *_args, **_kwargs):
        self.broadcasts.append((_args, _kwargs))
        return None

    async def notify_agent(self, *_args, **_kwargs):
        self.notifications.append((_args, _kwargs))
        return None


class ApiV2RegressionTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test.db"
        asyncio.run(init_db(self._db_path))

        app = FastAPI()
        self.ws = _DummyWS()
        app.state.ws_manager = self.ws
        app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        app.state.testing = True
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

    def test_channel_join_leave_are_idempotent_and_validate_channel(self):
        self._register("alice")
        self._register("bob")

        response = self.client.post(
            "/api/v1/channels",
            json={"name": "room", "description": "", "createdBy": "alice"},
        )
        self.assertEqual(response.status_code, 200, response.text)

        first_join = self.client.post("/api/v1/channels/room/join", json={"agentId": "bob"})
        self.assertEqual(first_join.status_code, 200, first_join.text)
        self.assertTrue(first_join.json()["changed"])
        second_join = self.client.post("/api/v1/channels/room/join", json={"agentId": "bob"})
        self.assertEqual(second_join.status_code, 200, second_join.text)
        self.assertFalse(second_join.json()["changed"])

        channel = self.client.get("/api/v1/channels/room")
        self.assertEqual(channel.status_code, 200, channel.text)
        self.assertEqual([message["body"] for message in channel.json()["messages"]].count("bob joined the channel"), 1)

        first_leave = self.client.post("/api/v1/channels/room/leave", json={"agentId": "bob"})
        self.assertEqual(first_leave.status_code, 200, first_leave.text)
        self.assertTrue(first_leave.json()["changed"])
        second_leave = self.client.post("/api/v1/channels/room/leave", json={"agentId": "bob"})
        self.assertEqual(second_leave.status_code, 200, second_leave.text)
        self.assertFalse(second_leave.json()["changed"])

        channel = self.client.get("/api/v1/channels/room")
        self.assertEqual(channel.status_code, 200, channel.text)
        self.assertEqual([message["body"] for message in channel.json()["messages"]].count("bob left the channel"), 1)

        missing = self.client.post("/api/v1/channels/missing/leave", json={"agentId": "bob"})
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_environment_heartbeat_upserts_persistent_record(self):
        payload = {
            "id": "wsl:test-host:default",
            "label": "WSL on test-host",
            "machineId": "wsl-Ubuntu:test-host",
            "os": "linux",
            "kind": "wsl",
            "bridgeId": "bridge-1",
            "bridgeVersion": "4.0.0",
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

    def test_environment_heartbeat_ignores_flag_like_roots(self):
        env = self._heartbeat_environment(cwdRoots=["/workspace", "--help", "-h", "/extra"])

        self.assertEqual(env["cwdRoots"], ["/workspace", "/extra"])
        self.assertEqual(env["metadata"]["advertisedCwdRoots"], ["/workspace", "/extra"])

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
        self.assertIn("closeTransientDetails", dashboard.text)
        self.assertIn("_dashboardUiVersion", dashboard.text)
        self.assertIn("persistentOpenDetails", dashboard.text)
        self.assertIn(".actions-menu,.chat-send-options", dashboard.text)
        self.assertIn("xterm.min.css", dashboard.text)
        self.assertIn("xterm.min.js", dashboard.text)
        self.assertIn("addon-fit.min.js", dashboard.text)
        self.assertIn(".console-head{display:grid", dashboard.text)
        self.assertIn(".console-output{min-height:0;overflow:hidden", dashboard.text)
        self.assertIn(".console-output .xterm{height:100%;width:100%", dashboard.text)
        self.assertIn(".console-direct-row", dashboard.text)
        self.assertIn(".console-input-row .btn{min-height:32px;height:32px", dashboard.text)
        self.assertIn("const body = text.endsWith('\\r') ? text : `${text}\\r`;", dashboard.text)
        self.assertIn("codex --no-alt-screen resume --include-non-interactive", dashboard.text)
        self.assertNotIn("codex-aify${agentFlag} resume --include-non-interactive", dashboard.text)
        self.assertIn("stripTerminalControlSequences", dashboard.text)
        self.assertIn("mountConsoleTerminal(terminalId, output", dashboard.text)
        self.assertIn("fitConsoleTerminal", dashboard.text)
        self.assertIn("ResizeObserver", dashboard.text)
        self.assertIn("/resize", dashboard.text)
        self.assertIn("window.Terminal", dashboard.text)
        self.assertNotIn("${esc(meta.id)} Console", dashboard.text)
        self.assertIn("Advanced run control", dashboard.text)
        self.assertIn("Normal users and agents should send messages, not dispatches.", dashboard.text)
        self.assertIn("instructions-tabs", dashboard.text)
        self.assertIn("showInstructionsTab('daily')", dashboard.text)
        self.assertIn("data-instruction-panel=\"sessions\"", dashboard.text)
        self.assertIn("chat-channel-add-member", dashboard.text)
        self.assertIn("Add member", dashboard.text)
        self.assertIn("data-channel-member-select", dashboard.text)
        self.assertIn("chat-online-only", dashboard.text)
        self.assertIn("Online only", dashboard.text)
        self.assertIn("chat-peek-mode", dashboard.text)
        self.assertIn("Peek mode", dashboard.text)
        self.assertIn("markSelectedChatRead()", dashboard.text)
        self.assertIn("chat-send-btn", dashboard.text)
        self.assertIn("sendChatMessage({queueIfBusy:true})", dashboard.text)
        self.assertIn("setChatSending(true, queueIfBusy)", dashboard.text)
        self.assertIn(">Queue</button>", dashboard.text)
        self.assertNotIn("chat-queue-if-busy", dashboard.text)
        self.assertIn("sessions-grid", dashboard.text)
        self.assertIn("chat-mode-console", dashboard.text)
        self.assertIn('onclick="startConsoleForSelected', dashboard.text)
        self.assertIn('onclick="refreshSelectedConsole()', dashboard.text)
        self.assertIn('onclick="stopSelectedConsole()', dashboard.text)
        self.assertIn("Console unavailable", dashboard.text)
        self.assertIn("deleteSessionRecord(session.id)", dashboard.text)
        self.assertIn("table-wrap", dashboard.text)
        self.assertIn("Click command to copy", dashboard.text)
        self.assertIn("Pause for CLI", dashboard.text)
        self.assertIn("data-agent-edit-env", dashboard.text)
        self.assertIn("Edit workspace roots", dashboard.text)
        self.assertIn("Edit identity ID", dashboard.text)
        self.assertIn("Managed Runtime Policy", dashboard.text)
        self.assertIn("Dashboard Title", dashboard.text)
        self.assertIn("Color Scheme", dashboard.text)
        self.assertIn("Palette", dashboard.text)
        self.assertIn("s-dashboard-primary", dashboard.text)
        self.assertIn("theme-preview-grid", dashboard.text)
        self.assertIn("s-dashboard-secondary", dashboard.text)
        self.assertIn("s-dashboard-tertiary", dashboard.text)
        self.assertIn("value=\"ocean\"", dashboard.text)
        self.assertIn("value=\"graphite\"", dashboard.text)
        self.assertIn("value=\"crimson\"", dashboard.text)
        self.assertIn("value=\"indigo\"", dashboard.text)
        self.assertIn("--accent:#d34b64", dashboard.text)
        self.assertIn("--accent-contrast:#fff7fa", dashboard.text)
        self.assertIn("btn-primary{background:var(--accent)", dashboard.text)
        self.assertIn("segmented button.active{background:var(--secondary)", dashboard.text)
        self.assertIn("--state-online:#4fc17b", dashboard.text)
        self.assertNotIn("setProperty('--blue'", dashboard.text)
        self.assertIn("chart-line{fill:none;stroke:var(--accent)", dashboard.text)
        self.assertIn(".sidebar.collapsed{width:52px;padding:56px 8px 16px", dashboard.text)
        self.assertIn(".sidebar.collapsed .toggle-btn{right:13px;top:16px", dashboard.text)
        self.assertIn("value=\"12\" onchange=\"regenerateContinuePacket", dashboard.text)
        self.assertIn("[clipped ", dashboard.text)
        self.assertNotIn("id=\"status-dot\"", dashboard.text)
        self.assertIn("setAnalyticsRange('all')", dashboard.text)
        self.assertIn("toggleIssueMute", dashboard.text)
        self.assertIn("contract-show-hidden", dashboard.text)
        self.assertIn("dismissContract", dashboard.text)
        self.assertIn("Runtime Session", dashboard.text)
        self.assertIn("Wake path", dashboard.text)
        self.assertIn("Unread here", dashboard.text)
        self.assertIn("s-claude-model", dashboard.text)
        self.assertIn("s-claude-effort", dashboard.text)
        self.assertIn("s-codex-model", dashboard.text)
        self.assertIn("s-codex-effort", dashboard.text)
        self.assertNotIn("env-spawn-effort", dashboard.text)
        self.assertNotIn("agent-edit-effort", dashboard.text)
        self.assertIn("Compaction History", dashboard.text)
        self.assertNotIn("assignAgentEnvironment", dashboard.text)

    def test_settings_include_dashboard_appearance_defaults(self):
        settings = self.client.get("/api/v1/settings")
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertEqual(settings.json()["dashboard_title"], "AIFY Comms")
        self.assertEqual(settings.json()["dashboard_theme"], "default")
        self.assertEqual(settings.json()["dashboard_primary_color"], "")
        self.assertEqual(settings.json()["dashboard_secondary_color"], "")
        self.assertEqual(settings.json()["dashboard_tertiary_color"], "")

        updated = self.client.put(
            "/api/v1/settings",
            json={
                "dashboard_title": "Sand Castle Comms",
                "dashboard_theme": "ember",
                "dashboard_primary_color": "#f2b76e",
                "dashboard_secondary_color": "#8ebaf1",
                "dashboard_tertiary_color": "#e78776",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["dashboard_title"], "Sand Castle Comms")
        self.assertEqual(updated.json()["dashboard_theme"], "ember")
        self.assertEqual(updated.json()["dashboard_primary_color"], "#f2b76e")
        self.assertEqual(updated.json()["dashboard_secondary_color"], "#8ebaf1")
        self.assertEqual(updated.json()["dashboard_tertiary_color"], "#e78776")

    def test_analytics_range_filters_run_mix_and_all_time_series(self):
        self._register("lead")
        self._register("coder")
        self._send_message(
            from_agent="lead",
            to="coder",
            type="request",
            subject="recent",
            body="recent body",
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, from_agent, target_agent, message_type, subject, body,
                status, requested_at, finished_at
            ) VALUES
                ('run_recent', 'lead', 'coder', 'request', 'recent', 'recent body', 'completed', '2099-01-01T00:00:00Z', '2099-01-01T00:00:00Z'),
                ('run_old', 'lead', 'coder', 'request', 'old', 'old body', 'failed', '2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z')
            """,
        )

        scoped = self.client.get("/api/v1/analytics?range=hour")
        self.assertEqual(scoped.status_code, 200, scoped.text)
        self.assertEqual(scoped.json()["rangeLabel"], "last 24 hours")
        self.assertEqual(scoped.json()["runsByStatus"].get("failed", 0), 0)
        self.assertEqual(scoped.json()["runsByStatus"].get("completed", 0), 1)

        all_time = self.client.get("/api/v1/analytics?range=all")
        self.assertEqual(all_time.status_code, 200, all_time.text)
        self.assertEqual(all_time.json()["rangeLabel"], "all time")
        self.assertEqual(all_time.json()["runsByStatus"].get("failed", 0), 1)
        self.assertTrue(all_time.json()["messagesPerAllTime"])

    def test_managed_claude_spawn_uses_settings_default_model(self):
        self._heartbeat_environment(
            id="windows:test-host:default",
            bridgeId="bridge-current",
            machineId="win32:test-host",
            os="windows",
            kind="windows",
            cwdRoots=["C:/workspace"],
            runtimes=[
                {
                    "runtime": "claude-code",
                    "modes": ["managed-warm"],
                    "capabilities": {"bridgeResume": True, "interrupt": True},
                }
            ],
        )
        settings = self.client.put(
            "/api/v1/settings",
            json={"managed_claude_model": "opus", "managed_claude_effort": "medium"},
        )
        self.assertEqual(settings.status_code, 200, settings.text)

        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "windows:test-host:default",
                "agentId": "default-claude",
                "role": "manager",
                "runtime": "claude-code",
                "workspace": "C:/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn = created.json()["spawnRequest"]
        self.assertEqual(spawn["spawnSpec"]["model"], "opus")
        self.assertEqual(spawn["spawnSpec"]["metadata"]["runtimeConfig"]["effort"], "medium")

        updated = self.client.patch(
            f"/api/v1/spawn-requests/{spawn['id']}",
            json={
                "status": "running",
                "bridgeId": "bridge-current",
                "machineId": "win32:test-host",
                "sessionHandle": "claude-session-default",
                "runtimeState": {"sessionId": "claude-session-default"},
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        agent = self._fetchone("SELECT model, runtime_config FROM agents WHERE id = ?", ("default-claude",))
        self.assertEqual(agent["model"], "opus")
        self.assertEqual(json.loads(agent["runtime_config"])["effort"], "medium")

    def test_managed_spawn_blank_model_uses_runtime_default_latest(self):
        self._heartbeat_environment(id="wsl:test-host:default", bridgeId="bridge-current")
        settings = self.client.get("/api/v1/settings")
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertEqual(settings.json().get("managed_codex_model"), "")
        self.assertEqual(settings.json().get("managed_claude_model"), "")
        self.assertEqual(settings.json().get("managed_pi_model"), "")

        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "wsl:test-host:default",
                "agentId": "runtime-default-codex",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn = created.json()["spawnRequest"]
        self.assertEqual(spawn["spawnSpec"]["model"], "")
        self.assertEqual(spawn["spawnSpec"]["metadata"]["runtimeConfig"]["effort"], "high")

    def test_managed_pi_spawn_uses_settings_defaults_and_persists_runtime_config(self):
        self._heartbeat_environment(
            id="linux:test-host:default",
            bridgeId="bridge-current",
            runtimes=[{"runtime": "pi", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        settings = self.client.put(
            "/api/v1/settings",
            json={"managed_pi_model": "gpt-5.5", "managed_pi_effort": "high"},
        )
        self.assertEqual(settings.status_code, 200, settings.text)

        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "default-pi",
                "role": "coder",
                "runtime": "pi",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn = created.json()["spawnRequest"]
        self.assertEqual(spawn["spawnSpec"]["model"], "gpt-5.5")
        self.assertEqual(spawn["spawnSpec"]["metadata"]["runtimeConfig"]["effort"], "high")

        updated = self.client.patch(
            f"/api/v1/spawn-requests/{spawn['id']}",
            json={
                "status": "running",
                "bridgeId": "bridge-current",
                "machineId": "linux:test-host",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        agent = self._fetchone("SELECT model, runtime_config, capabilities FROM agents WHERE id = ?", ("default-pi",))
        self.assertEqual(agent["model"], "gpt-5.5")
        self.assertEqual(json.loads(agent["runtime_config"])["effort"], "high")
        self.assertIn("steer", json.loads(agent["capabilities"]))

    def test_managed_codex_spawn_uses_settings_defaults_and_persists_runtime_config(self):
        self._heartbeat_environment(id="wsl:test-host:default", bridgeId="bridge-current")
        settings = self.client.put(
            "/api/v1/settings",
            json={"managed_codex_model": "gpt-test-default", "managed_codex_effort": "xhigh"},
        )
        self.assertEqual(settings.status_code, 200, settings.text)

        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "wsl:test-host:default",
                "agentId": "default-codex",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn = created.json()["spawnRequest"]
        self.assertEqual(spawn["spawnSpec"]["model"], "gpt-test-default")
        self.assertEqual(spawn["spawnSpec"]["metadata"]["runtimeConfig"]["effort"], "xhigh")

        updated = self.client.patch(
            f"/api/v1/spawn-requests/{spawn['id']}",
            json={
                "status": "running",
                "bridgeId": "bridge-current",
                "machineId": "linux:test-host",
                "sessionHandle": "thread-default",
                "runtimeState": {"threadId": "thread-default"},
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        agent = self._fetchone("SELECT model, runtime_config FROM agents WHERE id = ?", ("default-codex",))
        self.assertEqual(agent["model"], "gpt-test-default")
        self.assertEqual(json.loads(agent["runtime_config"])["effort"], "xhigh")

    def test_managed_codex_spawn_override_wins_over_settings_defaults(self):
        self._heartbeat_environment(id="wsl:test-host:default", bridgeId="bridge-current")
        self.client.put(
            "/api/v1/settings",
            json={"managed_codex_model": "gpt-default", "managed_codex_effort": "medium"},
        )

        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "wsl:test-host:default",
                "agentId": "override-codex",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
                "model": "gpt-custom",
                "runtimeConfig": {"effort": "high", "quietTimeoutMs": 0},
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn = created.json()["spawnRequest"]
        self.assertEqual(spawn["spawnSpec"]["model"], "gpt-custom")
        self.assertEqual(spawn["spawnSpec"]["metadata"]["runtimeConfig"]["effort"], "high")
        self.assertEqual(spawn["spawnSpec"]["metadata"]["runtimeConfig"]["quietTimeoutMs"], 0)

    def test_runtime_settings_update_existing_managed_agents_globally(self):
        self._heartbeat_environment(id="wsl:test-host:default", bridgeId="bridge-current")
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "wsl:test-host:default",
                "agentId": "global-codex",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        codex_spawn = created.json()["spawnRequest"]
        updated = self.client.patch(
            f"/api/v1/spawn-requests/{codex_spawn['id']}",
            json={
                "status": "running",
                "bridgeId": "bridge-current",
                "machineId": "linux:test-host",
                "sessionHandle": "thread-global",
                "runtimeState": {"threadId": "thread-global"},
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        settings = self.client.put(
            "/api/v1/settings",
            json={"managed_codex_model": "gpt-global", "managed_codex_effort": "xhigh"},
        )
        self.assertEqual(settings.status_code, 200, settings.text)
        agent = self._fetchone("SELECT model, runtime_config FROM agents WHERE id = ?", ("global-codex",))
        self.assertEqual(agent["model"], "gpt-global")
        self.assertEqual(json.loads(agent["runtime_config"])["effort"], "xhigh")

    def test_runtime_settings_update_existing_managed_pi_agents_globally(self):
        self._heartbeat_environment(
            id="linux:test-host:default",
            bridgeId="bridge-current",
            runtimes=[{"runtime": "pi", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "global-pi",
                "role": "coder",
                "runtime": "pi",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        pi_spawn = created.json()["spawnRequest"]
        updated = self.client.patch(
            f"/api/v1/spawn-requests/{pi_spawn['id']}",
            json={"status": "running", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        settings = self.client.put(
            "/api/v1/settings",
            json={"managed_pi_model": "gpt-5.5", "managed_pi_effort": "medium"},
        )
        self.assertEqual(settings.status_code, 200, settings.text)
        agent = self._fetchone("SELECT model, runtime_config FROM agents WHERE id = ?", ("global-pi",))
        self.assertEqual(agent["model"], "gpt-5.5")
        self.assertEqual(json.loads(agent["runtime_config"])["effort"], "medium")

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

    def test_managed_agent_status_follows_environment_not_child_heartbeat(self):
        self._heartbeat_environment(
            id="wsl:test-host:default",
            bridgeId="env-bridge",
            machineId="wsl-Ubuntu:test-host",
        )
        self._register(
            "managed-coder",
            role="coder",
            runtime="codex",
            machineId="wsl-Ubuntu:test-host",
            cwd="/workspace/project",
            launchMode="managed",
            sessionMode="managed",
            capabilities=["managed-run", "resume", "interrupt", "steer", "spawn"],
            status="active",
        )
        self._execute(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            (
                json.dumps({
                    "bridgeInstanceId": "env-bridge",
                    "environmentId": "wsl:test-host:default",
                    "threadId": "thread-1",
                }),
                "managed-coder",
            ),
        )
        self._execute(
            "UPDATE environments SET last_seen = '2020-01-01T00:00:00Z' WHERE id = ?",
            ("wsl:test-host:default",),
        )

        # Stale managed Codex MCP children can outlive the real environment
        # bridge. Their heartbeat must not make the teammate look reachable.
        heartbeat = self.client.post(
            "/api/v1/agents/managed-coder/heartbeat",
            json={"bridgeId": "orphan-child-mcp"},
        )
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        agent = listed.json()["agents"]["managed-coder"]
        self.assertEqual(agent["statusRaw"], "offline")

    def test_lost_resident_bridge_returns_to_managed_backing(self):
        self._heartbeat_environment(
            id="wsl:test-host:default",
            bridgeId="env-bridge",
            machineId="wsl-Ubuntu:test-host",
        )
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "wsl:test-host:default",
                "agentId": "dual-mode-coder",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn = created.json()["spawnRequest"]
        updated = self.client.patch(
            f"/api/v1/spawn-requests/{spawn['id']}",
            json={
                "status": "running",
                "bridgeId": "env-bridge",
                "machineId": "wsl-Ubuntu:test-host",
                "sessionHandle": "thread-managed",
                "runtimeState": {"threadId": "thread-managed", "environmentId": "wsl:test-host:default"},
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        self._register(
            "dual-mode-coder",
            role="coder",
            runtime="codex",
            machineId="linux:test-host",
            cwd="/workspace/project",
            bridgeId="resident-bridge",
            sessionMode="resident",
            launchMode="detached",
            sessionHandle="thread-managed",
            capabilities=["resident-run", "resume", "interrupt", "steer"],
            runtimeConfig={"appServerUrl": "ws://127.0.0.1:9"},
        )
        resident = self.client.get("/api/v1/agents/dual-mode-coder").json()
        self.assertEqual(resident["agent"]["sessionMode"], "resident")
        self.assertEqual(resident["agent"]["wakeMode"], "codex-live")

        lost = self.client.post(
            "/api/v1/agents/dual-mode-coder/resident-lost",
            json={"bridgeId": "resident-bridge", "runtime": "codex", "reason": "connect ECONNREFUSED 127.0.0.1:9"},
        )
        self.assertEqual(lost.status_code, 200, lost.text)
        payload = lost.json()
        self.assertEqual(payload["transition"], "resident_to_managed")
        self.assertEqual(payload["agent"]["sessionMode"], "managed")
        self.assertEqual(payload["agent"]["wakeMode"], "managed-worker")
        self.assertEqual(payload["agent"]["sessionHandle"], "thread-managed")
        self.assertEqual(payload["agent"]["runtimeState"]["environmentId"], "wsl:test-host:default")
        self.assertEqual(payload["agent"]["runtimeState"]["ownership"]["reason"], "resident_runtime_lost")
        self.assertNotIn("appServerUrl", payload["agent"]["runtimeConfig"])

        bridge = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id = ?", ("resident-bridge",))
        self.assertEqual(bridge["superseded_by"], "resident-lost")

        heartbeat = self.client.post(
            "/api/v1/agents/dual-mode-coder/heartbeat",
            json={"bridgeId": "resident-bridge"},
        )
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        self.assertTrue(heartbeat.json()["ignored"])

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="dual-mode-coder",
            type="request",
            subject="managed after resident close",
            body="use managed backing",
            mode="require_start",
            requireReply=True,
        )
        self.assertTrue(dispatched["ok"], dispatched)
        run = self._fetchone("SELECT execution_mode FROM dispatch_runs WHERE id = ?", (dispatched["runs"][0]["runId"],))
        self.assertEqual(run["execution_mode"], "managed")

    def test_send_does_not_steer_into_offline_environment_active_run(self):
        self._heartbeat_environment(
            id="wsl:test-host:default",
            label="WSL on test-host",
            bridgeId="bridge-stale",
            machineId="wsl-Ubuntu:test-host",
        )
        self._register(
            "managed-coder",
            role="coder",
            runtime="codex",
            machineId="wsl-Ubuntu:test-host",
            cwd="/workspace/project",
            launchMode="managed",
            sessionMode="managed",
            capabilities=["managed-run", "resume", "interrupt", "steer", "spawn"],
            status="active",
        )
        self._execute(
            "UPDATE environments SET last_seen = '2020-01-01T00:00:00Z' WHERE id = ?",
            ("wsl:test-host:default",),
        )
        self._execute(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            (
                json.dumps({
                    "bridgeInstanceId": "bridge-stale",
                    "environmentId": "wsl:test-host:default",
                    "threadId": "thread-1",
                }),
                "managed-coder",
            ),
        )
        self._execute(
            """
            INSERT INTO bridge_instances (
                id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen
            ) VALUES (?,?,?,?,?,?,?)
            """,
            ("bridge-stale", "managed-coder", "wsl-Ubuntu:test-host", "codex", "managed", "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode,
                runtime, message_type, subject, body, priority, status, claim_machine_id,
                claim_bridge_id, require_reply, requested_at, claimed_at, started_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run-stale",
                None,
                "manager",
                "managed-coder",
                "start_if_possible",
                "managed",
                "codex",
                "request",
                "old work",
                "old body",
                "normal",
                "running",
                "wsl-Ubuntu:test-host",
                "bridge-stale",
                1,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:01Z",
                "2026-01-01T00:00:01Z",
            ),
        )

        sent = self._send_message(
            from_agent="manager",
            to="managed-coder",
            type="request",
            subject="new work",
            body="please do this",
            trigger=True,
        )

        self.assertFalse(sent["ok"])
        self.assertIn("cannot start live work", sent["error"])
        stale_run = self._fetchone("SELECT status, summary, error_text FROM dispatch_runs WHERE id = ?", ("run-stale",))
        self.assertEqual(stale_run["status"], "failed")
        self.assertIn("environment", stale_run["summary"])
        self.assertIn("offline", stale_run["error_text"])
        controls = self._fetchall("SELECT * FROM dispatch_controls WHERE run_id = ?", ("run-stale",))
        self.assertEqual(controls, [])
        messages = self._fetchall("SELECT * FROM messages WHERE to_agent = ?", ("managed-coder",))
        self.assertEqual(messages, [])

    def test_dispatch_rejects_managed_agent_when_environment_is_offline(self):
        self._heartbeat_environment(
            id="wsl:test-host:default",
            label="WSL on test-host",
            bridgeId="bridge-stale",
            machineId="wsl-Ubuntu:test-host",
        )
        self._register(
            "managed-coder",
            role="coder",
            runtime="codex",
            machineId="wsl-Ubuntu:test-host",
            cwd="/workspace/project",
            launchMode="managed",
            sessionMode="managed",
            capabilities=["managed-run", "resume", "interrupt", "steer", "spawn"],
            status="active",
        )
        self._execute(
            "UPDATE environments SET last_seen = '2020-01-01T00:00:00Z' WHERE id = ?",
            ("wsl:test-host:default",),
        )
        self._execute(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            (
                json.dumps({"environmentId": "wsl:test-host:default", "bridgeInstanceId": "bridge-stale"}),
                "managed-coder",
            ),
        )

        dispatched = self._dispatch(
            from_agent="manager",
            to="managed-coder",
            type="request",
            subject="strict work",
            body="do not queue into offline env",
            mode="require_start",
            requireReply=True,
        )

        self.assertFalse(dispatched["ok"])
        self.assertEqual(dispatched["runs"], [])
        self.assertIn("managed environment", dispatched["notStarted"][0]["reason"])
        messages = self._fetchall("SELECT * FROM messages WHERE to_agent = ?", ("managed-coder",))
        self.assertEqual(messages, [])

    def test_runs_listing_repairs_offline_environment_active_run(self):
        self._heartbeat_environment(id="wsl:test-host:default", bridgeId="bridge-stale")
        self._register(
            "managed-coder",
            role="coder",
            runtime="codex",
            machineId="wsl-Ubuntu:test-host",
            cwd="/workspace/project",
            launchMode="managed",
            sessionMode="managed",
            capabilities=["managed-run", "resume", "interrupt", "steer", "spawn"],
            status="active",
        )
        self._execute(
            "UPDATE environments SET last_seen = '2020-01-01T00:00:00Z' WHERE id = ?",
            ("wsl:test-host:default",),
        )
        self._execute(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            (
                json.dumps({
                    "bridgeInstanceId": "bridge-stale",
                    "environmentId": "wsl:test-host:default",
                    "threadId": "thread-1",
                }),
                "managed-coder",
            ),
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, from_agent, target_agent, dispatch_mode, execution_mode, runtime,
                message_type, subject, body, priority, status, claim_machine_id,
                claim_bridge_id, require_reply, requested_at, claimed_at, started_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run-stale",
                "manager",
                "managed-coder",
                "start_if_possible",
                "managed",
                "codex",
                "request",
                "old work",
                "old body",
                "normal",
                "running",
                "wsl-Ubuntu:test-host",
                "bridge-stale",
                1,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:01Z",
                "2026-01-01T00:00:01Z",
            ),
        )
        self._execute(
            """
            INSERT INTO dispatch_controls (
                id, run_id, from_agent, action, body, source_message_id, status, requested_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            ("ctl-stale", "run-stale", "manager", "steer", "wake up", "msg-1", "pending", "2026-01-01T00:00:02Z"),
        )

        listed = self.client.get("/api/v1/dispatch/runs?limit=5")
        self.assertEqual(listed.status_code, 200, listed.text)
        run = next(item for item in listed.json()["runs"] if item["id"] == "run-stale")
        self.assertEqual(run["status"], "failed")
        self.assertIn("environment", run["summary"])
        control = self._fetchone("SELECT status, response_text FROM dispatch_controls WHERE id = ?", ("ctl-stale",))
        self.assertEqual(control["status"], "failed")
        self.assertIn("offline", control["response_text"])

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

    def test_pi_runtime_alias_is_spawnable_from_environment(self):
        environment = self._heartbeat_environment(
            runtimes=[{"runtime": "pi", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        self.assertEqual(environment["runtimes"][0]["runtime"], "pi")

        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "pi-worker",
                "role": "coder",
                "runtime": "oh-my-pi",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_request = created.json()["spawnRequest"]
        self.assertEqual(spawn_request["runtime"], "pi")
        self.assertEqual(spawn_request["spawnSpec"]["runtime"], "pi")

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

    def test_sessions_include_console_ownership_defaults(self):
        self._heartbeat_environment(id="linux:test-host:default")
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "console-defaults-agent",
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

        sessions = self.client.get("/api/v1/sessions?agentId=console-defaults-agent")
        self.assertEqual(sessions.status_code, 200, sessions.text)
        session = sessions.json()["sessions"][0]
        self.assertEqual(session["ownerMode"], "managed")
        self.assertEqual(session["ownerBridgeId"], "bridge-current")
        self.assertEqual(session["terminalId"], "")
        self.assertEqual(session["terminalStatus"], "")
        self.assertEqual(session["terminalCommand"], "")
        self.assertEqual(session["terminalWorkspace"], "")
        self.assertEqual(session["terminal"], {
            "id": "",
            "status": "",
            "command": "",
            "workspace": "",
            "ownerMode": "managed",
            "ownerBridgeId": "bridge-current",
        })

    def test_pi_runtime_state_session_file_backfills_handle(self):
        self._register("pi-file-agent", runtime="pi", sessionMode="managed")

        updated = self.client.patch(
            "/api/v1/agents/pi-file-agent/runtime-state",
            json={"runtimeState": {"sessionFile": "C:/Users/test/.omp/agent/sessions/project/abc123_deadbeef.jsonl"}},
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        agent = self.client.get("/api/v1/agents/pi-file-agent").json()["agent"]
        self.assertEqual(agent["sessionHandle"], "C:/Users/test/.omp/agent/sessions/project/abc123_deadbeef.jsonl")
        self.assertEqual(agent["runtimeState"]["sessionFile"], "C:/Users/test/.omp/agent/sessions/project/abc123_deadbeef.jsonl")


    def test_get_db_applies_sqlite_contention_pragmas_per_connection(self):
        async def _read_pragmas():
            db = await get_db()
            try:
                busy_timeout = (await (await db.execute("PRAGMA busy_timeout")).fetchone())[0]
                synchronous = (await (await db.execute("PRAGMA synchronous")).fetchone())[0]
                return busy_timeout, synchronous
            finally:
                await db.close()

        busy_timeout, synchronous = asyncio.run(_read_pragmas())

        self.assertGreaterEqual(busy_timeout, 5000)
        self.assertEqual(synchronous, 1)

    def test_api_database_lock_errors_return_json_not_html_500(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]

        with patch.object(api_v2, "get_db", side_effect=sqlite3.OperationalError("database is locked")):
            response = self.client.post(
                f"/api/v1/terminals/{terminal_id}/output",
                json={"bridgeId": "bridge-current", "output": "x", "status": "attached"},
            )

        self.assertEqual(response.headers["content-type"].split(";")[0], "application/json")
        self.assertIn(response.status_code, {500, 503})
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("database", payload["error"].lower())

    def test_terminal_output_posts_are_coalesced_into_one_audit_write(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]

        for chunk in ["a", "b", "c"]:
            response = self.client.post(
                f"/api/v1/terminals/{terminal_id}/output",
                json={"bridgeId": "bridge-current", "output": chunk, "status": "attached"},
            )
            self.assertEqual(response.status_code, 200, response.text)

        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())

        fetched = self.client.get(f"/api/v1/terminals/{terminal_id}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["terminal"]["output"], "abc")
        output_events = self._fetchall(
            "SELECT body FROM terminal_events WHERE terminal_id = ? AND event_type = 'terminal_output'",
            (terminal_id,),
        )
        self.assertEqual([row["body"] for row in output_events], ["abc"])

    def test_buffered_active_status_does_not_overwrite_stopped_terminal(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]

        active = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={"bridgeId": "bridge-current", "output": "still active", "status": "attached"},
        )
        self.assertEqual(active.status_code, 200, active.text)
        self._execute(
            "UPDATE terminal_sessions SET status = 'stopped', stopped_at = ?, updated_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", terminal_id),
        )

        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())

        terminal = self._fetchone("SELECT status, output FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(terminal["status"], "stopped")
        self.assertIn("still active", terminal["output"])
    def test_environment_heartbeat_persists_terminal_capabilities(self):
        environment = self._heartbeat_environment(
            terminal=True,
            pty=True,
            terminalRuntimes=["codex", "pi"],
        )
        self.assertTrue(environment["terminal"])
        self.assertTrue(environment["pty"])
        self.assertEqual(environment["terminalRuntimes"], ["codex", "pi"])

        listed = self.client.get("/api/v1/environments")
        self.assertEqual(listed.status_code, 200, listed.text)
        listed_env = listed.json()["environments"][0]
        self.assertTrue(listed_env["terminal"])
        self.assertTrue(listed_env["pty"])
        self.assertEqual(listed_env["terminalRuntimes"], ["codex", "pi"])

    def _create_running_session(
        self,
        *,
        agent_id: str = "console-agent",
        terminal: bool = False,
        workspace: str = "/workspace/repo",
        runtime: str = "codex",
        terminal_runtimes: list[str] | None = None,
        session_handle: str = "thread-1",
    ):
        self._heartbeat_environment(
            terminal=terminal,
            pty=terminal,
            terminalRuntimes=(terminal_runtimes if terminal_runtimes is not None else ([runtime] if terminal else [])),
            runtimes=[
                {
                    "runtime": runtime,
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                }
            ],
        )
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": agent_id,
                "role": "coder",
                "runtime": runtime,
                "workspace": workspace,
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
            json={"status": "running", "bridgeId": "bridge-current", "processId": "1234", "sessionHandle": session_handle},
        )
        self.assertEqual(running.status_code, 200, running.text)
        return running.json()["spawnRequest"]["sessionId"]

    def test_console_start_rejects_environment_without_terminal_support(self):
        session_id = self._create_running_session(terminal=False)

        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(started.status_code, 409, started.text)
        self.assertIn("does not advertise terminal support", started.text)
        self.assertIsNone(self._fetchone("SELECT id FROM terminal_sessions LIMIT 1"))

    def test_console_start_rejects_workspace_outside_environment_roots(self):
        session_id = self._create_running_session(terminal=True)

        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard", "workspace": "/elsewhere/repo"},
        )
        self.assertEqual(started.status_code, 400, started.text)
        self.assertIn("outside the roots", started.text)
        self.assertIsNone(self._fetchone("SELECT id FROM terminal_sessions LIMIT 1"))

    def test_console_start_creates_terminal_record_and_audit_event(self):
        session_id = self._create_running_session(terminal=True)

        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard", "command": "codex-aify --aify-agent console-agent"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        payload = started.json()
        terminal = payload["terminal"]
        self.assertEqual(terminal["sessionId"], session_id)
        self.assertEqual(terminal["agentId"], "console-agent")
        self.assertEqual(terminal["runtime"], "codex")
        self.assertEqual(terminal["status"], "starting")
        self.assertEqual(terminal["workspace"], "/workspace/repo")
        self.assertEqual(terminal["command"], "codex-aify --aify-agent console-agent")

        session = self.client.get(f"/api/v1/sessions?agentId=console-agent").json()["sessions"][0]
        self.assertEqual(session["ownerMode"], "console")
        self.assertEqual(session["terminalId"], terminal["id"])
        self.assertEqual(session["terminalStatus"], "starting")

        fetched = self.client.get(f"/api/v1/terminals/{terminal['id']}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["terminal"]["id"], terminal["id"])
        self.assertEqual(fetched.json()["events"][0]["eventType"], "console_start_requested")

        event = self._fetchone(
            "SELECT event_type, body FROM terminal_events WHERE terminal_id = ?",
            (terminal["id"],),
        )
        self.assertEqual(event["event_type"], "console_start_requested")
        self.assertIn("dashboard", event["body"])

    def test_terminal_controls_claim_update_and_output_buffer(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard", "command": "bash"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]

        claim = self.client.post(
            "/api/v1/terminals/controls/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        control = claim.json()["controls"][0]
        self.assertEqual(control["terminalId"], terminal_id)
        self.assertEqual(control["action"], "start")

        updated = self.client.patch(
            f"/api/v1/terminals/controls/{control['id']}",
            json={"status": "completed", "terminalStatus": "attached", "output": "ready\n"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        fetched = self.client.get(f"/api/v1/terminals/{terminal_id}")
        self.assertEqual(fetched.json()["terminal"]["status"], "attached")
        self.assertEqual(fetched.json()["terminal"]["output"], "ready\n")

        sent = self.client.post(
            f"/api/v1/terminals/{terminal_id}/input",
            json={"requestedBy": "dashboard", "body": "echo hi\n"},
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        resized = self.client.post(
            f"/api/v1/terminals/{terminal_id}/resize",
            json={"requestedBy": "dashboard", "cols": 120, "rows": 40},
        )
        self.assertEqual(resized.status_code, 200, resized.text)
        stopped = self.client.post(
            f"/api/v1/terminals/{terminal_id}/stop",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(stopped.status_code, 200, stopped.text)

        controls = self._fetchall(
            "SELECT action, status FROM terminal_controls WHERE terminal_id = ? ORDER BY id",
            (terminal_id,),
        )
        self.assertEqual([row["action"] for row in controls], ["start", "input", "resize", "stop"])
        self.assertEqual(controls[-1]["status"], "pending")

    def test_terminal_stop_reconciles_stale_bridge_owner(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard", "command": "bash"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]
        self._heartbeat_environment(bridgeId="replacement-bridge", terminal=True, pty=True)

        stopped = self.client.post(
            f"/api/v1/terminals/{terminal_id}/stop",
            json={"requestedBy": "dashboard"},
        )

        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(stopped.json()["terminal"]["status"], "stopped")
        session = self._fetchone("SELECT owner_mode, terminal_status FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(session["owner_mode"], "managed")
        self.assertEqual(session["terminal_status"], "stopped")
        control = self._fetchone(
            "SELECT action, status, handled_at FROM terminal_controls WHERE terminal_id = ? AND action = 'stop' ORDER BY requested_at DESC LIMIT 1",
            (terminal_id,),
        )
        self.assertEqual(control["action"], "stop")
        self.assertEqual(control["status"], "completed")
        self.assertTrue(control["handled_at"])

    def test_terminal_output_buffer_is_bounded(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]

        output = "x" * 70000
        appended = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={"bridgeId": "bridge-current", "output": output, "status": "attached"},
        )
        self.assertEqual(appended.status_code, 200, appended.text)
        terminal = self.client.get(f"/api/v1/terminals/{terminal_id}").json()["terminal"]
        self.assertEqual(len(terminal["output"]), 65536)
        self.assertEqual(terminal["output"], "x" * 65536)

        stopped = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={"bridgeId": "bridge-current", "output": "\nbye\n", "status": "stopped"},
        )
        self.assertEqual(stopped.status_code, 200, stopped.text)
        session = self.client.get("/api/v1/sessions?agentId=console-agent").json()["sessions"][0]
        self.assertEqual(session["ownerMode"], "managed")
        self.assertEqual(session["terminalStatus"], "stopped")

    def test_console_start_builds_codex_resume_command(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        command = started.json()["terminal"]["command"]
        self.assertIn("codex-aify", command)
        self.assertIn("--aify-agent console-agent", command)
        self.assertIn("resume --include-non-interactive thread-1", command)
        self.assertNotIn("--resume", command)

    def test_console_start_builds_claude_channels_command_without_dev_prompt(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        command = started.json()["terminal"]["command"]
        self.assertIn("claude --channels server:aify-comms-channel", command)
        self.assertIn("--dangerously-skip-permissions", command)
        self.assertIn("--resume claude-session-1", command)
        self.assertNotIn("claude-aify", command)
        self.assertNotIn("--dangerously-load-development-channels", command)

    def test_console_child_register_does_not_convert_managed_session_to_cli_takeover(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]

        registered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "console-agent",
                "role": "coder",
                "runtime": "claude-code",
                "sessionMode": "resident",
                "sessionHandle": "claude-session-1",
                "machineId": "linux:test-host",
                "bridgeId": "console-channel-bridge",
                "terminalId": terminal_id,
                "autoRegister": True,
                "restoreDeleted": True,
            },
        )

        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertEqual(registered.json()["sessionMode"], "managed")
        self.assertEqual(registered.json()["ownershipTransition"], "console_terminal_attached")
        agent = self._fetchone("SELECT session_mode, capabilities, runtime_state FROM agents WHERE id = ?", ("console-agent",))
        self.assertEqual(agent["session_mode"], "managed")
        self.assertIn("managed-run", json.loads(agent["capabilities"]))
        self.assertEqual(json.loads(agent["runtime_state"])["consoleTerminal"]["terminalId"], terminal_id)
        session = self._fetchone("SELECT status, ended_at, owner_mode, terminal_id FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(session["status"], "running")
        self.assertIsNone(session["ended_at"])
        self.assertEqual(session["owner_mode"], "console")
        self.assertEqual(session["terminal_id"], terminal_id)

    def test_pi_console_requires_handle_unless_fresh_context_requested(self):
        self._heartbeat_environment(
            terminal=True,
            pty=True,
            terminalRuntimes=["pi"],
            runtimes=[
                {
                    "runtime": "pi",
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                }
            ],
        )
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "pi-console-agent",
                "role": "coder",
                "runtime": "pi",
                "workspace": "/workspace/repo",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "processId": "1234", "sessionHandle": ""},
        )
        self.assertEqual(running.status_code, 200, running.text)
        session_id = running.json()["spawnRequest"]["sessionId"]

        rejected = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("needs a session handle", rejected.text)

        fresh = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard", "freshContext": True},
        )
        self.assertEqual(fresh.status_code, 200, fresh.text)
        self.assertIn("pi-aify", fresh.json()["terminal"]["command"])
        self.assertNotIn("--resume", fresh.json()["terminal"]["command"])

    def test_managed_dispatch_to_active_console_terminal_forwards_to_pty(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="work",
            body="do it",
            mode="start_if_possible",
            createMessage=True,
        )
        self.assertEqual(dispatched["runs"], [])
        self.assertEqual(dispatched["notStarted"], [])
        self.assertEqual(dispatched["consoleDeliveries"][0]["targetAgentId"], "console-agent")
        self.assertEqual(dispatched["consoleDeliveries"][0]["terminalId"], terminal_id)
        contract = self._fetchone("SELECT id, status, dispatch_mode, require_reply FROM dispatch_runs WHERE target_agent = ?", ("console-agent",))
        self.assertEqual(contract["status"], "delivered")
        self.assertEqual(contract["dispatch_mode"], "terminal")
        self.assertEqual(contract["require_reply"], 1)
        self.assertEqual(dispatched["consoleDeliveries"][0]["contractRunId"], contract["id"])
        control = self._fetchone("SELECT * FROM terminal_controls WHERE terminal_id = ? AND action = 'input'", (terminal_id,))
        self.assertIsNotNone(control)
        self.assertIn("AIFY dashboard message", control["body"])
        self.assertIn("dashboard", control["body"])
        self.assertIn("do it", control["body"])
        self.assertTrue(control["body"].endswith("\r"))

    def test_managed_claude_dispatch_starts_headless_pty_when_console_is_closed(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="work",
            body="do it without console open",
            mode="start_if_possible",
            createMessage=True,
        )
        self.assertEqual(dispatched["runs"], [])
        self.assertEqual(dispatched["notStarted"], [])
        self.assertEqual(dispatched["consoleDeliveries"][0]["targetAgentId"], "console-agent")
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        session = self._fetchone("SELECT owner_mode, terminal_id, terminal_status FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(session["owner_mode"], "managed")
        self.assertEqual(session["terminal_id"], terminal_id)
        self.assertEqual(session["terminal_status"], "starting")
        controls = self._fetchall("SELECT action, body FROM terminal_controls WHERE terminal_id = ? ORDER BY requested_at ASC", (terminal_id,))
        self.assertEqual([row["action"] for row in controls], ["start", "input"])
        self.assertIn("claude --channels server:aify-comms-channel", controls[0]["body"])
        self.assertIn("do it without console open", controls[1]["body"])
        contract = self._fetchone("SELECT id, status, dispatch_mode, require_reply FROM dispatch_runs WHERE target_agent = ?", ("console-agent",))
        self.assertEqual(contract["status"], "delivered")
        self.assertEqual(contract["dispatch_mode"], "terminal")
        self.assertEqual(contract["require_reply"], 1)
        self.assertEqual(dispatched["consoleDeliveries"][0]["contractRunId"], contract["id"])

    def test_managed_dispatch_starts_headless_pty_for_terminal_runtimes(self):
        cases = [
            ("codex", "codex-aify --aify-agent {agent_id}", "resume --include-non-interactive codex-thread-1", "codex-thread-1"),
            ("hermes", "hermes-aify --aify-agent {agent_id}", "--resume hermes-session-1", "hermes-session-1"),
            ("pi", "pi-aify --aify-agent {agent_id}", "--resume pi-session-1", "pi-session-1"),
            ("opencode", "opencode", "", "opencode-session-1"),
        ]
        for runtime, command_prefix, command_contains, handle in cases:
            with self.subTest(runtime=runtime):
                agent_id = f"{runtime}-pty-agent"
                session_id = self._create_running_session(
                    agent_id=agent_id,
                    terminal=True,
                    runtime=runtime,
                    terminal_runtimes=[runtime],
                    session_handle=handle,
                )

                dispatched = self._dispatch(
                    from_agent="dashboard",
                    to=agent_id,
                    type="request",
                    subject="work",
                    body=f"run through pty for {runtime}",
                    mode="start_if_possible",
                    createMessage=True,
                )
                self.assertEqual(dispatched["runs"], [])
                self.assertEqual(dispatched["notStarted"], [])
                self.assertEqual(dispatched["consoleDeliveries"][0]["targetAgentId"], agent_id)
                terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
                session = self._fetchone("SELECT owner_mode, terminal_id, terminal_status FROM agent_sessions WHERE id = ?", (session_id,))
                self.assertEqual(session["owner_mode"], "managed")
                self.assertEqual(session["terminal_id"], terminal_id)
                self.assertEqual(session["terminal_status"], "starting")
                controls = self._fetchall("SELECT action, body FROM terminal_controls WHERE terminal_id = ? ORDER BY requested_at ASC, id ASC", (terminal_id,))
                self.assertEqual([row["action"] for row in controls], ["start", "input"])
                self.assertTrue(controls[0]["body"].startswith(command_prefix.format(agent_id=agent_id)), controls[0]["body"])
                if command_contains:
                    self.assertIn(command_contains, controls[0]["body"])
                self.assertIn(f"run through pty for {runtime}", controls[1]["body"])
                contract = self._fetchone("SELECT status, dispatch_mode, require_reply FROM dispatch_runs WHERE target_agent = ?", (agent_id,))
                self.assertEqual(contract["status"], "delivered")
                self.assertEqual(contract["dispatch_mode"], "terminal")
                self.assertEqual(contract["require_reply"], 1)

    def test_message_send_delivers_to_active_console_pty_without_queuing_run(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]

        sent = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "to": "console-agent",
                "type": "request",
                "subject": "console chat",
                "body": "answer through the pty",
                "trigger": True,
            },
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        payload = sent.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["dispatchRuns"], [])
        self.assertEqual(payload["notStarted"], [])
        self.assertEqual(payload["consoleDeliveries"][0]["terminalId"], terminal_id)

        controls = self._fetchall("SELECT action, body FROM terminal_controls WHERE terminal_id = ? ORDER BY requested_at ASC, id ASC", (terminal_id,))
        self.assertEqual([row["action"] for row in controls], ["start", "input"])
        self.assertIn("answer through the pty", controls[1]["body"])
        message = self._fetchone("SELECT dispatch_requested FROM messages WHERE id = ?", (payload["messageId"],))
        self.assertEqual(message["dispatch_requested"], 1)
        contract = self._fetchone("SELECT id, status, dispatch_mode, require_reply FROM dispatch_runs WHERE target_agent = ?", ("console-agent",))
        self.assertEqual(contract["status"], "delivered")
        self.assertEqual(contract["dispatch_mode"], "terminal")
        self.assertEqual(contract["require_reply"], 1)
        self.assertEqual(payload["consoleDeliveries"][0]["contractRunId"], contract["id"])
        receipt = self._fetchone("SELECT message_id FROM read_receipts WHERE message_id = ? AND agent_id = ?", (payload["messageId"], "console-agent"))
        self.assertEqual(receipt["message_id"], payload["messageId"])

    def test_message_send_starts_managed_pty_for_hermes_when_console_is_closed(self):
        session_id = self._create_running_session(
            agent_id="hermes-pty-agent",
            terminal=True,
            runtime="hermes",
            terminal_runtimes=["hermes"],
            session_handle="hermes-session-1",
        )

        sent = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "to": "hermes-pty-agent",
                "type": "request",
                "subject": "hermes chat",
                "body": "run hermes through managed pty",
                "trigger": True,
            },
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        payload = sent.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["dispatchRuns"], [])
        self.assertEqual(payload["notStarted"], [])
        terminal_id = payload["consoleDeliveries"][0]["terminalId"]

        session = self._fetchone("SELECT owner_mode, terminal_id, terminal_status FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(session["owner_mode"], "managed")
        self.assertEqual(session["terminal_id"], terminal_id)
        self.assertEqual(session["terminal_status"], "starting")
        controls = self._fetchall("SELECT action, body FROM terminal_controls WHERE terminal_id = ? ORDER BY requested_at ASC, id ASC", (terminal_id,))
        self.assertEqual([row["action"] for row in controls], ["start", "input"])
        self.assertTrue(controls[0]["body"].startswith("hermes-aify --aify-agent hermes-pty-agent"), controls[0]["body"])
        self.assertIn("--resume hermes-session-1", controls[0]["body"])
        self.assertIn("run hermes through managed pty", controls[1]["body"])
        contract = self._fetchone("SELECT id, status, dispatch_mode, require_reply FROM dispatch_runs WHERE target_agent = ?", ("hermes-pty-agent",))
        self.assertEqual(contract["status"], "delivered")
        self.assertEqual(contract["dispatch_mode"], "terminal")
        self.assertEqual(contract["require_reply"], 1)
        self.assertEqual(payload["consoleDeliveries"][0]["contractRunId"], contract["id"])

    def test_terminal_control_claim_orders_start_before_input_with_same_timestamp(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="codex",
            terminal_runtimes=["codex"],
            session_handle="codex-thread-1",
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="work",
            body="ordering check",
            mode="start_if_possible",
            createMessage=True,
        )
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        self._execute("UPDATE terminal_controls SET requested_at = ? WHERE terminal_id = ?", ("2026-01-01T00:00:00Z", terminal_id))

        claim = self.client.post(
            "/api/v1/terminals/controls/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual([control["action"] for control in claim.json()["controls"][:2]], ["start", "input"])

    def test_managed_dispatch_reclaims_session_from_stale_console_owner(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]
        self._heartbeat_environment(bridgeId="bridge-replacement", terminal=True, pty=True)
        self._execute(
            """
            UPDATE terminal_sessions
            SET status = ?, updated_at = ?, bridge_id = ?
            WHERE id = ?
            """,
            ("attached", "2026-01-01T00:00:00Z", "bridge-old", terminal_id),
        )
        self._execute(
            """
            UPDATE agent_sessions
            SET owner_mode = ?, owner_bridge_id = ?, terminal_status = ?
            WHERE id = ?
            """,
            ("console", "bridge-old", "attached", session_id),
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="work",
            body="do it",
            mode="start_if_possible",
            createMessage=True,
        )
        self.assertEqual(dispatched["runs"], [])
        self.assertEqual(dispatched["notStarted"], [])
        new_terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        self.assertNotEqual(new_terminal_id, terminal_id)
        session = self._fetchone("SELECT owner_mode, terminal_id, terminal_status FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(session["owner_mode"], "managed")
        self.assertEqual(session["terminal_id"], new_terminal_id)
        self.assertEqual(session["terminal_status"], "starting")
        terminal = self._fetchone("SELECT status, error FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(terminal["status"], "failed")
        self.assertIn("stale Console owner", terminal["error"])
        controls = self._fetchall("SELECT action FROM terminal_controls WHERE terminal_id = ? ORDER BY requested_at ASC, id ASC", (new_terminal_id,))
        self.assertEqual([row["action"] for row in controls], ["start", "input"])

    def test_delete_session_rejects_running_session(self):
        session_id = self._create_running_session(terminal=True)

        deleted = self.client.delete(f"/api/v1/sessions/{session_id}")

        self.assertEqual(deleted.status_code, 409, deleted.text)
        session = self._fetchone("SELECT id, status FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertIsNotNone(session)
        self.assertEqual(session["status"], "running")

    def test_delete_session_removes_inactive_session_but_keeps_agent(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]
        now = "2026-05-14T00:00:00Z"
        self._execute(
            "UPDATE terminal_sessions SET status = ?, stopped_at = ?, updated_at = ? WHERE id = ?",
            ("stopped", now, now, terminal_id),
        )
        self._execute(
            """
            UPDATE agent_sessions
            SET status = ?, terminal_status = ?, owner_mode = ?, ended_at = ?, last_seen = ?
            WHERE id = ?
            """,
            ("stopped", "stopped", "managed", now, now, session_id),
        )

        deleted = self.client.delete(f"/api/v1/sessions/{session_id}")

        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["ok"])
        self.assertIsNone(self._fetchone("SELECT id FROM agent_sessions WHERE id = ?", (session_id,)))
        self.assertIsNone(self._fetchone("SELECT id FROM terminal_sessions WHERE id = ?", (terminal_id,)))
        agent = self._fetchone("SELECT id FROM agents WHERE id = ?", ("console-agent",))
        self.assertIsNotNone(agent)

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

        agent = self._fetchone("SELECT cwd, launch_mode, session_mode, session_handle, status FROM agents WHERE id = ?", ("move-me",))
        session = self._fetchone("SELECT environment_id, workspace, status, session_handle FROM agent_sessions WHERE agent_id = ?", ("move-me",))
        spec = self._fetchone("SELECT environment_id, workspace FROM spawn_specs WHERE agent_id = ?", ("move-me",))
        self.assertEqual(agent["cwd"], "/newroot/project")
        self.assertEqual(agent["launch_mode"], "none")
        self.assertEqual(agent["session_mode"], "managed")
        self.assertEqual(agent["session_handle"], "thread-1")
        self.assertEqual(agent["status"], "offline")
        self.assertEqual(session["environment_id"], "linux:new-host:default")
        self.assertEqual(session["workspace"], "/newroot/project")
        self.assertEqual(session["status"], "lost")
        self.assertEqual(session["session_handle"], "thread-1")
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
        session = self._fetchone("SELECT id, environment_id, runtime, workspace, status, session_handle, spawn_spec_id FROM agent_sessions WHERE agent_id = ?", ("resident-manager",))
        spec = self._fetchone("SELECT environment_id, runtime, workspace FROM spawn_specs WHERE agent_id = ?", ("resident-manager",))
        self.assertEqual(agent["session_mode"], "managed")
        self.assertEqual(agent["launch_mode"], "none")
        self.assertEqual(agent["session_handle"], "thread-old")
        self.assertEqual(agent["status"], "offline")
        self.assertIsNotNone(session)
        self.assertEqual(session["environment_id"], "linux:new-host:default")
        self.assertEqual(session["runtime"], "codex")
        self.assertEqual(session["workspace"], "/newroot/project")
        self.assertEqual(session["session_handle"], "thread-old")
        self.assertEqual(session["status"], "stopped")
        self.assertTrue(session["spawn_spec_id"])
        self.assertEqual(spec["environment_id"], "linux:new-host:default")
        self.assertEqual(spec["workspace"], "/newroot/project")

        restarted = self.client.post(
            f"/api/v1/sessions/{session['id']}/control",
            json={"action": "restart", "from_agent": "dashboard", "subject": "restart resident-manager"},
        )
        self.assertEqual(restarted.status_code, 200, restarted.text)
        spawn_request = self._fetchone(
            "SELECT resume_policy, session_handle FROM spawn_requests WHERE id = ?",
            (restarted.json()["spawnRequest"]["id"],),
        )
        self.assertEqual(spawn_request["resume_policy"], "native_first")
        self.assertEqual(spawn_request["session_handle"], "thread-old")

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

    def test_session_recreate_is_explicit_fresh_context_reset(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "recreate-coder",
                "role": "coder",
                "runtime": "codex",
                "workspace": "/workspace/repo",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={"status": "running", "bridgeId": "bridge-current", "processId": "1234", "sessionHandle": "thread-old"},
        )
        self.assertEqual(running.status_code, 200, running.text)
        session_id = running.json()["spawnRequest"]["sessionId"]

        recreated = self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "recreate", "from_agent": "dashboard", "subject": "recreate worker", "body": "fresh start"},
        )
        self.assertEqual(recreated.status_code, 200, recreated.text)
        self.assertEqual(recreated.json()["session"]["status"], "ended")
        spawn_request = self._fetchone(
            "SELECT resume_policy, session_handle, initial_message FROM spawn_requests WHERE id = ?",
            (recreated.json()["spawnRequest"]["id"],),
        )
        self.assertEqual(spawn_request["resume_policy"], "fresh_context")
        self.assertEqual(spawn_request["session_handle"], "")
        self.assertEqual(spawn_request["initial_message"], "fresh start")
        agent = self._fetchone("SELECT session_handle, runtime_state FROM agents WHERE id = ?", ("recreate-coder",))
        self.assertEqual(agent["session_handle"], "")
        self.assertEqual(agent["runtime_state"], "{}")

    def test_operator_can_set_agent_session_handle_for_each_runtime(self):
        self._heartbeat_environment()
        cases = {
            "claude-code": "sessionId",
            "codex": "threadId",
            "opencode": "sessionId",
            "pi": "sessionId",
        }
        for runtime, handle_key in cases.items():
            agent_id = f"lead-{runtime.replace('-', '')}"
            session_id = f"sess-{agent_id}"
            with self.subTest(runtime=runtime):
                self._register(
                    agent_id,
                    role="tech-lead",
                    runtime=runtime,
                    sessionMode="managed",
                    launchMode="managed",
                    cwd="C:/Docker/aify-project-graph",
                )
                self._execute(
                    "UPDATE agents SET runtime_state = ? WHERE id = ?",
                    (json.dumps({"sessionId": "old-session", "threadId": "old-thread", "keep": True}), agent_id),
                )
                self._execute(
                    """
                    INSERT INTO agent_sessions (
                        id, agent_id, environment_id, runtime, workspace, mode, session_handle,
                        spawn_spec_id, spawn_request_id, capabilities, telemetry, status, started_at, last_seen
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        session_id,
                        agent_id,
                        "linux:test-host:default",
                        runtime,
                        "C:/Docker/aify-project-graph",
                        "managed-warm",
                        "old-session",
                        None,
                        None,
                        json.dumps({"persistent": True, "nativeResume": False}),
                        "{}",
                        "running",
                        "2026-04-28T10:00:00Z",
                        "2026-04-28T10:00:00Z",
                    ),
                )

                updated = self.client.patch(
                    f"/api/v1/agents/{agent_id}/session-handle",
                    json={"sessionHandle": "new-session", "requestedBy": "dashboard"},
                )
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(updated.json()["agent"]["sessionHandle"], "new-session")
                self.assertEqual(updated.json()["agent"]["runtimeState"][handle_key], "new-session")

                agent = self._fetchone("SELECT session_handle, runtime_state FROM agents WHERE id = ?", (agent_id,))
                runtime_state = json.loads(agent["runtime_state"])
                self.assertEqual(agent["session_handle"], "new-session")
                self.assertEqual(runtime_state[handle_key], "new-session")
                self.assertTrue(runtime_state["keep"])
                self.assertNotIn("threadId" if handle_key == "sessionId" else "sessionId", runtime_state)

                session = self._fetchone("SELECT session_handle, capabilities, telemetry FROM agent_sessions WHERE id = ?", (session_id,))
                session_capabilities = json.loads(session["capabilities"])
                session_telemetry = json.loads(session["telemetry"])
                self.assertEqual(session["session_handle"], "new-session")
                self.assertTrue(session_capabilities["nativeResume"])
                self.assertTrue(session_capabilities["bridgeResume"])
                self.assertEqual(session_telemetry["registeredHandle"][handle_key], "new-session")

                cleared = self.client.patch(
                    f"/api/v1/agents/{agent_id}/session-handle",
                    json={"sessionHandle": "", "requestedBy": "dashboard"},
                )
                self.assertEqual(cleared.status_code, 200, cleared.text)
                agent = self._fetchone("SELECT session_handle, runtime_state FROM agents WHERE id = ?", (agent_id,))
                runtime_state = json.loads(agent["runtime_state"])
                self.assertEqual(agent["session_handle"], "")
                self.assertNotIn("sessionId", runtime_state)
                self.assertNotIn("threadId", runtime_state)
                session = self._fetchone("SELECT session_handle, capabilities, telemetry FROM agent_sessions WHERE id = ?", (session_id,))
                self.assertEqual(session["session_handle"], "")
                self.assertFalse(json.loads(session["capabilities"])["nativeResume"])
                self.assertNotIn("registeredHandle", json.loads(session["telemetry"]))

    def test_runtime_state_update_persists_reported_managed_native_handle(self):
        self._heartbeat_environment(
            id="linux:test-host:default",
            bridgeId="bridge-current",
            runtimes=[{"runtime": "pi", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "late-handle-pi",
                "role": "coder",
                "runtime": "pi",
                "workspace": "/workspace/project",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        running = self.client.patch(
            f"/api/v1/spawn-requests/{spawn_id}",
            json={
                "status": "running",
                "bridgeId": "bridge-current",
                "machineId": "linux:test-host",
                "runtimeState": {
                    "bridgeInstanceId": "bridge-current",
                    "environmentId": "linux:test-host:default",
                    "spawnRequestId": spawn_id,
                    "sessionId": "",
                },
            },
        )
        self.assertEqual(running.status_code, 200, running.text)
        self.assertEqual(running.json()["spawnRequest"]["sessionHandle"], "")

        state_update = self.client.patch(
            "/api/v1/agents/late-handle-pi/runtime-state",
            json={
                "runtimeState": {
                    "bridgeInstanceId": "bridge-current",
                    "environmentId": "linux:test-host:default",
                    "spawnRequestId": spawn_id,
                    "sessionId": "pi-native-session",
                }
            },
        )
        self.assertEqual(state_update.status_code, 200, state_update.text)

        agent = self._fetchone("SELECT session_handle, runtime_state FROM agents WHERE id = ?", ("late-handle-pi",))
        self.assertEqual(agent["session_handle"], "pi-native-session")
        self.assertEqual(json.loads(agent["runtime_state"])["sessionId"], "pi-native-session")
        session = self._fetchone(
            "SELECT session_handle FROM agent_sessions WHERE agent_id = ? AND spawn_request_id = ?",
            ("late-handle-pi", spawn_id),
        )
        self.assertEqual(session["session_handle"], "pi-native-session")

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

        self._register(
            "takeover-coder",
            runtime="codex",
            cwd="/workspace/repo",
            sessionMode="resident",
            sessionHandle="thread-from-cli",
            launchMode="codex-live",
        )
        session = self._fetchone("SELECT session_handle FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(session["session_handle"], "thread-from-cli")

    def test_resident_register_auto_takes_over_idle_managed_agent(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={"createdBy": "dashboard", "environmentId": "linux:test-host:default", "agentId": "auto-owner", "role": "coder", "runtime": "codex", "workspace": "/workspace/repo"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        self.client.post("/api/v1/spawn-requests/claim", json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"})
        running = self.client.patch(f"/api/v1/spawn-requests/{spawn_id}", json={"status": "running", "bridgeId": "bridge-current", "processId": "1234", "sessionHandle": "managed-thread"})
        self.assertEqual(running.status_code, 200, running.text)

        registered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "auto-owner",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "sessionHandle": "resident-thread",
                "machineId": "linux:test-host",
                "bridgeId": "resident-bridge",
                "capabilities": ["resident-run", "resume", "interrupt", "steer"],
                "runtimeConfig": {"appServerUrl": "ws://127.0.0.1:1234"},
            },
        )
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertEqual(registered.json()["sessionMode"], "resident")
        agent = self._fetchone("SELECT session_mode, session_handle, launch_mode, runtime_state FROM agents WHERE id = ?", ("auto-owner",))
        self.assertEqual(agent["session_mode"], "resident")
        self.assertEqual(agent["session_handle"], "resident-thread")
        self.assertNotEqual(agent["launch_mode"], "none")
        self.assertEqual(json.loads(agent["runtime_state"]).get("bridgeInstanceId"), "resident-bridge")
        session = self._fetchone("SELECT status, session_handle FROM agent_sessions WHERE agent_id = ?", ("auto-owner",))
        self.assertEqual(session["status"], "cli-takeover")
        self.assertEqual(session["session_handle"], "resident-thread")

    def test_resident_register_defers_takeover_until_active_managed_run_ends(self):
        self._heartbeat_environment()
        self._register("defer-owner", runtime="codex", sessionMode="managed", launchMode="managed", capabilities=["managed-run", "resume", "interrupt", "steer"])
        run = self._dispatch(from_agent="dashboard", to="defer-owner", subject="active", body="work", mode="start_if_possible")
        run_id = run["runs"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET status = 'running', started_at = ? WHERE id = ?", ("2026-01-01T00:00:00Z", run_id))

        registered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "defer-owner",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "sessionHandle": "resident-thread",
                "machineId": "linux:test-host",
                "bridgeId": "resident-bridge",
                "capabilities": ["resident-run", "resume", "interrupt", "steer"],
                "runtimeConfig": {"appServerUrl": "ws://127.0.0.1:1234"},
            },
        )
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertEqual(registered.json()["ownershipTransition"], "pending_resident_takeover")
        agent = self._fetchone("SELECT session_mode, runtime_state FROM agents WHERE id = ?", ("defer-owner",))
        self.assertEqual(agent["session_mode"], "managed")
        self.assertIn("pendingResidentTakeover", json.loads(agent["runtime_state"]))

        patched = self.client.patch(f"/api/v1/dispatch/runs/{run_id}", json={"status": "completed", "summary": "done"})
        self.assertEqual(patched.status_code, 200, patched.text)
        agent = self._fetchone("SELECT session_mode, session_handle, runtime_state FROM agents WHERE id = ?", ("defer-owner",))
        self.assertEqual(agent["session_mode"], "resident")
        self.assertEqual(agent["session_handle"], "resident-thread")
        self.assertNotIn("pendingResidentTakeover", json.loads(agent["runtime_state"]))

    def test_pending_resident_runtime_patch_does_not_clobber_managed_bridge(self):
        self._heartbeat_environment()
        self._register(
            "pending-owner",
            runtime="codex",
            sessionMode="managed",
            launchMode="managed",
            capabilities=["managed-run", "resume", "interrupt", "steer"],
            runtimeConfig={"sandboxMode": "danger-full-access"},
        )
        self.client.patch(
            "/api/v1/agents/pending-owner/runtime-state",
            json={"runtimeState": {"bridgeInstanceId": "managed-bridge", "environmentId": "linux:test-host:default"}},
        )
        run = self._dispatch(from_agent="dashboard", to="pending-owner", subject="active", body="work", mode="start_if_possible")
        run_id = run["runs"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET status = 'running', started_at = ? WHERE id = ?", ("2026-01-01T00:00:00Z", run_id))

        registered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "pending-owner",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "sessionHandle": "resident-thread",
                "machineId": "linux:test-host",
                "bridgeId": "resident-bridge",
                "capabilities": ["resident-run", "resume", "interrupt", "steer"],
                "runtimeConfig": {"appServerUrl": "ws://127.0.0.1:1234"},
            },
        )
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertEqual(registered.json()["ownershipTransition"], "pending_resident_takeover")

        patched = self.client.patch(
            "/api/v1/agents/pending-owner/runtime-state",
            json={"runtimeState": {"bridgeInstanceId": "resident-bridge", "threadId": "resident-thread"}},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        state = patched.json()["runtimeState"]
        self.assertEqual(state["bridgeInstanceId"], "managed-bridge")
        self.assertEqual(state["environmentId"], "linux:test-host:default")
        self.assertEqual(state["pendingResidentTakeover"]["bridgeId"], "resident-bridge")
        agent = self._fetchone("SELECT session_mode FROM agents WHERE id = ?", ("pending-owner",))
        self.assertEqual(agent["session_mode"], "managed")

    def test_stale_resident_auto_returns_to_managed_on_send(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={"createdBy": "dashboard", "environmentId": "linux:test-host:default", "agentId": "return-owner", "role": "coder", "runtime": "codex", "workspace": "/workspace/repo"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]
        self.client.post("/api/v1/spawn-requests/claim", json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"})
        self.client.patch(f"/api/v1/spawn-requests/{spawn_id}", json={"status": "running", "bridgeId": "bridge-current", "processId": "1234", "sessionHandle": "managed-thread"})
        self._register(
            "return-owner",
            runtime="codex",
            sessionMode="resident",
            sessionHandle="resident-thread",
            machineId="linux:test-host",
            bridgeId="resident-bridge",
            capabilities=["resident-run", "resume", "interrupt", "steer"],
            runtimeConfig={"appServerUrl": "ws://127.0.0.1:1234"},
        )
        self._execute("UPDATE agents SET last_seen = ?, runtime_state = ? WHERE id = ?", ("2000-01-01T00:00:00Z", json.dumps({"bridgeInstanceId": "resident-bridge"}), "return-owner"))
        self._execute("UPDATE bridge_instances SET last_seen = ? WHERE id = ?", ("2000-01-01T00:00:00Z", "resident-bridge"))

        sent = self._send_message(from_agent="dashboard", to="return-owner", type="request", subject="resume managed", body="hello", trigger=True)
        self.assertTrue(sent["ok"])
        run = self._fetchone("SELECT execution_mode FROM dispatch_runs WHERE id = ?", (sent["dispatchRuns"][0]["runId"],))
        self.assertEqual(run["execution_mode"], "managed")
        agent = self._fetchone("SELECT session_mode, launch_mode, session_handle FROM agents WHERE id = ?", ("return-owner",))
        self.assertEqual(agent["session_mode"], "managed")
        self.assertEqual(agent["launch_mode"], "managed")
        self.assertEqual(agent["session_handle"], "resident-thread")

    def test_session_stop_marks_resident_owner_for_bridge_termination(self):
        self._heartbeat_environment()
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={"createdBy": "dashboard", "environmentId": "linux:test-host:default", "agentId": "stop-resident", "role": "coder", "runtime": "codex", "workspace": "/workspace/repo"},
        )
        spawn_id = created.json()["spawnRequest"]["id"]
        self.client.post("/api/v1/spawn-requests/claim", json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"})
        running = self.client.patch(f"/api/v1/spawn-requests/{spawn_id}", json={"status": "running", "bridgeId": "bridge-current", "processId": "1234", "sessionHandle": "managed-thread"})
        session_id = running.json()["spawnRequest"]["sessionId"]
        self._register(
            "stop-resident",
            runtime="codex",
            sessionMode="resident",
            sessionHandle="resident-thread",
            machineId="linux:test-host",
            bridgeId="resident-bridge",
            capabilities=["resident-run", "resume", "interrupt", "steer"],
            runtimeConfig={"appServerUrl": "ws://127.0.0.1:1234"},
        )

        stopped = self.client.post(f"/api/v1/sessions/{session_id}/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(stopped.status_code, 200, stopped.text)
        agent = self._fetchone("SELECT status, launch_mode, status_note FROM agents WHERE id = ?", ("stop-resident",))
        self.assertEqual(agent["status"], "stopped")
        self.assertEqual(agent["launch_mode"], "none")
        self.assertIn("terminate", agent["status_note"])

    def test_agent_stop_marks_resident_owner_for_bridge_termination(self):
        self._register(
            "agent-stop-resident",
            runtime="codex",
            sessionMode="resident",
            sessionHandle="resident-thread",
            machineId="linux:test-host",
            bridgeId="resident-bridge",
            capabilities=["resident-run", "resume", "interrupt", "steer"],
            runtimeConfig={"appServerUrl": "ws://127.0.0.1:1234"},
        )

        stopped = self.client.post("/api/v1/agents/agent-stop-resident/control", json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(stopped.status_code, 200, stopped.text)
        agent = self._fetchone("SELECT status, launch_mode, status_note FROM agents WHERE id = ?", ("agent-stop-resident",))
        self.assertEqual(agent["status"], "stopped")
        self.assertEqual(agent["launch_mode"], "none")
        self.assertIn("terminate", agent["status_note"])

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

    def test_spawn_request_normalizes_linux_workspace_slashes_before_persisting(self):
        self._heartbeat_environment(cwdRoots=["/home/dev/projects"])
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={
                "createdBy": "dashboard",
                "environmentId": "linux:test-host:default",
                "agentId": "linux-path-agent",
                "role": "coder",
                "runtime": "codex",
                "workspace": "\\home\\dev\\projects\\blei-code-intel",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        spawn = created.json()["spawnRequest"]
        self.assertEqual(spawn["workspace"], "/home/dev/projects/blei-code-intel")
        self.assertEqual(spawn["spawnSpec"]["workspace"], "/home/dev/projects/blei-code-intel")

        claimed = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current", "machineId": "linux:test-host"},
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["spawnRequest"]["workspace"], "/home/dev/projects/blei-code-intel")

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

    def test_unsending_queued_message_cancels_attached_dispatch_run(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("manager", role="manager", runtime="claude-code", sessionMode="managed")

        active = self._dispatch(
            from_agent="dashboard",
            to="manager",
            type="request",
            subject="active turn",
            body="keep going",
            mode="start_if_possible",
            createMessage=True,
        )
        active_run_id = active["runs"][0]["runId"]
        self._execute(
            "UPDATE dispatch_runs SET status = 'running', started_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00Z", active_run_id),
        )

        sent = self._send_message(
            from_agent="lead",
            to="manager",
            type="request",
            subject="queued update",
            body="do this later",
            trigger=True,
        )
        self.assertTrue(sent["ok"])
        message_id = sent["messageId"]
        queued_run_id = sent["dispatchRuns"][0]["runId"]
        self.assertEqual(sent["dispatchRuns"][0]["status"], "queued")

        deleted = self.client.delete(f"/api/v1/messages/{message_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["deleted"], 1)
        self.assertEqual(deleted.json()["cancelledDispatchRuns"], 1)

        queued_run = self._fetchone(
            "SELECT status, summary, message_id, finished_at FROM dispatch_runs WHERE id = ?",
            (queued_run_id,),
        )
        self.assertEqual(queued_run["status"], "cancelled")
        self.assertEqual(queued_run["summary"], "Cancelled because source message was unsent.")
        self.assertIsNone(queued_run["message_id"])
        self.assertTrue(queued_run["finished_at"])

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
        result_message_id = final.json()["run"]["resultMessageId"]
        self.assertTrue(result_message_id)

        mirror = self._fetchone(
            "SELECT dispatch_requested FROM messages WHERE id = ?",
            (result_message_id,),
        )
        self.assertIsNotNone(mirror)
        self.assertEqual(mirror["dispatch_requested"], 1)
        delivery = self._fetchone(
            """
            SELECT from_agent, target_agent, status, require_reply
            FROM dispatch_runs
            WHERE message_id = ? AND id != ?
            """,
            (result_message_id, run_id),
        )
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery["from_agent"], "coder")
        self.assertEqual(delivery["target_agent"], "lead")
        self.assertEqual(delivery["status"], "queued")
        self.assertEqual(delivery["require_reply"], 0)

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

    def test_threaded_non_answer_message_does_not_close_reply_contract(self):
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
        run_id = request_send["dispatchRuns"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET status = 'completed', finished_at = ? WHERE id = ?", ("2026-01-01T00:00:00Z", run_id))

        follow_up = self._send_message(
            from_agent="coder",
            to="lead",
            type="info",
            subject="I saw this",
            body="not an answer yet",
            inReplyTo=request_send["messageId"],
            trigger=False,
        )
        self.assertTrue(follow_up["ok"])
        unchanged = self._fetchone("SELECT result_message_id FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(unchanged["result_message_id"] or "", "")

        answer = self._send_message(
            from_agent="coder",
            to="lead",
            type="response",
            subject="done",
            body="finished",
            inReplyTo=request_send["messageId"],
            trigger=False,
        )
        self.assertTrue(answer["ok"])
        closed = self._fetchone("SELECT result_message_id FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(closed["result_message_id"], answer["messageId"])

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

    def test_triggered_send_to_dashboard_is_store_only(self):
        self._register("manager", runtime="codex", sessionMode="managed")

        sent = self._send_message(
            from_agent="manager",
            to="dashboard",
            type="info",
            subject="async report",
            body="teammate acked",
            trigger=True,
        )

        self.assertTrue(sent["ok"])
        self.assertEqual(sent["recipients"], ["dashboard"])
        self.assertEqual(sent["dispatchRuns"], [])
        self.assertEqual(sent["recipientStatus"]["dashboard"]["runtime"], "dashboard")
        stored = self._fetchone(
            "SELECT from_agent, to_agent, dispatch_requested, body FROM messages WHERE id = ?",
            (sent["messageId"],),
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored["from_agent"], "manager")
        self.assertEqual(stored["to_agent"], "dashboard")
        self.assertEqual(stored["dispatch_requested"], 0)
        self.assertEqual(stored["body"], "teammate acked")
        self.assertIsNone(self._fetchone("SELECT id FROM dispatch_runs WHERE message_id = ?", (sent["messageId"],)))

    def test_triggered_channel_send_to_dashboard_member_is_store_only(self):
        self._register("manager", runtime="codex", sessionMode="managed")
        created = self.client.post(
            "/api/v1/channels",
            json={"name": "status", "description": "", "createdBy": "manager"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        joined = self.client.post("/api/v1/channels/status/join", json={"agentId": "dashboard"})
        self.assertEqual(joined.status_code, 200, joined.text)

        sent = self.client.post(
            "/api/v1/channels/status/send",
            json={"from_agent": "manager", "channel": "status", "body": "status update", "type": "info", "trigger": True},
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        payload = sent.json()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["recipients"], ["dashboard"])
        self.assertEqual(payload["dispatchRuns"], [])
        self.assertEqual(payload["recipientStatus"]["dashboard"]["runtime"], "dashboard")
        stored = self._fetchone(
            "SELECT from_agent, to_agent, channel, source, dispatch_requested, body FROM messages WHERE to_agent = ? AND channel = ?",
            ("dashboard", "status"),
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored["from_agent"], "manager")
        self.assertEqual(stored["source"], "channel")
        self.assertEqual(stored["dispatch_requested"], 0)
        self.assertEqual(stored["body"], "status update")
        self.assertIsNone(self._fetchone("SELECT id FROM dispatch_runs WHERE target_agent = ?", ("dashboard",)))

    def test_async_manager_summary_is_reported_to_dashboard_chat(self):
        self._register("manager", role="manager", runtime="claude-code", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        sent = self._send_message(
            from_agent="coder",
            to="manager",
            type="response",
            subject="Re: ping results",
            body="Coder result arrived",
            trigger=True,
        )
        self.assertTrue(sent["ok"])
        run_id = sent["dispatchRuns"][0]["runId"]

        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "Both teammates answered. Coder is online."},
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        report = self._fetchone(
            """
            SELECT from_agent, to_agent, type, subject, body, dispatch_requested
            FROM messages
            WHERE from_agent = 'manager' AND to_agent = 'dashboard'
            ORDER BY timestamp DESC
            LIMIT 1
            """
        )
        self.assertIsNotNone(report)
        self.assertEqual(report["type"], "info")
        self.assertEqual(report["body"], "Both teammates answered. Coder is online.")
        self.assertEqual(report["dispatch_requested"], 0)

        duplicate = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "Both teammates answered. Coder is online."},
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        count = self._fetchone(
            "SELECT COUNT(*) AS c FROM messages WHERE from_agent = 'manager' AND to_agent = 'dashboard'"
        )
        self.assertEqual(count["c"], 1)

    def test_async_manager_summary_does_not_duplicate_explicit_dashboard_reply(self):
        self._register("manager", role="manager", runtime="claude-code", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        sent = self._send_message(
            from_agent="coder",
            to="manager",
            type="response",
            subject="Re: ping results",
            body="Coder result arrived",
            trigger=True,
        )
        self.assertTrue(sent["ok"])
        run_id = sent["dispatchRuns"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET status = 'running', started_at = ? WHERE id = ?", ("2026-01-01T00:00:00Z", run_id))
        self._send_message(
            from_agent="manager",
            to="dashboard",
            type="info",
            subject="Ping update",
            body="Explicit report.",
            trigger=True,
        )

        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "This should remain run summary only."},
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        count = self._fetchone(
            "SELECT COUNT(*) AS c FROM messages WHERE from_agent = 'manager' AND to_agent = 'dashboard'"
        )
        self.assertEqual(count["c"], 1)

    def test_triggered_send_steers_busy_target_by_default_and_can_explicitly_queue(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        active = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="active work",
            body="keep working",
            mode="start_if_possible",
            createMessage=True,
        )
        active_run_id = active["runs"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET status = 'running', started_at = ? WHERE id = ?", ("2026-01-01T00:00:00Z", active_run_id))

        steered = self._send_message(
            from_agent="lead",
            to="coder",
            type="request",
            subject="current guidance",
            body="take this into account now",
            trigger=True,
        )
        self.assertTrue(steered["ok"])
        self.assertEqual(steered["dispatchRuns"][0]["status"], "steered")
        self.assertEqual(steered["dispatchRuns"][0]["runId"], active_run_id)
        contract_run_id = steered["dispatchRuns"][0]["contractRunId"]
        self.assertTrue(contract_run_id)
        control = self._fetchone(
            "SELECT action, status, source_message_id FROM dispatch_controls WHERE run_id = ?",
            (active_run_id,),
        )
        self.assertIsNotNone(control)
        self.assertEqual(control["action"], "steer")
        self.assertTrue(control["source_message_id"])
        contract = self._fetchone("SELECT status, message_id, require_reply FROM dispatch_runs WHERE id = ?", (contract_run_id,))
        self.assertIsNotNone(contract)
        self.assertEqual(contract["status"], "delivered")
        self.assertEqual(contract["message_id"], control["source_message_id"])
        self.assertEqual(contract["require_reply"], 1)
        contracts = self.client.get("/api/v1/contracts?limit=20&state=open&category=direct")
        self.assertEqual(contracts.status_code, 200, contracts.text)
        self.assertTrue(any(item["id"] == contract_run_id and item["state"] == "sent" for item in contracts.json()["contracts"]))

        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{active_run_id}",
            json={"status": "completed", "summary": "handled active and steered work", "resultMessageId": "reply-active"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        closed_contract = self._fetchone("SELECT result_message_id FROM dispatch_runs WHERE id = ?", (contract_run_id,))
        self.assertEqual(closed_contract["result_message_id"], "reply-active")
        answered = self.client.get("/api/v1/contracts?limit=20&state=answered&category=direct")
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertTrue(any(item["id"] == contract_run_id and item["state"] == "answered" for item in answered.json()["contracts"]))

        self._execute("UPDATE dispatch_runs SET status = 'running', result_message_id = '' WHERE id = ?", (active_run_id,))
        queued = self._send_message(
            from_agent="lead",
            to="coder",
            type="request",
            subject="queued work",
            body="next thing",
            trigger=True,
            queueIfBusy=True,
        )
        self.assertTrue(queued["ok"])
        self.assertTrue(queued["messageId"])
        self.assertEqual(queued["dispatchRuns"][0]["status"], "queued")
        self.assertEqual(queued["dispatchRuns"][0]["queuedBehindActiveRun"]["runId"], active_run_id)

    def test_normal_send_to_busy_non_steerable_target_queues_as_fallback(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("qa", runtime="codex", sessionMode="managed")
        self._register("manager", role="manager", runtime="claude-code", sessionMode="managed")

        active = self._dispatch(
            from_agent="dashboard",
            to="manager",
            type="request",
            subject="coordinate",
            body="coordinate team",
            mode="start_if_possible",
            createMessage=True,
        )
        active_run_id = active["runs"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET status = 'running', started_at = ? WHERE id = ?", ("2026-01-01T00:00:00Z", active_run_id))

        sent = self._send_message(
            from_agent="lead",
            to="manager",
            type="request",
            subject="new input",
            body="include this when you can",
            trigger=True,
        )
        self.assertTrue(sent["ok"])
        self.assertEqual(sent["notStarted"], [])
        self.assertEqual(sent["dispatchRuns"][0]["status"], "queued")
        self.assertEqual(sent["dispatchRuns"][0]["queuedBehindActiveRun"]["runId"], active_run_id)

        second_sender = self._send_message(
            from_agent="qa",
            to="manager",
            type="request",
            subject="qa input",
            body="include qa separately",
            trigger=True,
        )
        self.assertTrue(second_sender["ok"])
        self.assertEqual(second_sender["dispatchRuns"][0]["status"], "queued")
        self.assertNotEqual(second_sender["dispatchRuns"][0]["runId"], sent["dispatchRuns"][0]["runId"])
        queued_rows = self._fetchall("SELECT from_agent, target_agent FROM dispatch_runs WHERE target_agent = ? AND status = 'queued' ORDER BY requested_at", ("manager",))
        self.assertEqual([row["from_agent"] for row in queued_rows], ["lead", "qa"])

    def test_stale_pi_capabilities_gain_steer_without_recreate(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register(
            "old-pi",
            runtime="pi",
            sessionMode="managed",
            launchMode="managed",
            capabilities=["managed-run", "resume", "interrupt", "spawn"],
        )
        info = self.client.get("/api/v1/agents/old-pi")
        self.assertEqual(info.status_code, 200, info.text)
        self.assertIn("steer", info.json()["agent"]["capabilities"])

        active = self._dispatch(
            from_agent="dashboard",
            to="old-pi",
            type="request",
            subject="active",
            body="work",
            mode="start_if_possible",
            createMessage=True,
        )
        active_run_id = active["runs"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET status = 'running', started_at = ? WHERE id = ?", ("2026-01-01T00:00:00Z", active_run_id))

        steered = self._send_message(
            from_agent="lead",
            to="old-pi",
            type="request",
            subject="current guidance",
            body="use this now",
            trigger=True,
        )
        self.assertTrue(steered["ok"], steered)
        self.assertEqual(steered["dispatchRuns"][0]["status"], "steered")
        self.assertEqual(steered["dispatchRuns"][0]["runId"], active_run_id)

        self._register(
            "old-resident-pi",
            runtime="pi",
            sessionMode="resident",
            sessionHandle="pi-session",
            capabilities=["resident-run", "resume", "interrupt"],
        )
        resident = self.client.get("/api/v1/agents/old-resident-pi")
        self.assertEqual(resident.status_code, 200, resident.text)
        self.assertIn("steer", resident.json()["agent"]["capabilities"])

    def test_response_messages_steer_when_sender_is_busy_and_steer_capable(self):
        self._register("manager", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        active = self._dispatch(
            from_agent="dashboard",
            to="manager",
            type="request",
            subject="coordinate",
            body="coordinate team",
            mode="start_if_possible",
            createMessage=True,
        )
        active_run_id = active["runs"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET status = 'running', started_at = ? WHERE id = ?", ("2026-01-01T00:00:00Z", active_run_id))

        reply = self._send_message(
            from_agent="coder",
            to="manager",
            type="response",
            subject="Re: coordinate",
            body="I am done.",
            trigger=True,
        )
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["dispatchRuns"][0]["status"], "steered")
        self.assertEqual(reply["dispatchRuns"][0]["requireReply"], False)
        self.assertEqual(reply["dispatchRuns"][0]["runId"], active_run_id)

    def test_claude_aify_resident_channel_agents_are_steer_capable(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register(
            "claude",
            runtime="claude-code",
            sessionMode="resident",
            runtimeConfig={"channelEnabled": True},
        )
        info = self.client.get("/api/v1/agents/claude")
        self.assertEqual(info.status_code, 200, info.text)
        self.assertIn("steer", info.json()["agent"]["capabilities"])
        self.assertEqual(info.json()["agent"]["wakeMode"], "claude-live")

        active = self._dispatch(
            from_agent="lead",
            to="claude",
            type="request",
            subject="active resident work",
            body="handle this",
            mode="start_if_possible",
            createMessage=True,
        )
        active_run_id = active["runs"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET status = 'running', started_at = ? WHERE id = ?", ("2026-01-01T00:00:00Z", active_run_id))

        steered = self._send_message(
            from_agent="lead",
            to="claude",
            type="info",
            subject="new context",
            body="apply this to current work",
            trigger=True,
        )
        self.assertTrue(steered["ok"])
        self.assertEqual(steered["dispatchRuns"][0]["status"], "steered")
        self.assertEqual(steered["dispatchRuns"][0]["runId"], active_run_id)

    def test_plain_claude_resident_without_channel_is_not_live_wake_capable(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register(
            "plain-claude",
            runtime="claude-code",
            sessionMode="resident",
            capabilities=["resident-run", "interrupt", "steer"],
        )

        info = self.client.get("/api/v1/agents/plain-claude")
        self.assertEqual(info.status_code, 200, info.text)
        self.assertNotIn("resident-run", info.json()["agent"]["capabilities"])
        self.assertNotIn("steer", info.json()["agent"]["capabilities"])
        self.assertEqual(info.json()["agent"]["wakeMode"], "claude-needs-channel")

        sent = self._send_message(
            from_agent="lead",
            to="plain-claude",
            type="request",
            subject="hello",
            body="this should not be queued behind a non-channel resident",
            trigger=True,
        )
        self.assertFalse(sent["ok"])
        self.assertIn("cannot start live work", sent["error"])

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

    def test_triggered_send_merges_existing_future_queue(self):
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
        self.assertTrue(second["ok"])
        self.assertEqual(second["notStarted"], [])
        self.assertEqual(second["dispatchRuns"][0]["runId"], first["runs"][0]["runId"])
        self.assertTrue(second["dispatchRuns"][0]["merged"])
        self.assertEqual(second["dispatchRuns"][0]["mergedCount"], 2)
        second_message = self._fetchone("SELECT id FROM messages WHERE subject = ?", ("second",))
        self.assertIsNotNone(second_message)

        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "worker", "machineId": "", "bridgeId": "bridge-1", "executionModes": ["managed"]},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual(claim.json()["run"]["id"], first["runs"][0]["runId"])
        self.assertIn("second", claim.json()["run"]["subject"])
        self.assertIn("two", claim.json()["run"]["body"])

        receipts = self._fetchall(
            "SELECT message_id FROM read_receipts WHERE agent_id = ? ORDER BY message_id",
            ("worker",),
        )
        self.assertEqual({row["message_id"] for row in receipts}, {first_message_id, second["messageId"]})

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
        self._register(
            "worker",
            runtime="claude-code",
            sessionMode="resident",
            bridgeId="stdio-current",
            runtimeConfig={"channelEnabled": True},
        )
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

    def test_dispatch_claim_ignores_stale_embedded_message_ids_when_marking_read(self):
        self._register("dashboard")
        self._register("worker", runtime="codex", sessionMode="managed")

        sent = self._send_message(
            from_agent="dashboard",
            to="worker",
            type="request",
            subject="please handle these",
            body="first message",
            trigger=True,
        )
        run_id = sent["dispatchRuns"][0]["runId"]
        source_message_id = sent["messageId"]
        self._execute(
            "UPDATE dispatch_runs SET body = ? WHERE id = ?",
            ("first message\n\n--- Message 2 ---\nMessage Id: missing-message\nbody", run_id),
        )

        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "worker", "machineId": "linux:test-host", "bridgeId": "bridge-1", "executionModes": ["managed"]},
        )

        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual(claim.json()["run"]["id"], run_id)
        receipt_rows = self._fetchall("SELECT message_id FROM read_receipts WHERE agent_id = ? ORDER BY message_id", ("worker",))
        self.assertEqual([row["message_id"] for row in receipt_rows], [source_message_id])

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
        self.assertEqual(message["body"], "ready for review")

    def test_claude_delivery_only_runs_do_not_count_as_pending_handoffs(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("tester", runtime="claude-code", sessionMode="resident", runtimeConfig={"channelEnabled": True})

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

    def test_contracts_classify_overdue_request(self):
        self._register("lead", runtime="codex", sessionMode="resident", sessionHandle="lead-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:1"})
        self._register("coder", runtime="codex", sessionMode="resident", sessionHandle="coder-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:2"})
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1})

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="needs answer",
            body="please answer",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET requested_at = '2000-01-01T00:00:00Z' WHERE id = ?", (run_id,))

        response = self.client.get("/api/v1/contracts?limit=10")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        contract = next(item for item in payload["contracts"] if item["id"] == run_id)
        self.assertEqual(contract["state"], "overdue")
        self.assertTrue(contract["replyExpected"])
        self.assertTrue(contract["overdue"])
        self.assertEqual(contract["category"], "direct")
        self.assertEqual(payload["summary"]["overdue"], 1)

    def test_contract_reminder_sends_notice_and_records_event(self):
        self._register("lead", runtime="codex", sessionMode="resident", sessionHandle="lead-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:1"})
        self._register("coder", runtime="codex", sessionMode="resident", sessionHandle="coder-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:2"})
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1})

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="review",
            subject="review please",
            body="review this",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET requested_at = '2000-01-01T00:00:00Z' WHERE id = ?", (run_id,))

        response = self.client.post(f"/api/v1/contracts/reminders/run?runId={run_id}")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["reminded"]), 1)
        reminder_message_id = payload["reminded"][0]["messageId"]

        reminder = self._fetchone("SELECT from_agent, to_agent, subject, body, dispatch_requested FROM messages WHERE id = ?", (reminder_message_id,))
        self.assertIsNotNone(reminder)
        self.assertEqual(reminder["from_agent"], "dashboard")
        self.assertEqual(reminder["to_agent"], "coder")
        self.assertIn("Reminder: reply overdue", reminder["subject"])
        self.assertIn('comms_inbox(agentId="coder"', reminder["body"])
        self.assertIn(f'inReplyTo="{created["messageId"]}"', reminder["body"])
        self.assertIn('comms_send(from="coder", to="lead", type="response"', reminder["body"])
        self.assertEqual(reminder["dispatch_requested"], 1)

        event = self._fetchone("SELECT event_type, body FROM dispatch_events WHERE run_id = ? AND event_type = 'reply_reminder'", (run_id,))
        self.assertIsNotNone(event)
        self.assertIn(reminder_message_id, event["body"])

    def test_contracts_do_not_treat_high_priority_responses_as_missing_replies(self):
        self._register("lead", runtime="codex", sessionMode="resident", sessionHandle="lead-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:1"})
        self._register("coder", runtime="codex", sessionMode="resident", sessionHandle="coder-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:2"})

        created = self._dispatch(
            from_agent="coder",
            to="lead",
            type="response",
            subject="Re: review",
            body="done",
            priority="high",
            mode="start_if_possible",
            createMessage=True,
            requireReply=False,
        )
        run_id = created["runs"][0]["runId"]
        self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "response delivered"},
        )

        response = self.client.get("/api/v1/contracts?limit=20&includeClosed=true")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(any(item["id"] == run_id for item in response.json()["contracts"]))

    def test_contracts_hide_answered_rows_until_history_is_requested(self):
        self._register("lead", runtime="codex", sessionMode="resident", sessionHandle="lead-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:1"})
        self._register("coder", runtime="codex", sessionMode="resident", sessionHandle="coder-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:2"})

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="needs answer",
            body="please answer",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={
                "status": "completed",
                "summary": "answered",
                "resultMessageId": "reply-1",
            },
        )

        open_view = self.client.get("/api/v1/contracts?limit=20")
        self.assertEqual(open_view.status_code, 200, open_view.text)
        self.assertFalse(any(item["id"] == run_id for item in open_view.json()["contracts"]))

        history_view = self.client.get("/api/v1/contracts?limit=20&includeClosed=true")
        self.assertEqual(history_view.status_code, 200, history_view.text)
        contract = next(item for item in history_view.json()["contracts"] if item["id"] == run_id)
        self.assertEqual(contract["state"], "answered")

        answered_view = self.client.get("/api/v1/contracts?limit=20&state=answered")
        self.assertEqual(answered_view.status_code, 200, answered_view.text)
        answered_contract = next(item for item in answered_view.json()["contracts"] if item["id"] == run_id)
        self.assertEqual(answered_contract["state"], "answered")

    def test_contract_history_respects_stale_window(self):
        self._register("lead", runtime="codex", sessionMode="resident", sessionHandle="lead-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:1"})
        self._register("coder", runtime="codex", sessionMode="resident", sessionHandle="coder-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:2"})
        self.client.put("/api/v1/settings", json={"contract_stale_hours": 1})

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="old answered",
            body="please answer",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "answered", "resultMessageId": "reply-old"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        self._execute("UPDATE dispatch_runs SET requested_at = ?, finished_at = ? WHERE id = ?", ("2000-01-01T00:00:00Z", "2000-01-01T00:00:01Z", run_id))

        history_view = self.client.get("/api/v1/contracts?limit=20&includeClosed=true")
        self.assertEqual(history_view.status_code, 200, history_view.text)
        self.assertFalse(any(item["id"] == run_id for item in history_view.json()["contracts"]))

        answered_view = self.client.get("/api/v1/contracts?limit=20&state=answered")
        self.assertEqual(answered_view.status_code, 200, answered_view.text)
        self.assertFalse(any(item["id"] == run_id for item in answered_view.json()["contracts"]))

    def test_contracts_can_filter_category_before_limit(self):
        self._register("lead", runtime="codex", sessionMode="resident", sessionHandle="lead-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:1"})
        self._register("coder", runtime="codex", sessionMode="resident", sessionHandle="coder-thread", runtimeConfig={"appServerUrl": "ws://127.0.0.1:2"})

        direct = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="direct work",
            body="please answer",
            mode="start_if_possible",
            createMessage=True,
        )["runs"][0]["runId"]
        self._dispatch(
            from_agent="lead",
            to="lead",
            type="request",
            subject="self wake",
            body="continue",
            mode="start_if_possible",
            createMessage=True,
        )
        self.client.post("/api/v1/channels/ops", json={"description": "ops", "createdBy": "lead"})
        self.client.post("/api/v1/channels/ops/join", json={"agentId": "coder"})
        self.client.post(
            "/api/v1/channels/ops/send",
            json={"from_agent": "lead", "channel": "ops", "type": "request", "body": "channel work", "trigger": True},
        )

        response = self.client.get("/api/v1/contracts?limit=20&category=direct")
        self.assertEqual(response.status_code, 200, response.text)
        contracts = response.json()["contracts"]
        self.assertTrue(any(item["id"] == direct for item in contracts))
        self.assertTrue(all(item["category"] == "direct" for item in contracts))

        open_response = self.client.get("/api/v1/contracts?limit=20&category=direct&state=open")
        self.assertEqual(open_response.status_code, 200, open_response.text)
        open_contracts = open_response.json()["contracts"]
        self.assertTrue(any(item["id"] == direct for item in open_contracts))
        self.assertTrue(all(item["category"] == "direct" for item in open_contracts))
        self.assertTrue(all(item["state"] in {"sent", "seen", "queued", "working", "overdue"} for item in open_contracts))

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
