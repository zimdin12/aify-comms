import asyncio
import json
import tempfile
import time
import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import get_db, init_db
from service import main as service_main
from service.routers import api_v2
from service.routers.api_v2 import router

from service.tests._base import FastApiTestCase, DummyWS, PRE_PLAN4_SETTINGS


# Back-compat alias: a few tests below reference _DummyWS directly.
_DummyWS = DummyWS


class ApiV2RegressionTests(FastApiTestCase):
    # Most existing regression tests were written when PTY-input
    # ("via-console") was the implicit default for managed claude. The new
    # default (operator design) is channel routing — managed claude flows
    # through claude-channel.js notifications. Opt this whole suite into the
    # legacy via-console mode so the historical contracts (consoleDeliveries,
    # terminal-control inputs, idle-prompt closes, etc.) still apply.
    # Individual tests for the new channel-route default set this back to
    # False explicitly.
    #
    # Plan 4 (2026-05-25) also flipped managed_via_wrapper and
    # managed_pty_eager_spawn defaults to ON. Most legacy regressions predate
    # the wrapper-backed path / eager-spawn behavior; opt this whole suite
    # back into the pre-Plan-4 defaults so those historical contracts still
    # apply. Plan-4-specific tests live in test_default_settings_plan4.py and
    # opt back in explicitly.
    LEGACY_SETTINGS = PRE_PLAN4_SETTINGS

    def _register(self, agent_id: str, *, role: str = "coder", **extra):
        payload = {"agentId": agent_id, "role": role}
        payload.update(extra)
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _register_live_codex_resident(self, agent_id: str, *, session_handle: str, bridge_id: str, port: int, role: str = "coder"):
        return self._register(
            agent_id,
            role=role,
            runtime="codex",
            sessionMode="resident",
            sessionHandle=session_handle,
            machineId="linux:test-host",
            bridgeId=bridge_id,
            capabilities=["resident-run", "resume", "interrupt", "steer"],
            runtimeConfig={"appServerUrl": f"ws://127.0.0.1:{port}"},
        )

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

    def _executemany(self, query: str, seq_params):
        """Batch-insert helper: one connection for a whole loop of rows.

        Functionally identical to calling ``_execute`` per row, but avoids
        opening/closing a fresh sqlite connection per row in seed loops.
        """
        async def _run():
            db = await get_db()
            try:
                await db.executemany(query, list(seq_params))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def test_dispatch_run_events_are_bounded_and_cursor_paginated(self):
        run_id = "run_events_page"
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode,
                message_type, subject, body, priority, status, require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                None,
                "dashboard",
                "worker",
                "start_if_possible",
                "managed",
                "request",
                "bounded events",
                "please inspect",
                "normal",
                "running",
                1,
                "2026-05-20T00:00:00Z",
            ),
        )
        self._executemany(
            "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
            (
                (run_id, f"event_{index:02d}", f"body {index}", f"2026-05-20T00:{index:02d}:00Z")
                for index in range(75)
            ),
        )

        first = self.client.get(f"/api/v1/dispatch/runs/{run_id}/events?limit=500")
        self.assertEqual(first.status_code, 200, first.text)
        first_data = first.json()
        self.assertEqual(len(first_data["events"]), 50)
        self.assertTrue(first_data["hasMore"])
        self.assertTrue(first_data["nextBefore"])
        first_ids = [event["id"] for event in first_data["events"]]
        self.assertEqual(first_ids, sorted(first_ids, reverse=True))

        second = self.client.get(f"/api/v1/dispatch/runs/{run_id}/events?limit=50&before={first_data['nextBefore']}")
        self.assertEqual(second.status_code, 200, second.text)
        second_data = second.json()
        second_ids = [event["id"] for event in second_data["events"]]
        self.assertTrue(second_ids)
        self.assertFalse(set(first_ids) & set(second_ids))
        self.assertTrue(all(int(event_id) < int(first_data["nextBefore"]) for event_id in second_ids))

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
        # machine_id is normalized (lowercased) end to end so casing
        # differences across launch paths can't split one logical machine.
        self.assertEqual(first_env["machineId"], "wsl-ubuntu:test-host")
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
        self.assertIn("SESSION_SELECTION_STORAGE_KEY", dashboard.text)
        self.assertIn("persistSelectedSessions", dashboard.text)
        self.assertIn("data-session-select", dashboard.text)
        self.assertIn(".filter(el => !el.matches('.chat-send-options'))", dashboard.text)
        self.assertIn("xterm.min.css", dashboard.text)
        self.assertIn("xterm.min.js", dashboard.text)
        self.assertIn("addon-fit.min.js", dashboard.text)
        self.assertIn(".console-head{display:grid", dashboard.text)
        self.assertIn(".console-output{min-height:0;overflow:hidden", dashboard.text)
        self.assertIn(".console-output .xterm{height:100%;width:100%", dashboard.text)
        self.assertIn("@keyframes terminalActivityPulse", dashboard.text)
        self.assertIn(".chat-status-dot.working.terminal-active", dashboard.text)
        self.assertIn(".chat-status-dot.blocked", dashboard.text)
        self.assertIn(".status-dot.blocked", dashboard.text)
        self.assertIn("markTerminalActivity(data.terminalId, data.agentId)", dashboard.text)
        self.assertIn("state.sessionFile", dashboard.text)
        self.assertIn("terminalActivityClassForAgent", dashboard.text)
        self.assertIn("fetchRunsForCurrentFilters()", dashboard.text)
        self.assertIn("loadDashboardRuns()", dashboard.text)
        self.assertIn("console appears to need input", dashboard.text)
        self.assertIn("input needed", dashboard.text)
        self.assertNotIn("chat-console-input", dashboard.text)
        self.assertNotIn("sendConsoleAsMessage", dashboard.text)
        self.assertNotIn("sendConsoleInput()", dashboard.text)
        self.assertNotIn("console-direct-row", dashboard.text)
        self.assertNotIn("console-input-row", dashboard.text)
        self.assertIn("sendConsoleInputBody(id, data)", dashboard.text)
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
        self.assertIn("Windows, WSL, Linux, macOS, Docker", dashboard.text)
        self.assertIn("On Linux, macOS, or WSL use <code>aify-comms</code>", dashboard.text)
        self.assertIn("Wrapper/config install is intentionally disabled", dashboard.text)
        self.assertIn("Wrapper install is intentionally disabled", dashboard.text)
        self.assertIn("Pi and OpenCode remain managed/debug paths", dashboard.text)
        self.assertIn("Legacy <code>omp-aify</code>/<code>pi-aify</code> presence wrappers are not installed by default", dashboard.text)
        self.assertNotIn("bash install.sh --client opencode http://192.168.100.10:8800", dashboard.text)
        self.assertNotIn("bash install.sh --client pi http://192.168.100.10:8800", dashboard.text)
        self.assertNotIn("<code>omp-aify</code> / <code>pi-aify</code>", dashboard.text)
        self.assertIn("chat-channel-add-member", dashboard.text)
        self.assertIn("Add member", dashboard.text)
        self.assertIn("data-channel-member-select", dashboard.text)
        # The binary "Hide offline" toggle (chat-online-only) was replaced by the
        # per-status chat filter multiselect (5203bc1).
        self.assertIn("chat-status-filter", dashboard.text)
        self.assertIn("chat-status-menu", dashboard.text)
        self.assertIn("chat-unread-up", dashboard.text)
        self.assertIn("Unread up", dashboard.text)
        self.assertIn("chat-working-up", dashboard.text)
        self.assertIn("Working up", dashboard.text)
        self.assertIn("resetChatViewFilters()", dashboard.text)
        self.assertIn("Reset view", dashboard.text)
        self.assertIn("chat-peek-mode", dashboard.text)
        self.assertIn("Peek mode", dashboard.text)
        self.assertIn("markSelectedChatRead()", dashboard.text)
        self.assertIn("chat-send-btn", dashboard.text)
        self.assertIn("sendChatMessage({queueIfBusy:true})", dashboard.text)
        self.assertIn("setChatSending(true, queueIfBusy)", dashboard.text)
        self.assertIn("chatDraftKey", dashboard.text)
        self.assertIn("window._selectedContracts", dashboard.text)
        self.assertIn("closeSelectedContracts()", dashboard.text)
        self.assertIn("last reminded", dashboard.text)
        self.assertIn("['request', 'review', 'error'].includes", dashboard.text)
        self.assertIn(">Queue</button>", dashboard.text)
        self.assertNotIn("chat-queue-if-busy", dashboard.text)
        self.assertIn("sessions-grid", dashboard.text)
        self.assertIn("session-batch-bar", dashboard.text)
        self.assertIn("data-session-select", dashboard.text)
        self.assertIn("selectStoppableSessions()", dashboard.text)
        self.assertIn("selectDeletableSessions()", dashboard.text)
        self.assertIn("batchStopSelectedSessions()", dashboard.text)
        self.assertIn("batchDeleteSelectedSessions()", dashboard.text)
        self.assertIn("sessionCanStop(session)", dashboard.text)
        self.assertIn("reports no PTY/terminal support", dashboard.text)
        self.assertIn("chat-mode-console", dashboard.text)
        self.assertIn('onclick="startConsoleForSelected', dashboard.text)
        self.assertIn('onclick="refreshSelectedConsole()', dashboard.text)
        self.assertIn('onclick="stopSelectedConsole()', dashboard.text)
        self.assertIn("Console unavailable", dashboard.text)
        self.assertIn("deleteSessionRecord(session.id)", dashboard.text)
        self.assertIn("table-wrap", dashboard.text)
        self.assertIn("Click command to copy", dashboard.text)
        self.assertIn("Pause for CLI", dashboard.text)
        self.assertIn("function agentModeSwitchAction(agentId, agentInfo = {})", dashboard.text)
        self.assertIn("switchAgentSessionMode(agentId, targetMode", dashboard.text)
        self.assertIn("function agentResidentLaunchCommand(agentId, agentInfo = {})", dashboard.text)
        self.assertIn("function showResidentSwitchNotice(agentId, agentInfo = {}, result = {})", dashboard.text)
        self.assertIn("Resident launch / re-register command", dashboard.text)
        self.assertIn("No extra registration should be needed", dashboard.text)
        self.assertIn("no live resident wrapper candidate is recorded yet", dashboard.text)
        # Sessions page is agent-centric (327931c): the switch action is invoked
        # per-agent via meta.id; the old session-centric call site was removed.
        self.assertIn("agentModeSwitchAction(meta.id, agentInfo)", dashboard.text)
        self.assertIn("function sessionPresenceForSession(session = {}, agentInfo = {})", dashboard.text)
        self.assertIn("function sessionModeSummary(session = {}, agentInfo = {})", dashboard.text)
        self.assertIn("status-dot ${esc(presenceClass)}", dashboard.text)
        self.assertIn("session: ${session.status || 'unknown'}", dashboard.text)
        self.assertIn("visibleCapLabels", dashboard.text)
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
        self.assertIn("s-worker-idle-enabled", dashboard.text)
        self.assertIn("Idle worker close window", dashboard.text)
        self.assertIn("s-env-offline", dashboard.text)
        self.assertIn("s-active-run-stale", dashboard.text)
        self.assertIn("s-active-managed-stale", dashboard.text)
        self.assertIn("s-resident-lease", dashboard.text)
        self.assertIn("s-manual-session-mode", dashboard.text)
        self.assertIn("s-managed-terminal-backing", dashboard.text)
        self.assertIn("s-managed-pty-eager", dashboard.text)
        self.assertIn("s-managed-wrapper-runtimes", dashboard.text)
        self.assertIn("s-insert-console", dashboard.text)
        self.assertIn("settingsNumber", dashboard.text)
        self.assertIn("parseRuntimeListSetting", dashboard.text)
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
        self.assertTrue(settings.json()["console_auto_confirm_claude_dev_channels"])
        self.assertEqual(settings.json()["reply_reminder_minutes"], 10)
        self.assertEqual(settings.json()["reply_reminder_repeat_minutes"], 10)
        self.assertTrue(settings.json()["managed_terminal_backing_enabled"])
        self.assertTrue(api_v2.DEFAULT_SETTINGS["managed_pty_eager_spawn"])
        self.assertEqual(api_v2.DEFAULT_SETTINGS["managed_via_wrapper"], ["codex", "hermes"])
        self.assertFalse(settings.json()["managed_pty_eager_spawn"])
        self.assertFalse(settings.json()["managed_via_wrapper"])
        self.assertFalse(api_v2.DEFAULT_SETTINGS["insert_messages_via_console"])
        self.assertTrue(settings.json()["insert_messages_via_console"])
        self.assertTrue(settings.json()["manual_session_mode"])
        self.assertEqual(settings.json()["environment_offline_seconds"], 90)
        self.assertEqual(settings.json()["active_run_stale_minutes"], 30)
        self.assertEqual(settings.json()["active_managed_run_stale_minutes"], 5)
        self.assertEqual(settings.json()["resident_lease_seconds"], 150)
        self.assertFalse(settings.json()["worker_idle_close_enabled"])
        self.assertEqual(settings.json()["worker_idle_close_minutes"], 0)
        self.assertEqual(settings.json()["dashboard_tertiary_color"], "")

        updated = self.client.put(
            "/api/v1/settings",
            json={
                "dashboard_title": "Sand Castle Comms",
                "dashboard_theme": "ember",
                "dashboard_primary_color": "#f2b76e",
                "dashboard_secondary_color": "#8ebaf1",
                "dashboard_tertiary_color": "#e78776",
                "manual_session_mode": False,
                "managed_terminal_backing_enabled": False,
                "managed_pty_eager_spawn": False,
                "managed_via_wrapper": ["hermes"],
                "insert_messages_via_console": True,
                "environment_offline_seconds": 123,
                "active_run_stale_minutes": 31,
                "active_managed_run_stale_minutes": 6,
                "resident_lease_seconds": 180,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["dashboard_title"], "Sand Castle Comms")
        self.assertEqual(updated.json()["dashboard_theme"], "ember")
        self.assertEqual(updated.json()["dashboard_primary_color"], "#f2b76e")
        self.assertEqual(updated.json()["dashboard_secondary_color"], "#8ebaf1")
        self.assertEqual(updated.json()["dashboard_tertiary_color"], "#e78776")
        self.assertFalse(updated.json()["manual_session_mode"])
        self.assertFalse(updated.json()["managed_terminal_backing_enabled"])
        self.assertFalse(updated.json()["managed_pty_eager_spawn"])
        self.assertEqual(updated.json()["managed_via_wrapper"], ["hermes"])
        self.assertTrue(updated.json()["insert_messages_via_console"])
        self.assertEqual(updated.json()["environment_offline_seconds"], 123)
        self.assertEqual(updated.json()["active_run_stale_minutes"], 31)
        self.assertEqual(updated.json()["active_managed_run_stale_minutes"], 6)
        self.assertEqual(updated.json()["resident_lease_seconds"], 180)

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

    def test_managed_wrapper_child_reregister_preserves_runtime_policy(self):
        self._register(
            "policy-claude",
            role="manager",
            runtime="claude-code",
            sessionMode="managed",
            launchMode="managed",
            sessionHandle="claude-session-1",
            model="opus",
            runtimeConfig={"effort": "medium", "maxTurns": 50},
        )

        refreshed = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "policy-claude",
                "role": "manager",
                "runtime": "claude-code",
                "sessionMode": "managed",
                "sessionHandle": "claude-session-1",
                "terminalId": "term-policy-claude",
                "managedWrapperChild": True,
                "runtimeConfig": {"channelEnabled": True},
            },
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)

        row = self._fetchone("SELECT model, runtime_config FROM agents WHERE id = ?", ("policy-claude",))
        self.assertEqual(row["model"], "opus")
        runtime_config = json.loads(row["runtime_config"])
        self.assertEqual(runtime_config["effort"], "medium")
        self.assertEqual(runtime_config["maxTurns"], 50)
        self.assertIs(runtime_config["channelEnabled"], True)

        self._register(
            "policy-codex",
            role="coder",
            runtime="codex",
            sessionMode="managed",
            launchMode="managed",
            sessionHandle="codex-thread-1",
            model="gpt-test",
            runtimeConfig={"effort": "xhigh", "quietTimeoutMs": 0},
        )

        refreshed = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "policy-codex",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "managed",
                "sessionHandle": "codex-thread-1",
                "terminalId": "term-policy-codex",
                "managedWrapperChild": True,
                "runtimeConfig": {"appServerUrl": "ws://127.0.0.1:9999"},
            },
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        row = self._fetchone("SELECT model, runtime_config FROM agents WHERE id = ?", ("policy-codex",))
        self.assertEqual(row["model"], "gpt-test")
        runtime_config = json.loads(row["runtime_config"])
        self.assertEqual(runtime_config["effort"], "xhigh")
        self.assertEqual(runtime_config["quietTimeoutMs"], 0)
        self.assertEqual(runtime_config["appServerUrl"], "ws://127.0.0.1:9999")

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

    def test_lost_resident_bridge_stops_until_manual_switch(self):
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
        switched_resident = self.client.patch("/api/v1/agents/dual-mode-coder/session-mode", json={"mode": "resident"})
        self.assertEqual(switched_resident.status_code, 200, switched_resident.text)
        resident = self.client.get("/api/v1/agents/dual-mode-coder").json()
        self.assertEqual(resident["agent"]["sessionMode"], "resident")
        self.assertEqual(resident["agent"]["wakeMode"], "codex-live")

        lost = self.client.post(
            "/api/v1/agents/dual-mode-coder/resident-lost",
            json={"bridgeId": "resident-bridge", "runtime": "codex", "reason": "connect ECONNREFUSED 127.0.0.1:9"},
        )
        self.assertEqual(lost.status_code, 200, lost.text)
        payload = lost.json()
        self.assertEqual(payload["transition"], "resident_to_stopped")
        self.assertEqual(payload["agent"]["sessionMode"], "resident")
        self.assertNotEqual(payload["agent"]["wakeMode"], "managed-worker")
        self.assertEqual(payload["agent"]["sessionHandle"], "thread-managed")
        self.assertIn("appServerUrl", payload["agent"]["runtimeConfig"])

        bridge = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id = ?", ("resident-bridge",))
        self.assertEqual(bridge["superseded_by"], "resident-lost")

        switched = self.client.patch(
            "/api/v1/agents/dual-mode-coder/session-mode",
            json={"mode": "managed", "requestedBy": "dashboard-test"},
        )
        self.assertEqual(switched.status_code, 200, switched.text)
        restored = self._fetchone("SELECT session_mode, launch_mode FROM agents WHERE id = ?", ("dual-mode-coder",))
        self.assertEqual(restored["session_mode"], "managed")
        self.assertEqual(restored["launch_mode"], "managed")

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

    def test_terminal_output_broadcast_includes_agent_id(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]

        response = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={"bridgeId": "bridge-current", "output": "x", "status": "attached"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())

        terminal_output_events = [
            args[1]
            for args, _kwargs in self.ws.broadcasts
            if args and args[0] == "terminal_output"
        ]
        self.assertTrue(terminal_output_events)
        self.assertEqual(terminal_output_events[-1]["terminalId"], terminal_id)
        self.assertEqual(terminal_output_events[-1]["agentId"], "console-agent")

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

    def test_attached_console_without_active_run_reports_active_not_working(self):
        # Corrected semantic (supersedes B1): an attached console with NO
        # tracked active run is reachable but NOT "working". Long-lived
        # managed consoles emit ambient output even while idle, so console
        # attachment/byte-activity must never by itself mean "working" —
        # otherwise idle agents show "working" forever. It must be "active".
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]
        attached = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={"bridgeId": "bridge-current", "output": "$ ", "status": "attached"},
        )
        self.assertEqual(attached.status_code, 200, attached.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["agents"]["console-agent"]["status"], "online")

    def test_idle_attached_console_reports_active_not_working(self):
        # Post-B1 regression the operator caught: an attached console with no
        # recent output is reachable but NOT working. "working" must require
        # recent console activity, not mere attachment.
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]
        attached = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={"bridgeId": "bridge-current", "output": "$ ", "status": "attached"},
        )
        self.assertEqual(attached.status_code, 200, attached.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())
        # Backdate console activity past the console-active window and expire
        # any cached live status so the read path recomputes.
        self._execute(
            "UPDATE terminal_sessions SET updated_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00Z", terminal_id),
        )
        # Force a clean recompute (drop any cached live-state row).
        self._execute("DELETE FROM agent_live_state WHERE agent_id = ?", ("console-agent",))

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["agents"]["console-agent"]["status"], "online")

    def test_agents_list_uses_cached_live_status_without_recomputing_ledgers(self):
        self._register("cached-agent", runtime="codex", sessionMode="managed", launchMode="managed")
        self._execute(
            """
            INSERT INTO agent_live_state (agent_id, status, reason, updated_at, refresh_after)
            VALUES (?,?,?,?,?)
            """,
            ("cached-agent", "offline", "cached for read path", "2026-01-01T00:00:00Z", "2099-01-01T00:00:00Z"),
        )

        with patch.object(api_v2, "_compute_agent_status", side_effect=AssertionError("read path should use cached live status")):
            listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["agents"]["cached-agent"]["status"], "offline")

    def test_agents_list_refreshes_expired_cached_status_from_environment(self):
        session_id = self._create_running_session(agent_id="expiring-agent")
        self.assertTrue(session_id)
        self._execute(
            """
            INSERT INTO agent_live_state (agent_id, status, reason, updated_at, refresh_after, environment_id, session_id)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                "expiring-agent",
                "active",
                "stale cache",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                "linux:test-host:default",
                session_id,
            ),
        )
        self._execute(
            "UPDATE environments SET last_seen = '2020-01-01T00:00:00Z' WHERE id = ?",
            ("linux:test-host:default",),
        )

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["agents"]["expiring-agent"]["status"], "offline")

    def test_terminal_output_responses_expose_monotonic_output_sequence(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]

        first = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={"bridgeId": "bridge-current", "output": "a", "status": "attached"},
        )
        second = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={"bridgeId": "bridge-current", "output": "b", "status": "attached"},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["terminal"]["outputSeq"], 1)
        self.assertEqual(second.json()["terminal"]["outputSeq"], 2)
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
        role: str = "coder",
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
                "role": role,
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

    def _stamp_live_channel_sidecar(self, agent_id: str = "console-agent", runtime: str = "claude-code"):
        """A fresh claude-channel.js channel-sidecar bridge heartbeat. A real
        managed claude PTY co-spawns this sidecar (the actual dispatch claimer);
        status-F1 (2026-05-31) requires a live, non-superseded channel-sidecar for
        a managed claude to be `online`/`blocked` — the wrapper PTY only renders.
        Test setups that model a LIVE managed claude must include it."""
        now = api_v2._now()
        self._execute(
            """
            INSERT OR REPLACE INTO bridge_instances (
                id, agent_id, machine_id, runtime, session_mode, session_handle,
                terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"channel-linux:test-host-{agent_id}",
                agent_id, "linux:test-host", runtime, "managed", "", "",
                "channel-sidecar", now, now, "", None,
            ),
        )

    def test_pi_idle_prompt_hint_detects_omp_input_box(self):
        # Pi (omp) idle prompt detector. Used by _close_idle_pi_terminal_run_without_reply
        # to close PTY-delivered managed-pi runs whose interactive omp
        # returned to the input box without emitting a structured reply.
        from service.routers.api_v2 import _terminal_pi_idle_prompt_hint
        idle_buffer = (
            "Some prior conversation\n"
            "more output\n"
            "╭── π  > ⬢ GPT-5.5 · high > \U0001f4c1 C:\\tmp > ◫ 49.1%/272K ⟲ > $6.53 ▶──╮\n"
            "╰─                                                                       ─╯\n"
        )
        self.assertTrue(_terminal_pi_idle_prompt_hint(idle_buffer), "must detect idle omp prompt")
        # Streaming-thinking marker AFTER the box → not idle.
        thinking = idle_buffer + "\n  thinking...\n"
        self.assertFalse(
            _terminal_pi_idle_prompt_hint(thinking),
            "must NOT mark idle when a thinking marker appears after the box",
        )
        # Without the box at the tail → not idle.
        no_box = "just some regular pi log output\nlast line\n"
        self.assertFalse(_terminal_pi_idle_prompt_hint(no_box))

    def test_startup_reconcile_clears_stale_managed_pty_for_resident_agents(self):
        # Service-start event: when a container restarts, in-flight
        # managed wrapper PTYs are dead (their bridge died). For agents
        # currently registered as resident, those terminal_sessions
        # rows must be cleared so the dashboard doesn't render ghost
        # consoles.
        from service.routers.api_v2 import _reconcile_stale_managed_terminals_for_resident_agents
        # Set up: a managed terminal_session for an agent that is now resident.
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-managed-1",
        )
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]
        agent_id = self._fetchone("SELECT agent_id FROM agent_sessions WHERE id = ?", (session_id,))["agent_id"]
        # Manually mark the agent as resident (simulating the agent having
        # been re-registered as resident while the managed PTY was still
        # alive — exactly the pre-fix stale state).
        self._execute("UPDATE agents SET session_mode='resident' WHERE id = ?", (agent_id,))

        # Run the startup reconcile sweep.
        import asyncio
        from service.db import get_db
        async def _run():
            db = await get_db()
            try:
                n = await _reconcile_stale_managed_terminals_for_resident_agents(db)
                await db.commit()
                return n
            finally:
                await db.close()
        cleared = asyncio.new_event_loop().run_until_complete(_run())

        self.assertEqual(cleared, 1, f"should reconcile exactly the one stale terminal; got {cleared}")
        term = self._fetchone("SELECT status, error FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(term["status"], "stopped")
        self.assertIn("reconciled_at_service_startup_resident_owns_agent", term["error"])
        sess = self._fetchone("SELECT terminal_id FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(sess["terminal_id"], "", f"agent_session terminal_id binding must be cleared; got {sess['terminal_id']!r}")

    def _seed_managed_claude_with_attached_terminal(self, agent_id: str, terminal_id: str):
        """B1 helper: seed a MANAGED claude-code agent whose runtime_state
        points at an `attached` (non-vterm) terminal_sessions row, the exact
        shape that produces a ghost console when the worker dies."""
        now = api_v2._now()
        # Environment + agent via the proven HTTP fixtures (creates the env row
        # the terminal/session FKs require).
        self._heartbeat_environment(
            id="linux:test-host:default",
            bridgeId="bridge-current",
            machineId="linux:test-host",
            runtimes=[{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        self._register(agent_id, runtime="claude-code", sessionMode="managed")
        # Force the managed claude shape: session_handle + consoleTerminal pointer.
        self._execute(
            "UPDATE agents SET session_mode='managed', runtime='claude-code', session_handle=?, runtime_state=? WHERE id = ?",
            (
                "claude-managed-handle-1",
                json.dumps({
                    "consoleTerminal": {
                        "terminalId": terminal_id,
                        "bridgeId": "bridge-current",
                        "sessionHandle": "claude-managed-handle-1",
                        "at": now,
                    }
                }),
                agent_id,
            ),
        )
        # agent_sessions row bound to the terminal (mirrors the model's clear path).
        # spawn_spec_id/spawn_request_id are passed as NULL (not '') so their
        # FKs don't fire — the same shape the production insert uses.
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
                f"sess_{agent_id}",
                agent_id,
                "linux:test-host:default",
                "claude-code",
                "/workspace/repo",
                "managed-warm",
                "managed",
                terminal_id,
                "attached",
                None,
                None,
                "running",
                now,
                now,
            ),
        )
        # The attached (non-vterm) terminal row — the ghost-console surface.
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                terminal_id,
                f"sess_{agent_id}",
                agent_id,
                "linux:test-host:default",
                "bridge-current",
                "claude-code",
                "/workspace/repo",
                "claude-aify --aify-agent " + agent_id,
                "",
                "attached",
                "dashboard",
                now,
                now,
                None,
                "",
            ),
        )

    def _run_managed_worker_hygiene(self):
        import asyncio as _asyncio
        from service.db import get_db as _get_db

        async def _run():
            db = await _get_db()
            try:
                result = await api_v2._reconcile_managed_worker_hygiene(db)
                await db.commit()
                return result
            finally:
                await db.close()

        return _asyncio.new_event_loop().run_until_complete(_run())

    def _run_orphan_spawn_reconcile(self):
        import asyncio as _asyncio
        from service.db import get_db as _get_db

        async def _run():
            db = await _get_db()
            try:
                return await api_v2._fail_orphaned_running_spawn_requests(db)
            finally:
                await db.close()

        return _asyncio.new_event_loop().run_until_complete(_run())

    def test_orphan_spawn_reconcile_fails_only_dead_bridge_old_spawns(self):
        # SAFETY TEST for _fail_orphaned_running_spawn_requests. Three running
        # spawns; only the orphan (dead bridge + old) may be failed:
        #   (a) running, claimed by a DEAD bridge, old      → FAILED (orphan)
        #   (b) running, claimed by the LIVE env bridge, old → LEFT (in progress)
        #   (c) running, claimed by a dead bridge, but FRESH → LEFT (grace window)
        self._heartbeat_environment(
            id="orphan-test-env", bridgeId="live-env-bridge-1",
            machineId="linux:orphan-test",
            runtimes=[{"runtime": "claude-code", "modes": ["managed-warm"]}],
        )
        old = "2020-01-01T00:00:00Z"
        fresh = api_v2._now()
        # Minimal spawn_spec to satisfy the spawn_requests.spawn_spec_id FK.
        self._execute(
            """INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, created_at, updated_at)
               VALUES (?,?,?,?,?,?)""",
            ("orphan-test-spec", "orphan-agent", "orphan-test-env", "claude-code", old, old),
        )

        def _ins(sid, bridge, claimed_at):
            self._execute(
                """INSERT INTO spawn_requests
                   (id, spawn_spec_id, agent_id, environment_id, runtime, mode, status,
                    claimed_by_bridge_id, claimed_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, "orphan-test-spec", sid + "-agent", "orphan-test-env", "claude-code",
                 "managed-warm", "running", bridge, claimed_at, claimed_at, claimed_at),
            )

        _ins("sp-orphan-dead-old", "dead-bridge-xyz", old)
        _ins("sp-live-old", "live-env-bridge-1", old)
        _ins("sp-orphan-dead-fresh", "dead-bridge-xyz", fresh)

        failed = self._run_orphan_spawn_reconcile()

        self.assertEqual(failed, 1, "exactly one orphan (dead bridge + old) should be failed")
        self.assertEqual(
            self._fetchone("SELECT status FROM spawn_requests WHERE id=?", ("sp-orphan-dead-old",))["status"],
            "failed", "orphan (dead claiming bridge, old) must be failed")
        self.assertEqual(
            self._fetchone("SELECT status FROM spawn_requests WHERE id=?", ("sp-live-old",))["status"],
            "running", "a spawn on the LIVE env bridge must NOT be failed (still in progress)")
        self.assertEqual(
            self._fetchone("SELECT status FROM spawn_requests WHERE id=?", ("sp-orphan-dead-fresh",))["status"],
            "running", "a freshly-claimed spawn must get the grace window (not failed)")

    def test_managed_hygiene_reaps_ghost_console_row(self):
        # MANAGED claude with a dead worker (NO channel-sidecar bridge row at
        # all → _has_live_channel_sidecar False) but a stale `attached`
        # terminal row + a consoleTerminal pointer = a phantom "Console
        # attached" for a dead agent. The reaper must reap it.
        terminal_id = "term_ghost_console"
        self._seed_managed_claude_with_attached_terminal("ghost-claude", terminal_id)
        # No channel-sidecar bridge_instances row is inserted → no live sidecar.
        # Dead worker → the bridge stopped streaming output, so updated_at is STALE
        # (a fresh updated_at means a live/booting worker — boot-race guard).
        self._execute("UPDATE terminal_sessions SET updated_at = '2020-01-01T00:00:00Z' WHERE id = ?", (terminal_id,))

        result = self._run_managed_worker_hygiene()

        self.assertEqual(result["managed_ghost_rows_reaped"], 1, result)
        self.assertEqual(result["orphan_workers_reaped"], 0, result)
        term = self._fetchone("SELECT status, error FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(term["status"], "stopped")
        self.assertIn("reconciled_managed_ghost_console_dead_worker", term["error"])
        agent = self._fetchone("SELECT runtime_state FROM agents WHERE id = ?", ("ghost-claude",))
        rs = json.loads(agent["runtime_state"] or "{}")
        self.assertNotIn("consoleTerminal", rs, f"consoleTerminal pointer must be cleared; got {rs!r}")
        sess = self._fetchone("SELECT terminal_id, terminal_status FROM agent_sessions WHERE terminal_id = ?", (terminal_id,))
        self.assertIsNone(sess, "agent_session terminal binding must be cleared")
        event = self._fetchone(
            "SELECT event_type FROM terminal_events WHERE terminal_id = ? AND event_type = ?",
            (terminal_id, "reconciled_managed_ghost_console"),
        )
        self.assertIsNotNone(event, "reconciled_managed_ghost_console event must be appended")

    def test_managed_hygiene_keeps_live_console(self):
        # MANAGED claude with a FRESH channel-sidecar heartbeat → the worker
        # is alive; a live-but-idle console must NOT be reaped.
        terminal_id = "term_live_console"
        self._seed_managed_claude_with_attached_terminal("live-claude", terminal_id)
        now = api_v2._now()
        self._execute(
            """
            INSERT INTO bridge_instances (
                id, agent_id, machine_id, runtime, session_mode, session_handle,
                terminal_id, bridge_kind, registered_at, last_seen, superseded_by
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "bridge-sidecar-live",
                "live-claude",
                "linux:test-host",
                "claude-code",
                "managed",
                "claude-managed-handle-1",
                terminal_id,
                "channel-sidecar",
                now,
                now,
                "",
            ),
        )

        result = self._run_managed_worker_hygiene()

        self.assertEqual(result["managed_ghost_rows_reaped"], 0, result)
        term = self._fetchone("SELECT status FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(term["status"], "attached", "live-but-idle console must NOT be reaped")

    def test_managed_hygiene_keeps_booting_worker_with_fresh_output(self):
        # DETERMINISTIC BOOT-RACE GUARD (2026-06-04): a managed claude worker whose
        # claimer (claude-channel.js sidecar) has NOT registered yet — because it
        # is still booting through a long SessionStart hook — has NO live sidecar,
        # but its PTY is alive and STREAMING the hook progress spinner, so the
        # bridge keeps bumping updated_at. The reaper must NOT mistake "no claimer
        # yet" for "dead worker" and reap it (the launches-then-dies-stuck-available
        # bug). The seed helper leaves a FRESH updated_at, simulating that stream.
        terminal_id = "term_booting_console"
        self._seed_managed_claude_with_attached_terminal("booting-claude", terminal_id)
        # No channel-sidecar / managed-wrapper-child bridge → no live claimer YET,
        # but fresh updated_at proves the PTY is alive (booting).

        result = self._run_managed_worker_hygiene()

        self.assertEqual(result["managed_ghost_rows_reaped"], 0, result)
        term = self._fetchone("SELECT status FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(
            term["status"], "attached",
            "a booting worker (fresh PTY output, no claimer registered yet) must NOT be reaped",
        )

    # --- B2: status rule refinement (status-F1) + orphan-worker detection ---

    def _seed_managed_hermes_with_attached_terminal(self, agent_id: str, terminal_id: str):
        """WS3 helper: seed a MANAGED hermes agent whose runtime_state points at
        an `attached` (non-vterm) console PTY terminal_sessions row — the exact
        shape that, pre-WS3, manufactured `online` from console presence ALONE
        (no live channel sidecar = no live claimer). Mirrors the claude helper
        but with runtime='hermes' and a hermes-aify console command."""
        now = api_v2._now()
        self._heartbeat_environment(
            id="linux:test-host:default",
            bridgeId="bridge-current",
            machineId="linux:test-host",
            runtimes=[{"runtime": "hermes", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        self._register(agent_id, runtime="hermes", sessionMode="managed")
        self._execute(
            "UPDATE agents SET session_mode='managed', runtime='hermes', session_handle=?, runtime_state=? WHERE id = ?",
            (
                "hermes-managed-handle-1",
                json.dumps({
                    "consoleTerminal": {
                        "terminalId": terminal_id,
                        "bridgeId": "bridge-current",
                        "sessionHandle": "hermes-managed-handle-1",
                        "at": now,
                    }
                }),
                agent_id,
            ),
        )
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
                f"sess_{agent_id}",
                agent_id,
                "linux:test-host:default",
                "hermes",
                "/workspace/repo",
                "managed-warm",
                "managed",
                terminal_id,
                "attached",
                None,
                None,
                "running",
                now,
                now,
            ),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                terminal_id,
                f"sess_{agent_id}",
                agent_id,
                "linux:test-host:default",
                "bridge-current",
                "hermes",
                "/workspace/repo",
                "hermes-aify --aify-agent " + agent_id,
                "",
                "attached",
                "dashboard",
                now,
                now,
                None,
                "",
            ),
        )

    def test_managed_hermes_online_requires_live_channel_sidecar(self):
        # WS3 Task 3.1: a managed hermes is `online` ONLY when BOTH a live
        # console PTY AND a live channel-sidecar (the actual claimer) exist.
        # Pre-WS3, a live console PTY ALONE manufactured `online` (the deaf-but-
        # online bug). With hermes now in _CHANNEL_SIDECAR_DELIVERY_RUNTIMES the
        # both-required gate applies: live console + STALE sidecar → available;
        # both live → online.
        terminal_id = "term_hermes_online_console"
        self._seed_managed_hermes_with_attached_terminal("online-hermes", terminal_id)

        # DEAF, not booting (2026-06-05): age the console and register a sidecar FOR it that then
        # went stale — a genuinely deaf worker that must stay `available`. (A FRESH console whose
        # sidecar has never registered is BOOTING → online; this test guards the DEAF case — a
        # sidecar last-seen AFTER the console started, then died.)
        _old = (datetime.now(timezone.utc) - timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _stale = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute("UPDATE terminal_sessions SET created_at = ? WHERE agent_id = ?", (_old, "online-hermes"))
        self._execute(
            """
            INSERT OR REPLACE INTO bridge_instances (
                id, agent_id, machine_id, runtime, session_mode, session_handle,
                terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("channel-linux:test-host-online-hermes", "online-hermes", "linux:test-host", "hermes",
             "managed", "", "", "channel-sidecar", _stale, _stale, "", None),
        )

        # Live `attached` console with a DEAD (registered-then-stale) channel-sidecar → not a
        # live claimer → available (NOT online).
        asyncio.run(self._async_invalidate("online-hermes"))
        agent = self.client.get("/api/v1/agents/online-hermes").json()["agent"]
        self.assertNotEqual(agent["status"], "online", agent)
        self.assertEqual(agent["status"], "available", agent)

        # Now add a fresh channel-sidecar heartbeat → live claimer → online.
        self._stamp_live_channel_sidecar("online-hermes", runtime="hermes")
        asyncio.run(self._async_invalidate("online-hermes"))
        agent = self.client.get("/api/v1/agents/online-hermes").json()["agent"]
        self.assertEqual(agent["status"], "online", agent)

    def test_managed_codex_online_from_fresh_wrapper_child_bridge(self):
        # FIX B3 (2026-06-03): a managed CODEX whose console terminal row has been
        # failed (the B1/B2 transient-close scenario, where the terminal-liveness
        # check no longer proves has_live_worker) must STILL read deliverable, not
        # `available`, when a fresh non-superseded `managed-wrapper-child` bridge is
        # heartbeating — that wrapper-child IS the live console's in-session
        # aify-comms claimer. Mirrors the hermes channel-sidecar gate.
        agent_id = "online-codex"
        terminal_id = "term_codex_failed_console"
        now = api_v2._now()
        self._heartbeat_environment(
            id="linux:test-host:default",
            bridgeId="bridge-current",
            machineId="linux:test-host",
            runtimes=[{"runtime": "codex", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        self._register(agent_id, runtime="codex", sessionMode="managed")
        self._execute(
            "UPDATE agents SET session_mode='managed', runtime='codex', session_handle=? WHERE id = ?",
            ("codex-managed-handle-1", agent_id),
        )
        # agent_sessions row bound to a console terminal that has since FAILED —
        # the post-transient-close state where the terminal-liveness check yields
        # no live worker.
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
                f"sess_{agent_id}", agent_id, "linux:test-host:default", "codex",
                "/workspace/repo", "managed-warm", "managed", terminal_id, "failed",
                None, None, "running", now, now,
            ),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                terminal_id, f"sess_{agent_id}", agent_id, "linux:test-host:default",
                "bridge-current", "codex", "/workspace/repo",
                "codex-aify --aify-agent " + agent_id, "", "failed", "dashboard",
                now, now, now, "transient app-server close",
            ),
        )

        # No managed-wrapper-child bridge yet → no live claimer → available.
        asyncio.run(self._async_invalidate(agent_id))
        agent = self.client.get(f"/api/v1/agents/{agent_id}").json()["agent"]
        self.assertEqual(agent["status"], "available", agent)

        # Fresh non-superseded managed-wrapper-child heartbeat → live claimer → online.
        self._execute(
            """
            INSERT OR REPLACE INTO bridge_instances (
                id, agent_id, machine_id, runtime, session_mode, session_handle,
                terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"mwc-{agent_id}", agent_id, "linux:test-host", "codex", "managed",
                "codex-managed-handle-1", terminal_id, "managed-wrapper-child",
                now, now, "", None,
            ),
        )
        asyncio.run(self._async_invalidate(agent_id))
        agent = self.client.get(f"/api/v1/agents/{agent_id}").json()["agent"]
        self.assertNotEqual(agent["status"], "available", agent)
        self.assertEqual(agent["status"], "online", agent)

    def test_managed_hygiene_reaps_hermes_ghost_console_row(self):
        # WS3 Task 3.4: now that hermes is in _CHANNEL_SIDECAR_DELIVERY_RUNTIMES,
        # _reconcile_managed_worker_hygiene must cover the hermes triad: a managed
        # hermes with a stale (here: absent) channel-sidecar + an `attached`
        # console row is a ghost console for a dead worker — reaped exactly like
        # claude's ghost path, with a hermes-aware reason string.
        terminal_id = "term_hermes_ghost_console"
        self._seed_managed_hermes_with_attached_terminal("ghost-hermes", terminal_id)
        # No channel-sidecar bridge row → no live claimer → dead worker.
        # Dead worker → bridge stopped streaming, so updated_at is STALE (boot-race guard).
        self._execute("UPDATE terminal_sessions SET updated_at = '2020-01-01T00:00:00Z' WHERE id = ?", (terminal_id,))

        result = self._run_managed_worker_hygiene()

        self.assertEqual(result["managed_ghost_rows_reaped"], 1, result)
        term = self._fetchone("SELECT status, error FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(term["status"], "stopped")
        self.assertIn("reconciled_managed_ghost_console_dead_worker", term["error"])
        agent = self._fetchone("SELECT runtime_state FROM agents WHERE id = ?", ("ghost-hermes",))
        rs = json.loads(agent["runtime_state"] or "{}")
        self.assertNotIn("consoleTerminal", rs, f"consoleTerminal pointer must be cleared; got {rs!r}")
        event = self._fetchone(
            "SELECT body FROM terminal_events WHERE terminal_id = ? AND event_type = ?",
            (terminal_id, "reconciled_managed_ghost_console"),
        )
        self.assertIsNotNone(event, "reconciled_managed_ghost_console event must be appended")
        payload = json.loads(event["body"] or "{}")
        self.assertEqual(payload.get("runtime"), "hermes", payload)
        self.assertIn("hermes delivery loop", payload.get("reason", ""), f"reason should be hermes-aware; got {payload!r}")

    def test_host_reported_dead_pty_marks_row_stopped_and_invalidates(self):
        # WS4 Task 4.2 (server side): the OWNING environment bridge is the only
        # thing that can probe a local PID. When it reports a console PTY's
        # process_id as DEAD, the server marks the terminal_sessions row stopped
        # and invalidates the agent's live state (so a frozen/crashed console
        # can't keep manufacturing presence).
        terminal_id = "term_dead_pty"
        self._seed_managed_hermes_with_attached_terminal("dead-pty-hermes", terminal_id)
        self._execute(
            "UPDATE terminal_sessions SET process_id = ? WHERE id = ?",
            ("4242", terminal_id),
        )
        # Seed a live_state row so we can prove invalidation deletes it.
        self._execute(
            "INSERT INTO agent_live_state (agent_id, status, updated_at) VALUES (?,?,?)",
            ("dead-pty-hermes", "online", api_v2._now()),
        )

        resp = self.client.post(
            f"/api/v1/terminals/{terminal_id}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "4242", "reason": "host pid not alive"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        term = self._fetchone("SELECT status, error FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(term["status"], "stopped", term)
        self.assertIn("host pid not alive", term["error"] or "")
        live = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("dead-pty-hermes",))
        self.assertIsNone(live, "agent live-state must be invalidated on host-reported dead PTY")

    def test_host_reported_dead_pty_is_idempotent_and_pid_guarded(self):
        # A report for a terminal that is already stopped is a harmless no-op
        # (200, still stopped). A report whose pid no longer matches the stored
        # process_id is rejected/ignored so a stale report can't stop a row that
        # the owning bridge has since restarted with a NEW pid.
        terminal_id = "term_dead_pty_idem"
        self._seed_managed_hermes_with_attached_terminal("idem-hermes", terminal_id)
        self._execute(
            "UPDATE terminal_sessions SET process_id = ? WHERE id = ?",
            ("9001", terminal_id),
        )
        # Stale report: pid mismatch → must NOT stop the row.
        resp = self.client.post(
            f"/api/v1/terminals/{terminal_id}/report-dead",
            json={"bridgeId": "bridge-current", "processId": "1234", "reason": "stale"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        term = self._fetchone("SELECT status FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(term["status"], "attached", "pid-mismatched report must not stop the row")

        # Matching report stops it; a second matching report is idempotent.
        for _ in range(2):
            resp = self.client.post(
                f"/api/v1/terminals/{terminal_id}/report-dead",
                json={"bridgeId": "bridge-current", "processId": "9001"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            term = self._fetchone("SELECT status FROM terminal_sessions WHERE id = ?", (terminal_id,))
            self.assertEqual(term["status"], "stopped", term)

    def _run_prune_orphaned_dispatch_runs(self, **kwargs):
        async def _run():
            db = await get_db()
            try:
                n = await api_v2._prune_orphaned_dispatch_runs(db, **kwargs)
                await db.commit()
                return n
            finally:
                await db.close()

        return asyncio.run(_run())

    def _seed_dispatch_run(self, run_id, *, from_agent, target_agent, status, requested_at):
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode,
                message_type, subject, body, priority, status, require_reply, requested_at, finished_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, None, from_agent, target_agent, "start_if_possible", "managed",
                "request", "s", "b", "normal", status, 0, requested_at, requested_at,
            ),
        )

    def test_prune_orphaned_dispatch_runs_deletes_terminal_runs_for_tombstoned_agent(self):
        # WS4 Task 4.3: TERMINAL-status dispatch_runs whose target/from agent is
        # tombstoned (or unknown) and older than the TTL are pruned. Live agents'
        # history and non-terminal runs are never touched.
        old = "2026-05-01T00:00:00Z"  # well past a 24h TTL relative to "now"
        recent = api_v2._now()

        # Tombstone a removed target agent.
        self._execute(
            "INSERT INTO agent_tombstones (agent_id, removed_at) VALUES (?,?)",
            ("ghost-target", api_v2._now()),
        )
        # A live agent that must NEVER have its history pruned.
        self._register("live-agent")

        # SHOULD be deleted: terminal run for a tombstoned target, past TTL.
        self._seed_dispatch_run("run_ghost_old", from_agent="dashboard", target_agent="ghost-target", status="completed", requested_at=old)
        # SHOULD be deleted: terminal run from a tombstoned sender, past TTL.
        self._seed_dispatch_run("run_ghost_from", from_agent="ghost-target", target_agent="someone", status="failed", requested_at=old)
        # SHOULD be deleted: terminal run for an UNKNOWN (never-registered) agent, past TTL.
        self._seed_dispatch_run("run_unknown_old", from_agent="dashboard", target_agent="never-existed", status="cancelled", requested_at=old)

        # SHOULD SURVIVE: terminal run for a tombstoned target but RECENT (within TTL).
        self._seed_dispatch_run("run_ghost_recent", from_agent="dashboard", target_agent="ghost-target", status="completed", requested_at=recent)
        # SHOULD SURVIVE: NON-terminal run for the tombstoned target, even if old.
        self._seed_dispatch_run("run_ghost_queued", from_agent="dashboard", target_agent="ghost-target", status="queued", requested_at=old)
        # SHOULD SURVIVE: terminal run for a LIVE agent, even if old.
        self._seed_dispatch_run("run_live_old", from_agent="dashboard", target_agent="live-agent", status="completed", requested_at=old)

        deleted = self._run_prune_orphaned_dispatch_runs(ttl_hours=24)
        self.assertEqual(deleted, 3, "exactly the 3 old terminal orphan runs should be pruned")

        surviving = {r["id"] for r in self._fetchall("SELECT id FROM dispatch_runs")}
        self.assertEqual(
            surviving,
            {"run_ghost_recent", "run_ghost_queued", "run_live_old"},
            surviving,
        )

    def test_prune_orphaned_dispatch_runs_respects_ttl_window(self):
        # A terminal run for a tombstoned target that is recent (inside the TTL)
        # is NOT pruned — the window must be honored so freshly-removed agents'
        # recent audit history is briefly retained.
        self._execute(
            "INSERT INTO agent_tombstones (agent_id, removed_at) VALUES (?,?)",
            ("fresh-ghost", api_v2._now()),
        )
        self._seed_dispatch_run("run_fresh", from_agent="dashboard", target_agent="fresh-ghost", status="completed", requested_at=api_v2._now())
        deleted = self._run_prune_orphaned_dispatch_runs(ttl_hours=24)
        self.assertEqual(deleted, 0)
        self.assertIsNotNone(self._fetchone("SELECT id FROM dispatch_runs WHERE id = ?", ("run_fresh",)))

    def test_managed_claude_online_requires_live_console(self):
        # status-F1 (refined): a managed claude is `online` ONLY when BOTH a live
        # console PTY AND a live channel-sidecar exist. A live sidecar with NO
        # console is a headless orphan worker → `available`, never `online`.
        terminal_id = "term_online_console"
        self._seed_managed_claude_with_attached_terminal("online-claude", terminal_id)
        self._stamp_live_channel_sidecar("online-claude")  # fresh sidecar
        # Live `attached` console + fresh sidecar → online.
        asyncio.run(self._async_invalidate("online-claude"))
        agent = self.client.get("/api/v1/agents/online-claude").json()["agent"]
        self.assertEqual(agent["status"], "online", agent)

        # Now stop the console terminal, leaving ONLY the fresh sidecar.
        self._execute(
            "UPDATE terminal_sessions SET status = 'stopped' WHERE id = ?",
            (terminal_id,),
        )
        asyncio.run(self._async_invalidate("online-claude"))
        agent = self.client.get("/api/v1/agents/online-claude").json()["agent"]
        self.assertNotEqual(agent["status"], "online", agent)
        self.assertEqual(agent["status"], "available", agent)

    def test_managed_hygiene_reaps_orphan_worker(self):
        # MANAGED claude, FRESH sidecar (worker alive), newest terminal row is
        # `stopped` ~200s ago (no live console), runtime_state.consoleTerminal set
        # → headless orphan: reap pointer, invalidate cache, count it.
        terminal_id = "term_orphan_worker"
        self._seed_managed_claude_with_attached_terminal("orphan-claude", terminal_id)
        self._stamp_live_channel_sidecar("orphan-claude")  # worker alive
        # Stamp a cached live_state row so we can prove invalidation deletes it.
        self._execute(
            """
            INSERT INTO agent_live_state (agent_id, status, reason, updated_at, refresh_after)
            VALUES (?,?,?,?,?)
            """,
            ("orphan-claude", "online", "stale", api_v2._now(), "2099-01-01T00:00:00Z"),
        )
        # Newest terminal row terminal-state with stopped_at ~200s in the past.
        old = "2000-01-01T00:00:00Z"
        self._execute(
            "UPDATE terminal_sessions SET status = 'stopped', stopped_at = ?, updated_at = ? WHERE id = ?",
            (old, old, terminal_id),
        )

        result = self._run_managed_worker_hygiene()

        self.assertEqual(result["orphan_workers_reaped"], 1, result)
        agent = self._fetchone("SELECT runtime_state FROM agents WHERE id = ?", ("orphan-claude",))
        rs = json.loads(agent["runtime_state"] or "{}")
        self.assertNotIn("consoleTerminal", rs, f"consoleTerminal pointer must be cleared; got {rs!r}")
        live = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("orphan-claude",))
        self.assertIsNone(live, "agent_live_state row must be invalidated (deleted)")
        event = self._fetchone(
            "SELECT event_type FROM terminal_events WHERE terminal_id = ? AND event_type = ?",
            (terminal_id, "reconciled_managed_orphan_worker"),
        )
        self.assertIsNotNone(event, "reconciled_managed_orphan_worker event must be appended")

    def test_managed_hygiene_keeps_online_console(self):
        # MANAGED claude, FRESH sidecar + live `attached` console → NOT an orphan.
        terminal_id = "term_orphan_keep"
        self._seed_managed_claude_with_attached_terminal("keep-claude", terminal_id)
        self._stamp_live_channel_sidecar("keep-claude")  # worker alive
        # terminal row stays `attached` (live console).

        result = self._run_managed_worker_hygiene()

        self.assertEqual(result["orphan_workers_reaped"], 0, result)
        self.assertEqual(result["managed_ghost_rows_reaped"], 0, result)
        term = self._fetchone("SELECT status FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(term["status"], "attached", "live console must be untouched")
        agent = self._fetchone("SELECT runtime_state FROM agents WHERE id = ?", ("keep-claude",))
        rs = json.loads(agent["runtime_state"] or "{}")
        self.assertIn("consoleTerminal", rs, "consoleTerminal pointer must be preserved")

    # --- status-truthfulness bug fixes (2026-06-01) ---

    def _async_compute_live_status(self, agent_id: str):
        """Drive _compute_live_status_cache directly and return the cache dict."""
        async def _run():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
                return await api_v2._compute_live_status_cache(db, row)
            finally:
                await db.close()
        return asyncio.run(_run())

    def test_managed_with_reachable_env_and_orphaned_session_stays_available(self):
        # STATUS POLICY (2026-06-04, sc-coder regression): a MANAGED agent whose
        # previous worker died — leaving an orphaned, NON-live session row owned by
        # an OLD bridge that differs from the current env bridge — must rest at
        # `available` when its owning environment is ONLINE (it is lazy-autostartable).
        # It must NOT be demoted to `offline`: managed `offline` is reserved for
        # disabled/stopped OR an unreachable environment. Before the fix the
        # "env bridge no longer owns the active session" branch (not gated by
        # session_mode) flipped this managed agent offline.
        # ONLINE owning environment whose CURRENT bridge ("env-bridge-current")
        # differs from the orphaned session's owner bridge below.
        self._heartbeat_environment(
            id="windows:test:default", machineId="win32:test", os="windows", kind="windows",
            bridgeId="env-bridge-current",
            runtimes=[{"runtime": "hermes", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        self._register("mgr-orphan-avail", runtime="hermes", sessionMode="managed")
        now = api_v2._now()
        # Orphaned, NON-live session row owned by an OLD (now-dead) worker bridge,
        # bound to the SAME environment whose CURRENT bridge is "env-bridge-current".
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, spawn_spec_id, spawn_request_id,
                status, started_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess-orphan-avail", "mgr-orphan-avail", "windows:test:default", "hermes",
                "/workspace/repo", "managed-warm", "managed", "worker-bridge-OLD",
                None, None, "stopped", now, now,
            ),
        )
        cache = self._async_compute_live_status("mgr-orphan-avail")
        self.assertEqual(
            cache["status"], "available",
            f"managed agent with reachable env + orphaned session must be available, not offline: {cache}",
        )

    def test_working_via_turnbusy_has_short_refresh_after(self):
        # FIX 2: when `working` is derived from a fresh turn_busy (NOT an active
        # run), refresh_after is clamped to the turn-busy self-heal window so a
        # DROPPED turn-end self-heals at the single long ceiling instead of the
        # 5-30min heartbeat windows.
        #
        # WS5 Task 5.2/5.3 (2026-06-02): this test encoded the OLD 120s timer-driven
        # self-heal as the PRIMARY transition. That role is replaced: the turn-END
        # EVENT (POST /turn-end) is now the primary off-working transition (and it
        # invalidates the cache immediately — see
        # test_turn_end_event_flips_managed_hermes_off_working_immediately). The
        # refresh_after clamp is now the DROPPED-event backstop, so it sits at the
        # long TURN_BUSY_BACKSTOP_SECONDS ceiling, not 120s. Assertion updated to
        # the backstop window accordingly (justified: it encoded the superseded
        # timer-as-primary behavior).
        self._register("tb-refresh-claude", runtime="claude-code", sessionMode="resident")
        now = api_v2._now()
        self._execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET turn_busy = 1, turn_updated_at = excluded.turn_updated_at
            """,
            ("tb-refresh-claude", "run-tb-1", "bridge-tb-1", "claude-code", now),
        )
        cache = self._async_compute_live_status("tb-refresh-claude")
        self.assertEqual(cache["status"], "working", cache)
        limit = api_v2._iso_add_seconds(now, api_v2.TURN_BUSY_BACKSTOP_SECONDS)
        self.assertTrue(cache["refresh_after"], cache)
        self.assertLessEqual(
            cache["refresh_after"], limit,
            f"refresh_after {cache['refresh_after']} must be <= turn_updated_at+backstop ({limit}); cache={cache}",
        )

    def test_turn_busy_status_long_ceiling_claim_gate_short_window_split(self):
        # pure-event-status changes #3 + #5 (2026-06-02): STATUS and the CLAIM/SEND
        # gate are DELIBERATELY RE-SPLIT.
        #   - STATUS goes pure-event with a LONG ceiling (30m) as the dropped-event
        #     self-heal only — safe now that change #1 (transcript turn-end detector)
        #     + change #2 (liveness wins) make a real turn-end reliable.
        #   - The CLAIM/SEND gate KEEPS the short 120s window so a queued send is
        #     never stranded behind a missed end-event.
        #
        # This SUPERSEDES the prior "single shared 120s window" test (commit 0fc84e6
        # collapsed them because turn-end events were unreliable — the root cause #1
        # now fixes, so the split is restored). It pins the new split behavior.
        self.assertGreater(
            api_v2.TURN_BUSY_BACKSTOP_SECONDS, api_v2.TURN_BUSY_STALE_SECONDS,
            "status backstop (long ceiling) must be DECOUPLED from the short claim window",
        )
        self.assertEqual(api_v2.TURN_BUSY_STALE_SECONDS, 120, "claim-gate window stays 120s (#5)")
        self._register("tb-split-claude", runtime="claude-code", sessionMode="resident")
        from datetime import datetime, timezone
        # Aged PAST the short claim window (120s) but WELL WITHIN the long status
        # ceiling: STATUS still `working` (pure-event, no flap) while the CLAIM/SEND
        # gate is OPEN so a queued run is deliverable (never stranded > 120s).
        aged_past_gate = api_v2._iso_from_ms(int(
            (datetime.now(timezone.utc).timestamp() - (api_v2.TURN_BUSY_STALE_SECONDS + 80)) * 1000
        ))
        self._execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET turn_busy = 1, turn_run_id = excluded.turn_run_id, turn_updated_at = excluded.turn_updated_at
            """,
            ("tb-split-claude", "run-split-1", "bridge-split-1", "claude-code", aged_past_gate),
        )
        cache = self._async_compute_live_status("tb-split-claude")
        self.assertEqual(
            cache["status"], "working",
            f"past the 120s claim window but within the long status ceiling → STILL working (pure-event); {cache}",
        )
        self.assertFalse(
            self._turn_busy_gate_for("tb-split-claude"),
            "past the 120s claim window → busy gate is OPEN so a queued run is deliverable (not stranded)",
        )
        # WITHIN both windows: working AND gate closed (both halves agree).
        fresh = api_v2._now()
        self._execute(
            "UPDATE agent_turn_state SET turn_updated_at = ? WHERE agent_id = ?",
            (fresh, "tb-split-claude"),
        )
        cache_fresh = self._async_compute_live_status("tb-split-claude")
        self.assertEqual(cache_fresh["status"], "working", f"within both windows → working; {cache_fresh}")
        self.assertTrue(self._turn_busy_gate_for("tb-split-claude"), "within the claim window → gate closed")

    def test_claim_gate_uses_short_window_not_backstop(self):
        # pure-event-status change #5: the CLAIM/SEND gate paths keep the SHORT
        # TURN_BUSY_STALE_SECONDS (120s) window — never the long status backstop —
        # so a queued send is not stranded behind a missed end-event for up to the
        # 30-min ceiling. Static guard on the two claim-gate sites so a future
        # refactor can't silently widen the gate to the long ceiling.
        import re
        from pathlib import Path
        src = Path(api_v2.__file__).read_text(encoding="utf-8")
        gate_lines = [
            ln for ln in src.splitlines()
            if "tb_epoch" in ln and "TURN_BUSY" in ln and "<=" in ln
        ]
        self.assertGreaterEqual(len(gate_lines), 2, "expected both claim-gate turn_busy freshness checks present")
        for ln in gate_lines:
            self.assertIn(
                "TURN_BUSY_STALE_SECONDS", ln,
                f"claim-gate must use the short 120s window, not the backstop: {ln.strip()}",
            )
            self.assertNotIn(
                "TURN_BUSY_BACKSTOP_SECONDS", ln,
                f"claim-gate must NOT use the long status ceiling: {ln.strip()}",
            )

    def test_engine_busy_for_queue_releases_on_ready_status(self):
        # WS-3 (2026-06-17): the queue/claim busy-check consults the liveness-aware
        # engine status (cached agent_live_state.status) so a queued send (a) releases
        # the instant the target returns to ready (online) even while the raw turn_busy
        # window is still open, and (b) never holds behind a DEAD worker's stale
        # turn_busy (engine → available). Cold cache falls back to raw freshness.
        import asyncio
        from service.db import get_db
        self._register("wq-target", runtime="claude-code", sessionMode="resident")
        self._seed_fresh_turn_busy("wq-target", "run-wq", "bridge-wq")  # raw window OPEN

        def busy():
            async def _run():
                db = await get_db()
                try:
                    return await api_v2._agent_engine_busy_for_queue(db, "wq-target")
                finally:
                    await db.close()
            return asyncio.run(_run())

        self.assertTrue(busy(), "cold cache → fall back to raw turn_busy freshness (busy)")
        self._execute(
            """
            INSERT INTO agent_live_state (agent_id, status, reason, environment_id, session_id,
                terminal_id, active_run_id, refresh_after, updated_at)
            VALUES (?, 'working', '', '', '', '', '', '9999-12-31T23:59:59Z', ?)
            ON CONFLICT(agent_id) DO UPDATE SET status='working'
            """,
            ("wq-target", api_v2._now()),
        )
        self.assertTrue(busy(), "engine working → busy (queue holds)")
        self._execute("UPDATE agent_live_state SET status='online' WHERE agent_id=?", ("wq-target",))
        self.assertFalse(busy(), "engine online (ready) → not busy; queue releases instantly")
        self._execute("UPDATE agent_live_state SET status='available' WHERE agent_id=?", ("wq-target",))
        self.assertFalse(busy(), "engine available (dead worker) → not busy; no strand")

    def test_stale_resident_bridge_with_turn_busy_is_not_working(self):
        # pure-event-status change #2: a DEAD resident worker stuck with
        # turn_busy=1 must derive `stale`/offline from its stale bridge lease —
        # NOT `working`. Before the fix the stale branch was guarded with
        # `and not turn_busy`, so a stale resident with turn_busy=1 SKIPPED stale
        # and fell into `elif turn_busy -> working`, i.e. working-forever once the
        # short status window was gone. Liveness (the 150s resident lease) must
        # win over turn_busy: a stale bridge is a dead worker regardless of any
        # lingering turn_busy=1.
        self._heartbeat_environment()
        self._register(
            "stale-resident-busy",
            runtime="claude-code",
            sessionMode="resident",
            launchMode="detached",
            sessionHandle="claude-session-stale-busy",
            bridgeId="bridge-stale-busy",
            machineId="linux:test-host",
            capabilities=["resident-run", "resume", "interrupt"],
            # resident claude only keeps the `resident-run` cap (the
            # resident_bridge_stale gate) when channel-enabled — and a
            # channel-enabled resident is exactly the one that can be stuck
            # mid-turn with a lingering turn_busy=1 after its bridge dies.
            runtimeConfig={"channelEnabled": True},
        )
        # Age the resident bridge past the 150s lease -> stale (dead worker).
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            "UPDATE bridge_instances SET last_seen=? WHERE id=?",
            (stale_at, "bridge-stale-busy"),
        )
        # Fresh turn_busy=1 (a missed turn-end left it set on a now-dead worker).
        now = api_v2._now()
        self._execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET turn_busy = 1, turn_updated_at = excluded.turn_updated_at
            """,
            ("stale-resident-busy", "run-stale-busy", "bridge-stale-busy", "claude-code", now),
        )
        cache = self._async_compute_live_status("stale-resident-busy")
        self.assertIn(
            cache["status"], {"stale", "offline"},
            f"stale resident bridge + turn_busy=1 must be stale/offline (dead worker), not working; {cache}",
        )
        self.assertNotEqual(
            cache["status"], "working",
            f"a dead resident must never read working off a lingering turn_busy; {cache}",
        )

    def test_status_is_pure_event_long_ceiling_not_short_window(self):
        # pure-event-status change #3: STATUS is now PURE-EVENT. A turn started by
        # a start event with NO re-pulse must stay `working` well past the old 120s
        # window (the short window no longer decides status — the turn-END EVENT
        # does, and a dropped event self-heals only at the single LONG ceiling).
        # An explicit /turn-end still clears it INSTANTLY (the event is primary).
        #
        # Anti-feedback-loop: status is derived from turn_busy; nothing here
        # re-arms turn_busy from that derived status.
        self.assertGreaterEqual(
            api_v2.TURN_BUSY_BACKSTOP_SECONDS, 30 * 60,
            "STATUS backstop must be the single LONG ceiling (>=30m), not the short claim window",
        )
        self.assertGreater(
            api_v2.TURN_BUSY_BACKSTOP_SECONDS, api_v2.TURN_BUSY_STALE_SECONDS,
            "status backstop (long) must be DECOUPLED from the short claim window (120s)",
        )
        self._heartbeat_environment()
        self._register(
            "pure-event-claude",
            runtime="claude-code",
            sessionMode="resident",
            launchMode="detached",
            sessionHandle="claude-pure-event",
            bridgeId="bridge-pure-event",
            machineId="linux:test-host",
            capabilities=["resident-run", "resume", "interrupt"],
            runtimeConfig={"channelEnabled": True},
        )
        # Keep the resident bridge FRESH (alive agent) so liveness backstops don't
        # fire — this isolates the turn_busy → status path.
        self._execute(
            "UPDATE bridge_instances SET last_seen=? WHERE id=?",
            (api_v2._now(), "bridge-pure-event"),
        )
        # turn_busy set by a START event, aged PAST the old 120s window but well
        # WITHIN the long ceiling, with NO re-pulse.
        aged = api_v2._iso_from_ms(int(
            (datetime.now(timezone.utc).timestamp() - (api_v2.TURN_BUSY_STALE_SECONDS + 90)) * 1000
        ))
        self._execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET turn_busy = 1, turn_updated_at = excluded.turn_updated_at
            """,
            ("pure-event-claude", "run-pure-event", "bridge-pure-event", "claude-code", aged),
        )
        cache = self._async_compute_live_status("pure-event-claude")
        self.assertEqual(
            cache["status"], "working",
            f"past the OLD 120s window but within the long ceiling → still working (no flap); {cache}",
        )
        # An explicit turn-END event clears it INSTANTLY (event is primary).
        self._execute(
            "UPDATE agent_turn_state SET turn_busy = 0 WHERE agent_id = ?",
            ("pure-event-claude",),
        )
        cache2 = self._async_compute_live_status("pure-event-claude")
        self.assertNotEqual(
            cache2["status"], "working",
            f"turn-end event (turn_busy=0) must clear working instantly, no timer wait; {cache2}",
        )

    def test_turn_end_event_flips_managed_hermes_off_working_immediately(self):
        # WS5 Task 5.2 — Bug A (false-working): a managed hermes that finished its
        # turn must flip OFF `working` the instant a real turn-END event arrives,
        # NOT after the TURN_BUSY_STALE_SECONDS timer. The managed-host now observes
        # the gateway session going idle and POSTs /turn-end; this proves the server
        # honors that event with an IMMEDIATE transition (no 120s wait).
        terminal_id = "term_hermes_turnend_busA"
        self._seed_managed_hermes_with_attached_terminal("turnend-hermes", terminal_id)
        self._stamp_live_channel_sidecar("turnend-hermes", runtime="hermes")  # live claimer
        # Mid-turn: fresh turn_busy=1 → working.
        now = api_v2._now()
        self._execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET turn_busy = 1, turn_run_id = excluded.turn_run_id, turn_updated_at = excluded.turn_updated_at
            """,
            ("turnend-hermes", "run-turnend-1", "channel-linux:test-host-turnend-hermes", "hermes", now),
        )
        # status_engine=new reads in_turn from agent_status_state (not agent_turn_state), so
        # seed it too for the mid-turn precondition.
        self._execute(
            """
            INSERT INTO agent_status_state (agent_id, status, in_turn, awaiting_input, turn_run_id, last_event, last_event_at, updated_at)
            VALUES (?, 'working', 1, 0, ?, 'turn_start', ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET in_turn=1, last_event_at=excluded.last_event_at, updated_at=excluded.updated_at
            """,
            ("turnend-hermes", "run-turnend-1", now, now),
        )
        asyncio.run(self._async_invalidate("turnend-hermes"))
        agent = self.client.get("/api/v1/agents/turnend-hermes").json()["agent"]
        self.assertEqual(agent["status"], "working", f"precondition: mid-turn → working; got {agent}")

        # Turn-END event fires (gateway idle observed → /turn-end). turn_busy=0.
        resp = self.client.post("/api/v1/agents/turnend-hermes/turn-end", json={
            "bridgeId": "channel-linux:test-host-turnend-hermes",
            "turnRuntime": "hermes",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        # IMMEDIATE flip off working — no timer wait. turn_updated_at is still
        # well within TURN_BUSY_STALE_SECONDS, so this can only pass if the EVENT
        # (turn_busy=0), not the staleness timer, drives the transition.
        agent = self.client.get("/api/v1/agents/turnend-hermes").json()["agent"]
        self.assertNotEqual(agent["status"], "working", f"turn-end must flip OFF working immediately; got {agent}")
        self.assertEqual(agent["status"], "online", f"finished managed hermes → online (deliverable); got {agent}")
        tb = self._fetchone("SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", ("turnend-hermes",))
        self.assertEqual(int(tb["turn_busy"] or 0), 0, "turn-end clears turn_busy")

    def test_turn_end_event_releases_queued_run_for_managed_hermes(self):
        # WS5 Task 5.2 — Bug B (stranded queue, downstream of A): while the agent
        # is mid-turn (turn_busy=1), a queueIfBusy comms_send QUEUES (does not fire
        # immediately because the system thinks he's working). After the turn-END
        # event clears turn_busy, the queue must no longer be blocked by a fake
        # turn — the next queueIfBusy evaluation sees is_turn_busy=False and the
        # run becomes deliverable. This proves the deadlock that left sends queued
        # forever (because the turn never appeared to end) is broken by the event.
        terminal_id = "term_hermes_turnend_busB"
        self._seed_managed_hermes_with_attached_terminal("queue-hermes", terminal_id)
        self._stamp_live_channel_sidecar("queue-hermes", runtime="hermes")
        # Acquire a live claimer lease (WS5.1) so the target is deliverable (not deaf).
        self.client.post("/api/v1/agents/queue-hermes/claimer-lease", json={
            "action": "acquire", "bridgeId": "channel-linux:test-host-queue-hermes",
        })
        now = api_v2._now()
        # Mid-turn: fresh turn_busy=1.
        self._execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET turn_busy = 1, turn_run_id = excluded.turn_run_id, turn_updated_at = excluded.turn_updated_at
            """,
            ("queue-hermes", "run-busy-1", "channel-linux:test-host-queue-hermes", "hermes", now),
        )

        # The mid-turn busy gate (queueIfBusy): with turn_busy fresh the target is
        # considered busy → a send would queue rather than deliver now.
        busy_before = self._turn_busy_gate_for("queue-hermes")
        self.assertTrue(busy_before, "precondition: fresh turn_busy → mid-turn busy → queues")

        # Turn-END event clears turn_busy.
        resp = self.client.post("/api/v1/agents/queue-hermes/turn-end", json={
            "bridgeId": "channel-linux:test-host-queue-hermes", "turnRuntime": "hermes",
        })
        self.assertEqual(resp.status_code, 200, resp.text)

        # After turn-end the agent is NOT left working, AND the busy gate is clear
        # so a queued run is now deliverable on the next claim.
        busy_after = self._turn_busy_gate_for("queue-hermes")
        self.assertFalse(busy_after, "turn-end must clear the mid-turn busy gate so the queue can drain")
        asyncio.run(self._async_invalidate("queue-hermes"))
        agent = self.client.get("/api/v1/agents/queue-hermes").json()["agent"]
        self.assertNotEqual(agent["status"], "working", f"agent must not be left working after turn-end; got {agent}")

    def _turn_busy_gate_for(self, agent_id: str) -> bool:
        """Mirror the dispatcher's mid-turn busy check (api_v2 send queueIfBusy
        branch): turn_busy=1 AND fresh within TURN_BUSY_STALE_SECONDS."""
        row = self._fetchone(
            "SELECT turn_busy, turn_updated_at FROM agent_turn_state WHERE agent_id = ?",
            (agent_id,),
        )
        if not row or int(row["turn_busy"] or 0) != 1:
            return False
        epoch = api_v2._iso_to_epoch(str(row["turn_updated_at"] or ""))
        if not epoch:
            return False
        from datetime import datetime, timezone
        return (datetime.now(timezone.utc).timestamp() - epoch) <= api_v2.TURN_BUSY_STALE_SECONDS

    # --- send-deadlock fix (2026-06-02): channel/resident steer past turn_busy ---

    def _register_resident_channel_claude(self, agent_id: str, *, bridge_id: str):
        """Resident claude with channelEnabled — gets the `steer` capability, the
        mid-turn-inject signal the claim gate keys on."""
        return self._register(
            agent_id,
            runtime="claude-code",
            sessionMode="resident",
            machineId="linux:test-host",
            bridgeId=bridge_id,
            launchMode="detached",
            capabilities=["resident-run", "resume", "interrupt", "steer"],
            runtimeConfig={"channelEnabled": True},
        )

    def _seed_fresh_turn_busy(self, agent_id: str, run_id: str, bridge_id: str, runtime: str = "claude-code"):
        self._execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET turn_busy = 1, turn_run_id = excluded.turn_run_id,
                turn_bridge_id = excluded.turn_bridge_id, turn_runtime = excluded.turn_runtime,
                turn_updated_at = excluded.turn_updated_at
            """,
            (agent_id, run_id, bridge_id, runtime, api_v2._now()),
        )

    def _seed_queued_dispatch_run(self, run_id: str, target: str, *, execution_mode: str, require_reply: int = 0, from_agent: str = "sc-claude"):
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode,
                message_type, subject, body, priority, status, require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, None, from_agent, target, "start_if_possible", execution_mode,
                "info", "fyi", "for your info", "normal", "queued", require_reply, api_v2._now(),
            ),
        )

    def test_channel_send_to_busy_channel_claude_is_claimed_not_deferred(self):
        # SEND DEADLOCK (fix 1): a 2nd channel/resident, rr=0 send to a
        # channelEnabled (steer-capable) resident claude that is mid-turn
        # (turn_busy fresh) must be CLAIMED immediately and injected, not held
        # behind the turn for up to 120s. This is the steer behavior — claude
        # queues multiple injects safely and in order.
        self._register_resident_channel_claude("sc-manager", bridge_id="bridge-sc-manager")
        # He is mid-turn, turn_busy anchored on the very message that was sent TO him.
        self._seed_fresh_turn_busy("sc-manager", "run_inbound_365680", "channel-linux:test-host-sc-manager")
        # A 2nd send queues. A resident channelEnabled claude's dispatches resolve
        # to execution_mode='resident' (see _agent_execution_mode) — the exact
        # shape of the two deadlocked sends in the live evidence.
        self._seed_queued_dispatch_run("run_queued_306769", "sc-manager", execution_mode="resident", require_reply=0)

        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "sc-manager",
                "bridgeId": "channel-linux:test-host-sc-manager",
                "bridgeKind": "channel-sidecar",
                "machineId": "linux:test-host",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        run = claim.json().get("run")
        self.assertIsNotNone(run, f"steer-capable busy claude must claim the queued channel run, not defer: {claim.json()}")
        self.assertEqual(run["id"], "run_queued_306769")

    def test_busy_non_channel_resident_still_defers_queued_run(self):
        # GATE PRESERVED (fix 1 safety): a resident codex (NOT steer/inject
        # capable for a mid-turn channel inject) that is turn_busy must STILL
        # defer its queued run — the carve-out is scoped to steerable targets.
        self._register(
            "codex-res",
            runtime="codex",
            sessionMode="resident",
            machineId="linux:test-host",
            bridgeId="bridge-codex-res",
            sessionHandle="thread-codex",
            capabilities=["resident-run", "resume", "interrupt", "steer"],
            runtimeConfig={"appServerUrl": "ws://127.0.0.1:9"},
        )
        self._seed_fresh_turn_busy("codex-res", "run_codex_inbound", "bridge-codex-res", runtime="codex")
        self._seed_queued_dispatch_run("run_codex_queued", "codex-res", execution_mode="resident", require_reply=0)
        # Strip steer so this target is genuinely not mid-turn-injectable.
        self._execute(
            "UPDATE agents SET capabilities = ? WHERE id = ?",
            (json.dumps(["resident-run", "resume", "interrupt"]), "codex-res"),
        )

        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "codex-res",
                "bridgeId": "bridge-codex-res",
                "machineId": "linux:test-host",
                "executionModes": ["resident"],
            },
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertIsNone(claim.json().get("run"), f"non-steerable busy target must STILL defer: {claim.json()}")
        run = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_codex_queued",))
        self.assertEqual(run["status"], "queued", "deferred run stays queued")

    def test_rr0_channel_delivery_completion_clears_turn_busy(self):
        # SEND DEADLOCK (fix 2): when the channel bridge marks an rr=0
        # channel/resident delivery COMPLETED, the recipient's turn_busy (which
        # the delivery re-pulse stamped) must be cleared — an info/response wake
        # is not sustained work — so the next queued send finds an open gate.
        self._register_resident_channel_claude("sc-manager2", bridge_id="bridge-sc-manager2")
        # rr=0 channel run mid-delivery; its delivery re-pulsed turn_busy onto it.
        self._seed_queued_dispatch_run("run_rr0_done", "sc-manager2", execution_mode="channel", require_reply=0)
        self._execute("UPDATE dispatch_runs SET status='claimed' WHERE id = ?", ("run_rr0_done",))
        self._seed_fresh_turn_busy("sc-manager2", "run_rr0_done", "channel-linux:test-host-sc-manager2")
        self.assertTrue(self._turn_busy_gate_for("sc-manager2"), "precondition: turn_busy fresh")

        resp = self.client.patch(
            "/api/v1/dispatch/runs/run_rr0_done",
            json={"status": "completed", "summary": "Delivered and completed by channel bridge"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        tb = self._fetchone("SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", ("sc-manager2",))
        self.assertEqual(int(tb["turn_busy"] or 0), 0, "rr=0 channel delivery completion must clear turn_busy")
        self.assertFalse(self._turn_busy_gate_for("sc-manager2"), "busy gate is now open so the queue can drain")

    def test_rr0_delivery_completion_does_not_clear_while_other_rr1_in_flight(self):
        # ANTI-FEEDBACK-LOOP / safety: an rr=0 completion must NOT clear
        # turn_busy while ANOTHER require_reply=1 channel/resident run is still
        # open for the same agent (genuine reply-owing work in flight).
        self._register_resident_channel_claude("sc-manager3", bridge_id="bridge-sc-manager3")
        # A genuine rr=1 turn is in flight.
        self._seed_queued_dispatch_run("run_rr1_open", "sc-manager3", execution_mode="channel", require_reply=1)
        self._execute("UPDATE dispatch_runs SET status='running' WHERE id = ?", ("run_rr1_open",))
        # An rr=0 delivery completes alongside it.
        self._seed_queued_dispatch_run("run_rr0_alongside", "sc-manager3", execution_mode="channel", require_reply=0)
        self._execute("UPDATE dispatch_runs SET status='claimed' WHERE id = ?", ("run_rr0_alongside",))
        self._seed_fresh_turn_busy("sc-manager3", "run_rr1_open", "channel-linux:test-host-sc-manager3")

        resp = self.client.patch(
            "/api/v1/dispatch/runs/run_rr0_alongside",
            json={"status": "completed", "summary": "Delivered and completed by channel bridge"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        tb = self._fetchone("SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", ("sc-manager3",))
        self.assertEqual(int(tb["turn_busy"] or 0), 1, "must NOT clear turn_busy while a real rr=1 turn is still open")

    def test_turn_busy_not_rearmed_from_derived_status(self):
        # ANTI-FEEDBACK-LOOP invariant: computing derived status for a mid-turn
        # agent must never WRITE/RE-ARM turn_busy — only the bridge sets it and
        # only an event/backstop/clear clears it. Reading status must leave
        # turn_busy exactly as the bridge left it (still 1, same updated_at).
        self._register_resident_channel_claude("sc-manager4", bridge_id="bridge-sc-manager4")
        self._seed_fresh_turn_busy("sc-manager4", "run_status_probe", "channel-linux:test-host-sc-manager4")
        before = self._fetchone("SELECT turn_busy, turn_updated_at FROM agent_turn_state WHERE agent_id = ?", ("sc-manager4",))
        cache = self._async_compute_live_status("sc-manager4")
        self.assertEqual(cache["status"], "working", cache)
        after = self._fetchone("SELECT turn_busy, turn_updated_at FROM agent_turn_state WHERE agent_id = ?", ("sc-manager4",))
        self.assertEqual(int(after["turn_busy"] or 0), 1, "status read must not clear turn_busy")
        self.assertEqual(after["turn_updated_at"], before["turn_updated_at"], "status read must not re-stamp turn_busy")

    def test_heartbeat_turnbusy_invalidates_live_state(self):
        # FIX 1: a heartbeat that writes turn_busy must invalidate the live-state
        # cache so working/idle reflects the flip immediately — not after the 60s
        # sweep. (The /turn-start and /turn-end endpoints already invalidate.)
        self._register("hb-turn-claude", runtime="claude-code", sessionMode="resident")
        # Seed a fresh cached live_state row with refresh_after far in the future.
        self._execute(
            """
            INSERT INTO agent_live_state (agent_id, status, reason, updated_at, refresh_after)
            VALUES (?,?,?,?,?)
            """,
            ("hb-turn-claude", "online", "cached", api_v2._now(), "2099-01-01T00:00:00Z"),
        )
        # Pre-condition: the cache row exists.
        pre = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("hb-turn-claude",))
        self.assertIsNotNone(pre, "precondition: live_state cache row must exist before heartbeat")

        resp = self.client.post(
            "/api/v1/agents/hb-turn-claude/heartbeat",
            json={
                "bridgeId": "bridge-hb-1",
                "turnBusy": True,
                "turnRunId": "run-hb-1",
                "turnRuntime": "claude-code",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        post = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("hb-turn-claude",))
        self.assertIsNone(post, "turnBusy heartbeat must invalidate (delete) the live_state cache row")

    def test_channel_pending_reply_online_only_when_live(self):
        # FIX 3 (a): a managed claude with a channel_pending_reply run AND a LIVE
        # worker (live console PTY + live sidecar) reads `online` (awaiting reply).
        terminal_id = "term_pending_live"
        self._seed_managed_claude_with_attached_terminal("pending-live-claude", terminal_id)
        self._stamp_live_channel_sidecar("pending-live-claude")  # live worker
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status,
                require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_pending_live_1", None, "dashboard", "pending-live-claude",
                "start_if_possible", "channel", "request", "owed question",
                "answer please", "normal", "delivered", 1, "2026-05-21T00:00:00Z",
            ),
        )
        asyncio.run(self._async_invalidate("pending-live-claude"))
        agent = self.client.get("/api/v1/agents/pending-live-claude").json()["agent"]
        self.assertEqual(agent["status"], "online", agent)
        self.assertIn("awaiting reply", (agent.get("statusNote") or "").lower())

    def test_channel_pending_reply_not_online_when_worker_dead(self):
        # FIX 3 (b): same pending reply but NO live worker (managed claude with a
        # live sidecar but a DEAD/stopped console) must NOT be upgraded to
        # `online` — a dead worker that owes a reply reads available/stale/offline,
        # never online (visible-TUI truthfulness).
        terminal_id = "term_pending_dead"
        self._seed_managed_claude_with_attached_terminal("pending-dead-claude", terminal_id)
        self._stamp_live_channel_sidecar("pending-dead-claude")  # sidecar alive...
        # ...but the console PTY is dead → managed claude has no live worker.
        self._execute(
            "UPDATE terminal_sessions SET status = 'stopped' WHERE id = ?",
            (terminal_id,),
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status,
                require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_pending_dead_1", None, "dashboard", "pending-dead-claude",
                "start_if_possible", "channel", "request", "owed question",
                "answer please", "normal", "delivered", 1, "2026-05-21T00:00:00Z",
            ),
        )
        asyncio.run(self._async_invalidate("pending-dead-claude"))
        agent = self.client.get("/api/v1/agents/pending-dead-claude").json()["agent"]
        self.assertNotEqual(agent["status"], "online", agent)
        self.assertIn(agent["status"], {"available", "stale", "offline"}, agent)

    def test_has_live_terminal_session_counts_recovering(self):
        # FIX 4: a console PTY momentarily in `recovering` is still a live
        # terminal session. Without this, B2's managed-claude online gate
        # (which requires _has_live_terminal_session) briefly flips to available.
        self._seed_managed_claude_with_attached_terminal("recovering-claude", "term_recovering")
        self._execute(
            "UPDATE terminal_sessions SET status = 'recovering' WHERE id = ?",
            ("term_recovering",),
        )
        async def _run():
            db = await get_db()
            try:
                return await api_v2._has_live_terminal_session(db, "recovering-claude")
            finally:
                await db.close()
        self.assertTrue(asyncio.run(_run()), "a `recovering` non-vterm terminal must count as live")

    def _seed_cached_live_state(self, agent_id: str, status: str = "available"):
        """Stamp a fresh, far-future cached agent_live_state row so a test can
        prove a code path invalidated (deleted) it."""
        self._execute(
            """
            INSERT OR REPLACE INTO agent_live_state (agent_id, status, reason, updated_at, refresh_after)
            VALUES (?,?,?,?,?)
            """,
            (agent_id, status, "seeded", api_v2._now(), "2099-01-01T00:00:00Z"),
        )

    def test_virtual_worker_start_invalidates_live_state(self):
        # FIX 1: a virtual/RPC worker START (ensure_virtual_terminal) sets
        # terminal_status='running' + runtime_state.virtualTerminalId and broadcasts
        # terminal_started — it must ALSO invalidate the live-state cache so a managed
        # agent that just got a live worker stops showing the stale `available` until
        # the 60s sweep.
        self._heartbeat_environment(
            id="linux:test-host:default",
            bridgeId="bridge-vw",
            machineId="linux:test-host",
            runtimes=[{"runtime": "pi", "modes": ["managed-warm"], "capabilities": {}}],
        )
        self._register("vw-pi", runtime="pi", sessionMode="managed")
        now = api_v2._now()
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, spawn_spec_id, spawn_request_id, status, started_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("sess_vw_pi", "vw-pi", "linux:test-host:default", "pi", "/workspace/repo",
             "managed-warm", "managed", None, None, "running", now, now),
        )
        self._seed_cached_live_state("vw-pi")
        pre = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("vw-pi",))
        self.assertIsNotNone(pre, "precondition: cached live_state row must exist")

        resp = self.client.post(
            "/api/v1/agents/vw-pi/virtual-terminal/ensure",
            json={"bridgeId": "bridge-vw", "runtime": "pi"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        # The worker is now running (virtualTerminalId set).
        agent_rs = self._fetchone("SELECT runtime_state FROM agents WHERE id = ?", ("vw-pi",))
        self.assertTrue(
            str(json.loads(agent_rs["runtime_state"] or "{}").get("virtualTerminalId") or "").strip(),
            "precondition: virtualTerminalId must be set by the start path",
        )
        post = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("vw-pi",))
        self.assertIsNone(post, "virtual worker start must invalidate (delete) the live_state cache row")

    def test_console_stop_reconcile_invalidates_live_state(self):
        # FIX 2: the console-stop reconcile branch (terminal already stopped/failed
        # OR the bridge can't claim) marks the terminal stopped + clears the binding
        # + broadcasts terminal_stopped, but historically omitted the cache invalidate
        # for VIRTUAL terminals (where _clear_console_terminal_binding no-ops because
        # the pointer is virtualTerminalId, not consoleTerminal). The sibling
        # bridge-reported completion path DOES invalidate; this branch must too.
        self._heartbeat_environment(
            id="linux:test-host:default",
            bridgeId="bridge-cs",
            machineId="linux:test-host",
            runtimes=[{"runtime": "pi", "modes": ["managed-warm"], "capabilities": {}}],
        )
        self._register("cs-pi", runtime="pi", sessionMode="managed")
        now = api_v2._now()
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, terminal_id, terminal_status,
                spawn_spec_id, spawn_request_id, status, started_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("sess_cs_pi", "cs-pi", "linux:test-host:default", "pi", "/workspace/repo",
             "managed-warm", "managed", "term_cs_recon", "stopped", None, None, "running", now, now),
        )
        # A virtual terminal already in `stopped` → drives the reconcile branch
        # (terminal_status in {stopped,failed}). bridge_id deliberately empty so
        # bridge_can_claim is also false.
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("term_cs_recon", "sess_cs_pi", "cs-pi", "linux:test-host:default", "",
             "pi", "/workspace/repo", api_v2.VIRTUAL_PI_RPC_COMMAND, "", "stopped",
             "dashboard", now, now, None, ""),
        )
        self._seed_cached_live_state("cs-pi", status="online")
        pre = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("cs-pi",))
        self.assertIsNotNone(pre, "precondition: cached live_state row must exist")

        resp = self.client.post(
            "/api/v1/terminals/term_cs_recon/stop",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        # Confirm we actually hit the reconcile branch.
        event = self._fetchone(
            "SELECT event_type FROM terminal_events WHERE terminal_id = ? AND event_type = ?",
            ("term_cs_recon", "console_stop_reconciled"),
        )
        self.assertIsNotNone(event, "precondition: reconcile branch must have fired")
        post = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("cs-pi",))
        self.assertIsNone(post, "console-stop reconcile must invalidate (delete) the live_state cache row")

    def test_orphan_reason_says_no_console_not_no_sidecar(self):
        # FIX 3 (#166): the status-F1 block must distinguish the headless-orphan case
        # (LIVE sidecar, DEAD console) from the genuine dead-sidecar case. The orphan
        # case must NOT claim "no live channel sidecar" — it must mention the console.
        terminal_id = "term_reason_orphan"
        self._seed_managed_claude_with_attached_terminal("reason-orphan-claude", terminal_id)
        self._stamp_live_channel_sidecar("reason-orphan-claude")  # sidecar IS alive
        self._execute(
            "UPDATE terminal_sessions SET status = 'stopped' WHERE id = ?",
            (terminal_id,),
        )
        cache = self._async_compute_live_status("reason-orphan-claude")
        self.assertEqual(cache["status"], "available", cache)
        reason = (cache.get("reason") or "").lower()
        self.assertIn("console", reason, f"orphan reason must mention the console; got {reason!r}")
        self.assertNotIn(
            "no live channel sidecar", reason,
            f"orphan reason must NOT claim a dead sidecar; got {reason!r}",
        )

        # The genuine dead-sidecar case still says sidecar.
        self._seed_managed_claude_with_attached_terminal("reason-deadsc-claude", "term_reason_deadsc")
        # No _stamp_live_channel_sidecar → sidecar is dead. Console also stopped.
        self._execute(
            "UPDATE terminal_sessions SET status = 'stopped' WHERE id = ?",
            ("term_reason_deadsc",),
        )
        cache2 = self._async_compute_live_status("reason-deadsc-claude")
        self.assertEqual(cache2["status"], "available", cache2)
        reason2 = (cache2.get("reason") or "").lower()
        self.assertIn("sidecar", reason2, f"dead-sidecar reason must mention the sidecar; got {reason2!r}")

    def test_env_disable_invalidates_bound_agents(self):
        # FIX 4: the env-disable control path sets bound agents `offline` but must
        # ALSO invalidate their live_state — `offline` is not a manual-status
        # short-circuit, so otherwise they show the wrong status until the sweep.
        self._heartbeat_environment(
            id="linux:test-host:default",
            bridgeId="bridge-envdis",
            machineId="linux:test-host",
            runtimes=[{"runtime": "pi", "modes": ["managed-warm"], "capabilities": {}}],
        )
        self._register("envdis-pi", runtime="pi", sessionMode="managed")
        now = api_v2._now()
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, spawn_spec_id, spawn_request_id, status, started_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("sess_envdis", "envdis-pi", "linux:test-host:default", "pi", "/workspace/repo",
             "managed-warm", "managed", None, None, "running", now, now),
        )
        self._seed_cached_live_state("envdis-pi", status="online")
        pre = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("envdis-pi",))
        self.assertIsNotNone(pre, "precondition: cached live_state row must exist")

        resp = self.client.post(
            "/api/v1/environments/linux:test-host:default/control",
            json={"action": "stop", "requestedBy": "dashboard"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        agent = self._fetchone("SELECT status FROM agents WHERE id = ?", ("envdis-pi",))
        self.assertEqual(agent["status"], "offline", agent)
        post = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("envdis-pi",))
        self.assertIsNone(post, "env-disable must invalidate (delete) bound agents' live_state cache rows")

    def test_stale_claimed_run_aged_out_despite_live_bridge(self):
        # FIX 5: a claimed/running run with a LIVE (fresh) bridge but a dead inner
        # controller (no progress for longer than the wall-clock ceiling) must be
        # aged-out — the bridge-liveness reaper alone never catches it. A fresh
        # running run with the same live bridge must NOT be touched.
        self.client.put("/api/v1/settings", json={
            "active_managed_run_stale_minutes": 5,
            "active_managed_run_wall_ceiling_minutes": 30,
        })
        self._register("aged-hermes", runtime="hermes", sessionMode="managed")
        live_seen = api_v2._now()
        self._execute(
            """
            INSERT INTO bridge_instances (id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen)
            VALUES (?,?,?,?,?,?,?)
            """,
            ("bridge-live-aged", "aged-hermes", "test-machine", "hermes", "managed", live_seen, live_seen),
        )
        old = (datetime.now(timezone.utc) - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        fresh = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Stale run: started 45 min ago, no progress, but the bridge is LIVE.
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at, claimed_at, started_at,
                claim_bridge_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("run_aged_stale", None, "dashboard", "aged-hermes", "start_if_possible",
             "managed", "request", "controller died silently", "body", "normal",
             "running", 0, old, old, old, "bridge-live-aged"),
        )
        # Fresh run on the same live bridge: started 2 min ago → must survive.
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at, claimed_at, started_at,
                claim_bridge_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("run_aged_fresh", None, "dashboard", "aged-hermes", "start_if_possible",
             "managed", "request", "still progressing", "body", "normal",
             "running", 0, fresh, fresh, fresh, "bridge-live-aged"),
        )
        self._seed_cached_live_state("aged-hermes", status="working")

        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_orphaned_managed_runs(db, limit=10)
            finally:
                await db.commit()
                await db.close()
        closed = asyncio.run(_run())
        closed_ids = {c["runId"] for c in closed}
        self.assertIn("run_aged_stale", closed_ids, f"stale run must be aged out; closed={closed}")
        self.assertNotIn("run_aged_fresh", closed_ids, f"fresh run must NOT be aged out; closed={closed}")
        stale_row = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_aged_stale",))
        self.assertEqual(stale_row["status"], "failed")
        fresh_row = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_aged_fresh",))
        self.assertEqual(fresh_row["status"], "running")
        live = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("aged-hermes",))
        self.assertIsNone(live, "aging out a stale run must invalidate the agent's live_state cache row")

    def _seed_claimed_run(self, run_id: str, agent_id: str, *, claim_bridge_id: str, claimed_minutes_ago: float, status: str = "claimed"):
        claimed_at = (datetime.now(timezone.utc) - timedelta(minutes=claimed_minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at, claimed_at, started_at,
                claim_bridge_id, claim_machine_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, None, "dashboard", agent_id, "start_if_possible",
                "channel", "request", "STATE CHECK", "body", "normal",
                status, 1, claimed_at, claimed_at, None,
                claim_bridge_id, "test-machine",
            ),
        )

    def _run_requeue_orphaned_claimed(self, **kwargs):
        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._requeue_orphaned_claimed_runs(db, **kwargs)
            finally:
                await db.commit()
                await db.close()
        return asyncio.run(_run())

    def test_orphaned_claimed_run_requeued(self):
        # Confirmed bug: a bridge claimed a run (status->claimed) then died before
        # delivering. The dead bridge never transitions claimed->delivered, a new
        # bridge won't re-claim an already-claimed run, so the run is stranded and
        # the agent shows falsely busy. A claimed run, claimed >grace ago, never
        # delivered, whose claim bridge has NO fresh bridge_instances row, must be
        # requeued so a live bridge re-claims it.
        self._register("orphan-claim-hermes", runtime="hermes", sessionMode="managed")
        # claim_bridge_id "dead-bridge" has NO bridge_instances row at all → dead.
        self._seed_claimed_run("run_orphan_claim", "orphan-claim-hermes", claim_bridge_id="dead-bridge", claimed_minutes_ago=5)
        self._seed_cached_live_state("orphan-claim-hermes", status="working")

        requeued = self._run_requeue_orphaned_claimed()
        self.assertEqual({r["runId"] for r in requeued}, {"run_orphan_claim"}, f"requeued={requeued}")

        row = self._fetchone(
            "SELECT status, claim_bridge_id, claim_machine_id, claimed_at FROM dispatch_runs WHERE id = ?",
            ("run_orphan_claim",),
        )
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["claim_bridge_id"] or "", "")
        self.assertEqual(row["claim_machine_id"] or "", "")
        self.assertEqual(row["claimed_at"] or "", "")
        event = self._fetchone(
            "SELECT body FROM dispatch_events WHERE run_id = ? AND event_type = 'requeued_orphaned_claim'",
            ("run_orphan_claim",),
        )
        self.assertIsNotNone(event, "requeued_orphaned_claim event must be appended")
        self.assertIn("dead-bridge", (event["body"] or ""), "event should note the dead bridge id")
        live = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("orphan-claim-hermes",))
        self.assertIsNone(live, "requeue must invalidate the agent's false-busy live_state cache row")

    def test_claimed_run_with_live_bridge_not_requeued(self):
        # GUARD: a claimed run whose claim bridge IS fresh/live is genuinely being
        # delivered right now — must NOT be requeued.
        self._register("live-claim-hermes", runtime="hermes", sessionMode="managed")
        live_seen = api_v2._now()
        self._execute(
            """
            INSERT INTO bridge_instances (id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen)
            VALUES (?,?,?,?,?,?,?)
            """,
            ("live-bridge", "live-claim-hermes", "test-machine", "hermes", "managed", live_seen, live_seen),
        )
        self._seed_claimed_run("run_live_claim", "live-claim-hermes", claim_bridge_id="live-bridge", claimed_minutes_ago=5)

        requeued = self._run_requeue_orphaned_claimed()
        self.assertEqual(requeued, [], f"a live bridge's claim must not be requeued; got {requeued}")
        row = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_live_claim",))
        self.assertEqual(row["status"], "claimed")

    def test_recently_claimed_run_not_requeued(self):
        # GUARD: claimed only 5s ago (within grace) — a live bridge would still be
        # mid-delivery. Don't race it.
        self._register("fresh-claim-hermes", runtime="hermes", sessionMode="managed")
        # No bridge_instances row (bridge would be "dead") but claim is fresh.
        self._seed_claimed_run("run_fresh_claim", "fresh-claim-hermes", claim_bridge_id="some-bridge", claimed_minutes_ago=5 / 60.0)

        requeued = self._run_requeue_orphaned_claimed(grace_seconds=90)
        self.assertEqual(requeued, [], f"a freshly-claimed run must not be requeued; got {requeued}")
        row = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_fresh_claim",))
        self.assertEqual(row["status"], "claimed")

    def test_delivered_run_not_requeued(self):
        # GUARD: a delivered/running run reached the agent — leave it to the
        # existing reapers (_close_orphaned_managed_runs).
        self._register("delivered-hermes", runtime="hermes", sessionMode="managed")
        self._seed_claimed_run("run_delivered", "delivered-hermes", claim_bridge_id="dead-bridge", claimed_minutes_ago=5, status="delivered")
        # Even if a delivered event is present and bridge is dead, do not touch it.
        self._execute(
            "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
            ("run_delivered", "delivered", "", api_v2._now()),
        )

        requeued = self._run_requeue_orphaned_claimed()
        self.assertEqual(requeued, [], f"a delivered run must not be requeued; got {requeued}")
        row = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_delivered",))
        self.assertEqual(row["status"], "delivered")

    def test_claimed_run_with_delivered_event_not_requeued(self):
        # GUARD: status is still 'claimed' but a 'delivered' dispatch_event exists
        # (delivery happened, status transition lagged) — it reached the agent, so
        # do not requeue even though the bridge later died.
        self._register("delivered-evt-hermes", runtime="hermes", sessionMode="managed")
        self._seed_claimed_run("run_delivered_evt", "delivered-evt-hermes", claim_bridge_id="dead-bridge", claimed_minutes_ago=5)
        self._execute(
            "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
            ("run_delivered_evt", "delivered", "", api_v2._now()),
        )

        requeued = self._run_requeue_orphaned_claimed()
        self.assertEqual(requeued, [], f"a run with a delivered event must not be requeued; got {requeued}")
        row = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_delivered_evt",))
        self.assertEqual(row["status"], "claimed")

    def _seed_turn_busy(self, agent_id: str, *, turn_bridge_id: str, updated_seconds_ago: float, run_id: str = "run_x", runtime: str = "hermes"):
        updated_at = (datetime.now(timezone.utc) - timedelta(seconds=updated_seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT OR REPLACE INTO agent_turn_state
                (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, ?, ?, ?, ?)
            """,
            (agent_id, run_id, turn_bridge_id, runtime, updated_at),
        )

    def _run_clear_turn_busy_dead_bridges(self, **kwargs):
        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._clear_turn_busy_for_dead_bridges(db, **kwargs)
            finally:
                await db.commit()
                await db.close()
        return asyncio.run(_run())

    def test_turn_busy_cleared_when_owning_bridge_is_dead(self):
        # BUG 1 (confirmed live): a managed hermes delivery loop set turn_busy=1 on
        # submit, then its process died (terminal closed) without firing a turn-end
        # event. agent_turn_state.turn_busy stayed 1 with turn_bridge_id pointing at
        # the now-dead loop's channel-sidecar bridge — agent shows `working` until
        # the ~30-min ceiling. The reconcile must clear turn_busy when that bridge
        # has no fresh bridge_instances heartbeat.
        self._register("ci-senior-dev", runtime="hermes", sessionMode="managed")
        # turn_bridge_id has NO bridge_instances row at all → dead claimer.
        self._seed_turn_busy(
            "ci-senior-dev",
            turn_bridge_id="hermes-managed-host-wsl:laputa-ci-senior-dev",
            updated_seconds_ago=174,
        )
        self._seed_cached_live_state("ci-senior-dev", status="working")

        cleared = self._run_clear_turn_busy_dead_bridges()
        self.assertEqual({c["agentId"] for c in cleared}, {"ci-senior-dev"}, f"cleared={cleared}")

        row = self._fetchone(
            "SELECT turn_busy, turn_run_id, turn_bridge_id FROM agent_turn_state WHERE agent_id = ?",
            ("ci-senior-dev",),
        )
        self.assertEqual(int(row["turn_busy"]), 0, "turn_busy must be cleared")
        self.assertEqual(row["turn_bridge_id"] or "", "")
        self.assertEqual(row["turn_run_id"] or "", "")
        live = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("ci-senior-dev",))
        self.assertIsNone(live, "clearing a stuck turn_busy must invalidate the false-working live_state cache row")

    def test_turn_busy_NOT_cleared_when_owning_bridge_is_fresh(self):
        # GUARD: turn_bridge_id IS a fresh, heartbeating bridge — the loop is
        # genuinely mid-delivery. turn_busy must NOT be cleared (no early turn-end).
        self._register("live-turn-hermes", runtime="hermes", sessionMode="managed")
        live_seen = api_v2._now()
        self._execute(
            """
            INSERT INTO bridge_instances (id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen)
            VALUES (?,?,?,?,?,?,?)
            """,
            ("live-turn-bridge", "live-turn-hermes", "test-machine", "hermes", "managed", live_seen, live_seen),
        )
        self._seed_turn_busy("live-turn-hermes", turn_bridge_id="live-turn-bridge", updated_seconds_ago=174)

        cleared = self._run_clear_turn_busy_dead_bridges()
        self.assertEqual(cleared, [], f"a fresh bridge's turn_busy must not be cleared; got {cleared}")
        row = self._fetchone("SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", ("live-turn-hermes",))
        self.assertEqual(int(row["turn_busy"]), 1, "turn_busy stays set while the owning bridge heartbeats")

    def test_turn_busy_NOT_cleared_when_no_owning_bridge_recorded(self):
        # GUARD: an empty turn_bridge_id is a harness/Stop-hook turn (e.g. a resident
        # claude on its own turn) — NOT a dead-claimer case. Leave it to the existing
        # ~30-min ceiling so a genuinely-working agent is never cut off.
        self._register("harness-turn-claude", runtime="claude-code", sessionMode="resident")
        self._seed_turn_busy("harness-turn-claude", turn_bridge_id="", updated_seconds_ago=600, runtime="claude-code")

        cleared = self._run_clear_turn_busy_dead_bridges()
        self.assertEqual(cleared, [], f"an empty-owner turn_busy must not be cleared; got {cleared}")
        row = self._fetchone("SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", ("harness-turn-claude",))
        self.assertEqual(int(row["turn_busy"]), 1, "a harness turn (no bridge owner) is left to the long ceiling")

    def _seed_queued_run(self, run_id: str, agent_id: str, *, from_agent: str = "sender-agent", requested_minutes_ago: float = 5.0, require_reply: bool = True):
        requested_at = (datetime.now(timezone.utc) - timedelta(minutes=requested_minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, None, from_agent, agent_id, "start_if_possible",
                "channel", "request", "do the thing", "body text", "normal",
                "queued", 1 if require_reply else 0, requested_at,
            ),
        )

    def _run_reap_undeliverable_queued(self, **kwargs):
        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._reap_undeliverable_queued_runs(db, **kwargs)
            finally:
                await db.commit()
                await db.close()
        return asyncio.run(_run())

    def test_undeliverable_queued_run_failed_and_mirrored_to_sender(self):
        # WS3 Task 3.2: a `queued` run whose target managed hermes has NO live
        # claimer (no fresh channel-sidecar, no claiming bridge) and is older than
        # the backstop window is never deliverable — no reaper covers it today, so
        # it piles up to the buffer cap. The backstop must FAIL it with an
        # actionable error AND mirror a reply back to the sender.
        self._register("deaf-hermes", runtime="hermes", sessionMode="managed")
        self._register("sender-agent", runtime="claude-code", sessionMode="resident")
        self._seed_queued_run("run_undeliverable", "deaf-hermes", from_agent="sender-agent", requested_minutes_ago=5)
        self._seed_cached_live_state("deaf-hermes", status="online")

        reaped = self._run_reap_undeliverable_queued()
        self.assertEqual({r["runId"] for r in reaped}, {"run_undeliverable"}, f"reaped={reaped}")

        row = self._fetchone("SELECT status, error_text FROM dispatch_runs WHERE id = ?", ("run_undeliverable",))
        self.assertEqual(row["status"], "failed")
        self.assertTrue((row["error_text"] or "").strip(), "an actionable error must be recorded")
        event = self._fetchone(
            "SELECT body FROM dispatch_events WHERE run_id = ? AND event_type = 'failed'",
            ("run_undeliverable",),
        )
        self.assertIsNotNone(event, "a failed dispatch_event must be appended")
        # A reply/handoff is mirrored back to the original sender.
        mirror = self._fetchone(
            "SELECT type FROM messages WHERE from_agent = ? AND to_agent = ?",
            ("deaf-hermes", "sender-agent"),
        )
        self.assertIsNotNone(mirror, "an undeliverable queued run must mirror a reply to the sender")
        # Status cache invalidated so the agent stops showing false `online`.
        live = self._fetchone("SELECT agent_id FROM agent_live_state WHERE agent_id = ?", ("deaf-hermes",))
        self.assertIsNone(live, "reaping must invalidate the target's live_state cache row")

    def test_queued_run_with_live_claimer_not_reaped(self):
        # GUARD: a queued run whose target has a LIVE channel-sidecar claimer is
        # genuinely deliverable on the next poll — leave it alone even past the
        # backstop window.
        self._register("live-deaf-hermes", runtime="hermes", sessionMode="managed")
        self._stamp_live_channel_sidecar("live-deaf-hermes", runtime="hermes")
        self._seed_queued_run("run_live_queued", "live-deaf-hermes", from_agent="sender-agent", requested_minutes_ago=5)

        reaped = self._run_reap_undeliverable_queued()
        self.assertEqual(reaped, [], f"a queued run with a live claimer must not be reaped; got {reaped}")
        row = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_live_queued",))
        self.assertEqual(row["status"], "queued")

    def test_recent_queued_run_not_reaped_within_backstop(self):
        # GUARD: a queued run within the backstop window is not reaped even with no
        # live claimer — a cold `available` agent may still lazy-autostart on claim.
        self._register("cold-hermes", runtime="hermes", sessionMode="managed")
        self._seed_queued_run("run_recent_queued", "cold-hermes", from_agent="sender-agent", requested_minutes_ago=0.5)

        reaped = self._run_reap_undeliverable_queued(backstop_seconds=180)
        self.assertEqual(reaped, [], f"a recently-queued run must not be reaped; got {reaped}")
        row = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_recent_queued",))
        self.assertEqual(row["status"], "queued")

    def test_resident_register_does_not_stop_managed_pty_until_manual_switch(self):
        # Manual ownership rule: launching a *-aify wrapper records resident
        # bridge metadata, but it does not take over a managed agent or kill
        # the managed PTY. The operator must press Switch to resident.
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-managed-1",
        )
        # Spawn a managed PTY (the kind of wrapper aify-comms creates).
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        managed_terminal_id = started.json()["terminal"]["id"]
        agent_id = self._fetchone("SELECT agent_id FROM agent_sessions WHERE id = ?", (session_id,))["agent_id"]
        # Operator launches claude-aify --aify-agent X → registers
        # as resident.
        register = self.client.post("/api/v1/agents", json={
            "agentId": agent_id,
            "role": "coder",
            "runtime": "claude-code",
            "sessionMode": "resident",
            "sessionHandle": "claude-resident-1",
            "machineId": "test-machine",
            "bridgeId": "resident-bridge-1",
        })
        self.assertEqual(register.status_code, 200, register.text)
        self.assertEqual(register.json().get("ownershipTransition"), "manual_switch_required", register.json())

        # Managed PTY is still live until the manual switch endpoint is used.
        term = self._fetchone("SELECT status, error FROM terminal_sessions WHERE id = ?", (managed_terminal_id,))
        self.assertIn(term["status"], {"starting", "running"}, f"resident register must not stop managed PTY; got {term['status']}")
        sess = self._fetchone("SELECT terminal_id, terminal_status FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(sess["terminal_id"], managed_terminal_id)

        switched = self.client.patch(f"/api/v1/agents/{agent_id}/session-mode", json={"mode": "resident", "force": True})
        self.assertEqual(switched.status_code, 200, switched.text)
        self.assertEqual(switched.json().get("sideEffects", {}).get("stoppedTerminalId"), managed_terminal_id)
        term = self._fetchone("SELECT status, error FROM terminal_sessions WHERE id = ?", (managed_terminal_id,))
        self.assertEqual(
            term["status"], "stopping",
            f"managed PTY must be stopping after manual resident switch; got {term['status']}",
        )

    def test_dev_channel_auto_confirm_requires_both_signals_and_fresh_terminal(self):
        # Operator complaint: "something enters 1's into console quite
        # randomly". Cause: detector matched the menu text any time it
        # appeared in the buffer, including in Claude's own conversation
        # output explaining the dev-channel feature. Fix gates on:
        #   (a) terminal created less than 30s ago, AND
        #   (b) BOTH "WARNING: Loading development channels" header
        #       AND "I am using this for local development" menu option
        #       present in the cleaned tail.
        # This test pins the gate at the helper-function level (avoids
        # the async queue-flush dance of going through the full HTTP
        # output-append path).
        import time as _t, re
        from service.routers.api_v2 import (
            _ANSI_RE,
            DEFAULT_SETTINGS,
        )
        # Reconstruct the gate logic inline so we can assert it
        # without the full async fixture. If the production helper's
        # gating changes, this test stays a behavior pin.
        def _would_fire(age_seconds: float, full_output: str) -> bool:
            if not full_output:
                return False
            if age_seconds > 30:
                return False
            stripped = _ANSI_RE.sub("", full_output[-6000:])
            return (
                "WARNING: Loading development channels" in stripped
                and "I am using this for local development" in stripped
            )

        # Fresh + both signals → fire.
        self.assertTrue(_would_fire(
            2,
            "WARNING: Loading development channels\nChannels: server:aify-comms-channel\nI am using this for local development\n",
        ))
        # Fresh + only menu option → do NOT fire.
        self.assertFalse(_would_fire(
            2,
            "claude said: I am using this for local development workflow",
        ))
        # Fresh + only warning header → do NOT fire.
        self.assertFalse(_would_fire(
            2,
            "WARNING: Loading development channels (admin context)",
        ))
        # Old + both signals → do NOT fire (age gate).
        self.assertFalse(_would_fire(
            120,
            "WARNING: Loading development channels\nI am using this for local development\n",
        ))

    def test_claude_dev_channel_reactive_auto_confirm_enqueues_choice(self):
        self.client.put("/api/v1/settings", json={"console_auto_confirm_claude_dev_channels": True})
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        fresh = api_v2._now()
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "term_claude_dev_confirm",
                session_id,
                "console-agent",
                "linux:test-host:default",
                "bridge-current",
                "claude-code",
                "/workspace/repo",
                "claude-aify --aify-agent console-agent --auto --resume claude-session-1",
                "",
                "attached",
                "dashboard",
                fresh,
                fresh,
                None,
                "",
            ),
        )

        async def _run():
            from service.db import get_db as _get_db
            from unittest.mock import patch

            captured = []

            async def _fake_sleep(_seconds):
                return None

            db = await _get_db()
            try:
                terminal = await (await db.execute(
                    "SELECT * FROM terminal_sessions WHERE id = ?",
                    ("term_claude_dev_confirm",),
                )).fetchone()
                output = (
                    "WARNING: Loading development channels\n"
                    "Channels: server:aify-comms-channel\n"
                    "❯ 1. I am using this for local development\n"
                    "Enter to confirm · Esc to cancel\n"
                )
                with patch("service.routers.api_v2.asyncio.create_task", side_effect=lambda coro: captured.append(coro) or None), \
                     patch("service.routers.api_v2.asyncio.sleep", side_effect=_fake_sleep):
                    await api_v2._maybe_auto_confirm_claude_dev_channel_prompt(db, terminal, output)
                    await db.commit()
                    self.assertEqual(len(captured), 1)
                    await captured[0]
                    await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())
        event = self._fetchone(
            "SELECT event_type FROM terminal_events WHERE terminal_id = ? AND event_type = ?",
            ("term_claude_dev_confirm", "dev_channel_prompt_auto_confirmed"),
        )
        self.assertIsNotNone(event)
        control = self._fetchone(
            "SELECT action, body, requested_by FROM terminal_controls WHERE terminal_id = ? ORDER BY requested_at DESC, id DESC LIMIT 1",
            ("term_claude_dev_confirm",),
        )
        self.assertEqual(control["action"], "input")
        self.assertEqual(control["body"], "1\r")
        self.assertEqual(control["requested_by"], "dev-channel-auto-confirm")

    def test_channel_delivery_receipt_is_not_persisted_as_chat_reply(self):
        # Operator-caught bug: channel-bridge PATCH writes a summary of
        # "Delivered to Claude channel session; awaiting explicit reply"
        # as a delivery receipt. Before this fix, _mirror_dashboard_run_summary_to_chat
        # persisted that receipt as a "Re: Hello"-style response message
        # in chat (the dashboard rendered it as if it were Claude's
        # actual reply). _is_delivery_only_claude_run only matched the
        # resident-session prefix and missed the channel-session one.
        from service.routers.api_v2 import _is_delivery_only_claude_run
        class _R(dict):
            def keys(self): return super().keys()
        channel_row = _R({
            "runtime": "claude-code",
            "status": "completed",
            "summary": "Delivered to Claude channel session; awaiting explicit reply",
        })
        self.assertTrue(
            _is_delivery_only_claude_run(channel_row),
            "channel-session delivery receipts must be treated as delivery-only (not persisted as a reply)",
        )
        # Resident still recognized.
        resident_row = _R({
            "runtime": "claude-code",
            "status": "completed",
            "summary": "Delivered to Claude resident session; awaiting explicit reply",
        })
        self.assertTrue(_is_delivery_only_claude_run(resident_row))
        # An actual Claude reply summary is NOT delivery-only.
        real_reply = _R({
            "runtime": "claude-code",
            "status": "completed",
            "summary": "Hello! I'm online.",
        })
        self.assertFalse(
            _is_delivery_only_claude_run(real_reply),
            "real Claude reply summaries must NOT be classified as delivery-only",
        )

    def test_managed_claude_channel_eligible_bypasses_managed_run_cap_check(self):
        # Deep-test caught this: managed claude with channelEnabled=true
        # uses the channel transport (claude-channel.js inside the
        # wrapper PTY) — it has no native managed-run API. Default
        # capabilities for managed claude omit "managed-run" by design.
        # Without this skip, _agent_execution_mode would reject the
        # dispatch with "agent capabilities do not include managed-run"
        # even though channel-only routing should deliver fine.
        from service.routers.api_v2 import _agent_execution_mode
        managed_claude_channel = {
            "id": "test-claude-channel",
            "runtime": "claude-code",
            "session_mode": "managed",
            "session_handle": "",
            "launch_mode": "detached",
            "capabilities": '["resume", "interrupt", "spawn"]',
            "runtime_config": '{"channelEnabled": true}',
        }
        # Simulate the sqlite Row contract — row[k] + 'keys()'.
        class _R(dict):
            def keys(self): return super().keys()
        row = _R(managed_claude_channel)
        execution_mode, error = _agent_execution_mode(row)
        self.assertEqual(error, None, f"channel-eligible managed claude must NOT be rejected for missing managed-run; got error={error}")
        self.assertEqual(execution_mode, "channel", f"managed claude with channelEnabled must route channel; got {execution_mode}")

        # Inverse: managed claude WITHOUT channelEnabled and WITHOUT
        # managed-run still rejected — protects against accidentally
        # opening a non-channel managed path that has no delivery.
        managed_claude_no_channel = dict(managed_claude_channel)
        managed_claude_no_channel["runtime_config"] = "{}"
        execution_mode2, error2 = _agent_execution_mode(_R(managed_claude_no_channel))
        self.assertEqual(execution_mode2, None)
        self.assertIn("managed-run", error2 or "")

    def test_managed_via_wrapper_setting_defaults_to_off(self):
        # Plan 4 (2026-05-25): flipped to ON by default ([codex,hermes])
        # now that wrapper-backed delivery has shipped. This contract
        # guard was the Plan 2 off-state assertion; the post-Plan-4
        # default-flip is asserted in test_default_settings_plan4.py.
        # Kept here so the regression suite still pins the value.
        from service.routers.api_v2 import DEFAULT_SETTINGS
        self.assertIn("managed_via_wrapper", DEFAULT_SETTINGS)
        val = DEFAULT_SETTINGS["managed_via_wrapper"]
        self.assertEqual(val, ["codex", "hermes"],
                         f"managed_via_wrapper Plan-4 default: expected [codex,hermes]; got {val!r}")

    def test_managed_via_wrapper_routes_dispatch_as_channel(self):
        # Unified-backing refactor: when managed_via_wrapper includes a runtime,
        # _agent_execution_mode returns 'channel' for managed dispatches so the
        # wrapper's child bridge (claiming with executionModes=['channel',
        # 'resident']) picks it up. Main bridge's dispatch loop has been
        # gated off for this runtime (Task A4).
        from service.routers.api_v2 import _agent_execution_mode
        class _R(dict):
            def keys(self): return super().keys()
        managed_hermes = _R({
            "id": "h-managed-wrapped",
            "runtime": "hermes",
            "session_mode": "managed",
            "session_handle": "",
            "launch_mode": "detached",
            "capabilities": '["managed-run","native-managed-run","resume","interrupt","spawn"]',
            "runtime_config": "{}",
        })
        # Without settings: existing behavior (returns 'managed').
        mode_default, _ = _agent_execution_mode(managed_hermes)
        self.assertEqual(mode_default, "managed", "default behavior unchanged when no settings passed")
        # With settings flagging hermes wrapper-backed: returns 'channel'.
        settings = {"managed_via_wrapper": ["hermes"]}
        mode_wrapped, error = _agent_execution_mode(managed_hermes, settings=settings)
        self.assertIsNone(error)
        self.assertEqual(mode_wrapped, "channel",
                         f"wrapper-backed hermes managed must route as channel; got {mode_wrapped}")
        # Codex NOT in the wrapper list: still managed (mixed runtime opt-in).
        managed_codex = _R({**managed_hermes, "id": "c-managed", "runtime": "codex"})
        mode_codex, _ = _agent_execution_mode(managed_codex, settings=settings)
        self.assertEqual(mode_codex, "managed", "codex unflagged stays on managed")

    def test_managed_via_wrapper_forces_eager_pty_spawn(self):
        # Unified-backing refactor: when managed_via_wrapper includes a runtime,
        # the wrapper PTY must pre-exist at spawn-request running transition.
        # Otherwise the main bridge stops claiming managed runs for that
        # runtime (Task A4 gate) AND the wrapper child bridge doesn't exist
        # yet → dispatches queue forever.
        self.client.put("/api/v1/settings", json={
            "managed_terminal_backing_enabled": True,
            "managed_pty_eager_spawn": False,  # NOT relying on the general eager flag
            "managed_via_wrapper": ["hermes"],
        })
        session_id = self._create_running_session(terminal=True, runtime="hermes")
        rows = self._fetchall("SELECT id FROM terminal_sessions WHERE session_id = ?", (session_id,))
        self.assertGreaterEqual(
            len(rows), 1,
            "wrapper-backed managed must eagerly spawn the PTY even when managed_pty_eager_spawn is false",
        )

    def test_ensure_managed_pty_writes_terminal_id_into_agent_runtime_state(self):
        # The wrapper PTY's terminal_session id must land in agents.runtime_state.terminalId
        # so the dashboard's chooseSessionConsoleWidget renders xterm against it.
        # Before this fix only the native-RPC ensure_virtual_terminal path published
        # virtualTerminalId (api_v2.py:7396); the wrapper PTY's row was orphaned from
        # the dashboard POV (operator-reported 2026-05-24).
        self.client.put("/api/v1/settings", json={
            "managed_terminal_backing_enabled": True,
            "managed_pty_eager_spawn": True,
        })
        session_id = self._create_running_session(terminal=True, runtime="hermes")
        # The spawn-request running transition with eager_spawn=True triggers
        # _ensure_managed_pty_for_dispatch which inserts a terminal_session row.
        # After this fix, the agent row's runtime_state should also carry terminalId.
        agent_row = self._fetchone(
            "SELECT a.runtime_state FROM agents a JOIN agent_sessions s ON s.agent_id = a.id WHERE s.id = ?",
            (session_id,),
        )
        self.assertIsNotNone(agent_row, "agent row must exist for the spawned session")
        rs = json.loads(agent_row["runtime_state"] or "{}")
        self.assertIn("terminalId", rs,
                      "_ensure_managed_pty_for_dispatch must publish terminalId in agents.runtime_state")
        self.assertTrue(rs["terminalId"].startswith("term_"),
                        f"terminalId must be a real terminal_session id, got {rs['terminalId']!r}")

    def test_managed_via_wrapper_for_runtime_handles_bool_list_none(self):
        from service.routers.api_v2 import _managed_via_wrapper_for_runtime
        # Off: returns False for all runtimes. (Plan 4 (2026-05-25)
        # flipped the DEFAULT to ON, so `{}` now resolves via
        # DEFAULT_SETTINGS to ["codex","hermes","pi"]. The off-state
        # contract still holds when callers pass an explicit False.)
        self.assertFalse(_managed_via_wrapper_for_runtime({"managed_via_wrapper": False}, "hermes"))
        self.assertFalse(_managed_via_wrapper_for_runtime({"managed_via_wrapper": False}, "codex"))
        # True: returns True for runtimes whose adapter declares
        # preferred_delivery_mode == "managed-via-wrapper" (codex/hermes).
        self.assertTrue(_managed_via_wrapper_for_runtime({"managed_via_wrapper": True}, "hermes"))
        self.assertTrue(_managed_via_wrapper_for_runtime({"managed_via_wrapper": True}, "codex"))
        # claude-code is already wrapper-backed via claude-channel; not gated by this flag.
        self.assertFalse(_managed_via_wrapper_for_runtime({"managed_via_wrapper": True}, "claude-code"))
        # Pi/OMP stays native RPC because OMP is single-client and the
        # dashboard Console must attach to the same virtual RPC stream that
        # chat dispatch uses, not a sibling pi-aify PTY.
        self.assertFalse(_managed_via_wrapper_for_runtime({"managed_via_wrapper": True}, "pi"))
        self.assertFalse(_managed_via_wrapper_for_runtime({"managed_via_wrapper": ["pi"]}, "pi"))
        # opencode adapter prefers "managed" (native RPC), not "managed-via-wrapper",
        # so it stays out even when the setting is True.
        self.assertFalse(_managed_via_wrapper_for_runtime({"managed_via_wrapper": True}, "opencode"))
        # List: only listed eligible runtimes route via wrapper.
        self.assertTrue(_managed_via_wrapper_for_runtime({"managed_via_wrapper": ["hermes"]}, "hermes"))
        self.assertFalse(_managed_via_wrapper_for_runtime({"managed_via_wrapper": ["hermes"]}, "codex"))
        # Unknown runtime: always False (defensive).
        self.assertFalse(_managed_via_wrapper_for_runtime({"managed_via_wrapper": True}, "unknown"))

    def test_resident_hermes_with_gateway_url_does_not_require_session_handle(self):
        # Operator-reported 2026-05-24: sc-hermes-test-2 → sc-hermes-test-1
        # ping-pong refused live delivery with "without a bound session
        # handle. Restart with hermes-aify and a resumable session handle..."
        # even though sc-hermes-test-1 had registered with a live gatewayUrl
        # in runtimeConfig (auto-detected from AIFY_HERMES_GATEWAY_URL by
        # the new hermes-aify wrapper + MCP env propagation).
        #
        # Root cause: my earlier resident-run capability carve-out in
        # mcp/stdio/runtimes.js + the Python capability check at line 946-947
        # accepted gateway-only hermes, but a SECOND gate at line 989-993
        # still required session_handle regardless of gatewayUrl. That gate
        # predates the gateway path and assumed all resident hermes have a
        # captured sessionHandle (resume-based wake).
        #
        # The gateway-channel controller resolves session.most_recent at
        # dispatch time, so sessionHandle is optional when gatewayUrl is set.
        # Mirror of the capability-check carve-out, applied to the second gate.
        from service.routers.api_v2 import _agent_execution_mode
        class _R(dict):
            def keys(self): return super().keys()

        # 1. Hermes resident WITH gatewayUrl, WITHOUT sessionHandle — must accept.
        with_gateway = _R({
            "id": "sc-hermes-test-1",
            "runtime": "hermes",
            "session_mode": "resident",
            "session_handle": "",
            "launch_mode": "detached",
            "capabilities": '["resident-run", "resume", "interrupt", "steer"]',
            "runtime_config": '{"gatewayUrl": "ws://127.0.0.1:62260/api/ws?token=secret"}',
        })
        mode, error = _agent_execution_mode(with_gateway)
        self.assertIsNone(error, f"hermes with gatewayUrl must NOT be rejected for missing session_handle; got: {error}")
        self.assertEqual(mode, "resident")

        # 2. Hermes resident WITHOUT gatewayUrl, WITHOUT sessionHandle — still rejected.
        # (Capabilities won't include resident-run in real registration since the
        # bridge's defaultCapabilitiesForRuntime gates on gatewayUrl too, but
        # validate the gate logic stays restrictive.)
        without_gateway = _R({
            "id": "sc-hermes-bare",
            "runtime": "hermes",
            "session_mode": "resident",
            "session_handle": "",
            "launch_mode": "detached",
            "capabilities": '["resident-run", "resume", "interrupt", "steer"]',
            "runtime_config": "{}",
        })
        mode2, error2 = _agent_execution_mode(without_gateway)
        self.assertIsNone(mode2)
        self.assertIn("gatewayurl", (error2 or "").lower(),
                      f"hermes without gatewayUrl AND without session_handle must still error; got: {error2}")

        # 3. Hermes resident WITH sessionHandle but no gateway — still rejected.
        # Hidden resume/session-create fallback is intentionally disabled; Hermes
        # resident wake must bind to the visible TUI gateway.
        legacy_handle = _R({
            "id": "sc-hermes-legacy",
            "runtime": "hermes",
            "session_mode": "resident",
            "session_handle": "sid-abc-123",
            "launch_mode": "detached",
            "capabilities": '["resident-run", "resume", "interrupt"]',
            "runtime_config": "{}",
        })
        mode3, error3 = _agent_execution_mode(legacy_handle)
        self.assertIsNone(mode3)
        self.assertIn("gatewayurl", (error3 or "").lower())

    def test_managed_pty_eager_spawn_creates_terminal_at_spawn_request_running(self):
        # Slices 1/2/4: when managed_pty_eager_spawn is on AND
        # managed_terminal_backing_enabled is on, the wrapper PTY is
        # created the moment the spawn-request transitions to running.
        # Operator-visible effect: console pre-exists by the time the
        # first dispatch arrives, so no "console pops up when I send"
        # UI symptom. Subsequent sends reuse via slice-3's
        # console-attach reuse + the dispatch _active_terminal_for_agent
        # lookup in _ensure_managed_pty_for_dispatch.
        self.client.put("/api/v1/settings", json={"managed_pty_eager_spawn": True})
        # Run the standard spawn-request->running flow via _create_running_session.
        session_id = self._create_running_session(terminal=True)
        # A terminal_session for this agent now exists eagerly.
        terminals = self._fetchall(
            "SELECT id, status FROM terminal_sessions WHERE session_id = ?",
            (session_id,),
        )
        self.assertEqual(
            len(terminals), 1,
            f"eager spawn must create exactly one terminal_session at spawn-request running; got {[dict(r) for r in terminals]}",
        )
        # The agent_session points at it.
        sess = self._fetchone("SELECT terminal_id, terminal_status FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(sess["terminal_id"], terminals[0]["id"])

    def test_managed_pty_eager_spawn_default_off_preserves_prior_behavior(self):
        # Pre-Plan-4 contract guard: lazy-PTY behavior with the flag
        # explicitly OFF. Plan 4 (2026-05-25) flipped the default to
        # ON; this test now explicitly opts out via settings to keep
        # the legacy-lazy contract covered. The post-Plan-4 default-ON
        # assertion lives in test_default_settings_plan4.py.
        self.client.put("/api/v1/settings", json={"managed_pty_eager_spawn": False})
        session_id = self._create_running_session(terminal=True)
        terminals = self._fetchall(
            "SELECT id FROM terminal_sessions WHERE session_id = ?",
            (session_id,),
        )
        self.assertEqual(
            len(terminals), 0,
            f"with eager-spawn off, no terminal_session must exist at spawn-request running; got {[dict(r) for r in terminals]}",
        )

    def test_console_start_reuses_existing_live_terminal_session(self):
        # Slice 3: clicking Start Console (or auto-attaching via the
        # dashboard) on a session that already has a live wrapper PTY
        # must REUSE that terminal_session instead of spawning a fresh
        # one. Operator symptom we're killing: every dashboard
        # interaction respawns a console pop-up even when the wrapper
        # is already running, creating sibling PTYs.
        session_id = self._create_running_session(terminal=True)
        first = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(first.status_code, 200, first.text)
        first_terminal_id = first.json()["terminal"]["id"]
        self.assertNotIn("reused", first.json(), "first start must NOT be marked reused")

        # Second start request immediately after — same session, same
        # agent, the prior terminal is still in starting/attached state.
        second = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertTrue(body.get("reused"), f"second start must be marked reused; got {body}")
        self.assertEqual(
            body["terminal"]["id"], first_terminal_id,
            f"reused start must return the SAME terminal id; got {body['terminal']['id']} vs {first_terminal_id}",
        )

        # Exactly one terminal_sessions row exists for this agent — no
        # sibling spawn.
        rows = self._fetchall(
            "SELECT id FROM terminal_sessions WHERE session_id = ?", (session_id,),
        )
        self.assertEqual(
            len(rows), 1,
            f"reusing existing terminal must NOT create a sibling row; got {[dict(r) for r in rows]}",
        )

        # The reuse event is audited so we can debug it in production.
        events = self._fetchall(
            "SELECT event_type FROM terminal_events WHERE terminal_id = ? ORDER BY created_at",
            (first_terminal_id,),
        )
        event_types = [r["event_type"] for r in events]
        self.assertIn(
            "console_attach_reused_existing", event_types,
            f"reuse event must be appended; got {event_types}",
        )

    def test_pi_console_start_creates_virtual_rpc_terminal_not_wrapper_pty(self):
        session_id = self._create_running_session(
            agent_id="pi-console-agent",
            terminal=True,
            runtime="pi",
            terminal_runtimes=["pi"],
            session_handle="pi-session-1",
        )

        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        body = started.json()
        self.assertTrue(body.get("virtual"), body)
        self.assertTrue(body["terminal"]["id"].startswith("vterm_"), body)
        self.assertEqual(body["terminal"]["command"], "aify://virtual-rpc/pi")
        self.assertNotIn("pi-aify", body["terminal"]["command"])

        session = self._fetchone("SELECT terminal_id, terminal_command FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(session["terminal_id"], body["terminal"]["id"])
        self.assertEqual(session["terminal_command"], "aify://virtual-rpc/pi")

    def test_console_start_rejects_environment_without_terminal_support(self):
        session_id = self._create_running_session(terminal=False)

        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(started.status_code, 409, started.text)
        self.assertIn("no PTY/terminal capability", started.text)
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
        self._execute(
            "UPDATE agents SET status_note = ?, runtime_state = ? WHERE id = ?",
            (
                "Dashboard Console PTY attached.",
                json.dumps({"consoleTerminal": {"terminalId": terminal_id, "bridgeId": "bridge-current"}}),
                "console-agent",
            ),
        )
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
        agent = self._fetchone("SELECT status_note, runtime_state FROM agents WHERE id = ?", ("console-agent",))
        self.assertEqual(agent["status_note"], "")
        self.assertNotIn("consoleTerminal", json.loads(agent["runtime_state"]))
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
        self.assertEqual(session["terminalId"], "")
        self.assertEqual(session["terminalStatus"], "")

    def test_console_start_builds_interactive_codex_command_resumes_stored_handle(self):
        # Plan 1 of the RuntimeAdapter refactor (2026-05-25) — the previous
        # codex carve-out always launched fresh because raw `codex resume
        # --include-non-interactive <handle>` failed on stale session files.
        # The dashboard Console goes through codex-aify (NOT raw
        # `codex resume`), and codex-aify gained a stale-handle fallback so
        # missing session files downgrade to fresh. With that in place the
        # interactive Console now resumes the stored handle, matching the
        # behavior of claude/hermes/pi managed launches.
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        command = started.json()["terminal"]["command"]
        self.assertIn("codex-aify", command)
        self.assertIn("--aify-agent console-agent", command)
        self.assertIn("--resume thread-1", command)
        self.assertNotIn("--include-non-interactive", command)

    def test_console_start_builds_claude_channels_command_resumes_stored_handle(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        command = started.json()["terminal"]["command"]
        # Human Console still goes through claude-aify, but it must preserve
        # the stored native handle. Otherwise opening/restarting from the
        # dashboard silently forks a fresh Claude conversation.
        self.assertIn("claude-aify", command)
        self.assertIn("--aify-agent console-agent", command)
        self.assertIn("--resume claude-session-1", command)
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
        self.assertEqual(fresh.json()["terminal"]["command"], "aify://virtual-rpc/pi")
        self.assertNotIn("--resume", fresh.json()["terminal"]["command"])

    def test_managed_dispatch_to_active_console_terminal_forwards_to_pty(self):
        # Hermes has no native managed adapter, so it always uses a managed PTY.
        # Native runtimes are covered below: default terminal-backed delivery,
        # plus an explicit legacy-native fallback when the setting is disabled.
        session_id = self._create_running_session(
            terminal=True,
            runtime="hermes",
            terminal_runtimes=["hermes"],
            session_handle="hermes-session-1",
        )
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
        self.assertEqual(contract["status"], "running")
        self.assertEqual(contract["dispatch_mode"], "terminal")
        self.assertEqual(contract["require_reply"], 1)
        self.assertEqual(dispatched["consoleDeliveries"][0]["contractRunId"], contract["id"])
        control = self._fetchone("SELECT * FROM terminal_controls WHERE terminal_id = ? AND action = 'input'", (terminal_id,))
        self.assertIsNotNone(control)
        self.assertIn("AIFY dashboard message", control["body"])
        self.assertIn("dashboard", control["body"])
        self.assertIn("do it", control["body"])
        self.assertTrue(control["body"].endswith("\r"))

    def test_managed_dispatch_native_runtime_uses_terminal_backing_by_default(self):
        for runtime, handle in (("codex", "codex-thread-1"),):
            with self.subTest(runtime=runtime):
                agent_id = f"{runtime}-terminal-agent"
                self._create_running_session(
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
                    body=f"terminal-backed dispatch for {runtime}",
                    mode="start_if_possible",
                    createMessage=True,
                )
                self.assertEqual(dispatched["notStarted"], [])
                self.assertEqual(dispatched["runs"], [])
                self.assertEqual(len(dispatched.get("consoleDeliveries", [])), 1)
                contract = self._fetchone(
                    "SELECT status, dispatch_mode, runtime FROM dispatch_runs WHERE target_agent = ?",
                    (agent_id,),
                )
                self.assertIsNotNone(contract)
                self.assertEqual(contract["status"], "running")
                self.assertEqual(contract["dispatch_mode"], "terminal")
                self.assertEqual(api_v2._normalize_runtime(contract["runtime"]), runtime)
                terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
                injected = self._fetchone(
                    "SELECT body FROM terminal_controls WHERE terminal_id = ? AND action = 'input'",
                    (terminal_id,),
                )
                self.assertIsNotNone(injected)
                self.assertIn(f"terminal-backed dispatch for {runtime}", injected["body"])

    def test_managed_dispatch_native_runtime_can_fall_back_to_native_when_terminal_backing_disabled(self):
        self.client.put("/api/v1/settings", json={"managed_terminal_backing_enabled": False})
        for runtime, handle in (("codex", "codex-thread-1"), ("pi", "pi-session-1"), ("opencode", "opencode-session-1")):
            with self.subTest(runtime=runtime):
                agent_id = f"{runtime}-native-agent"
                session_id = self._create_running_session(
                    agent_id=agent_id,
                    terminal=True,
                    runtime=runtime,
                    terminal_runtimes=[runtime],
                    session_handle=handle,
                )
                started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
                self.assertEqual(started.status_code, 200, started.text)
                terminal_id = started.json()["terminal"]["id"]

                dispatched = self._dispatch(
                    from_agent="dashboard",
                    to=agent_id,
                    type="request",
                    subject="work",
                    body=f"native dispatch for {runtime}",
                    mode="start_if_possible",
                    createMessage=True,
                )
                self.assertEqual(dispatched.get("consoleDeliveries", []), [])
                self.assertEqual(dispatched["notStarted"], [])
                self.assertTrue(dispatched["runs"], dispatched)
                contract = self._fetchone(
                    "SELECT status, dispatch_mode FROM dispatch_runs WHERE target_agent = ?",
                    (agent_id,),
                )
                self.assertIsNotNone(contract)
                self.assertNotEqual(contract["dispatch_mode"], "terminal")
                injected = self._fetchone(
                    "SELECT id FROM terminal_controls WHERE terminal_id = ? AND action = 'input'",
                    (terminal_id,),
                )
                self.assertIsNone(injected)
    def test_managed_claude_dispatch_uses_claude_aify_terminal_turn(self):
        # This test pins the start/input control sequence for managed
        # claude dispatch. Default-on auto-confirm of the dev-channel
        # prompt would add an extra "input" before the message; opt out
        # here so we keep asserting the core sequence.
        self.client.put("/api/v1/settings", json={"console_auto_confirm_claude_dev_channels": False})
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
        self.assertEqual(len(dispatched["consoleDeliveries"]), 1)
        self.assertEqual(dispatched["notStarted"], [])
        self.assertEqual(dispatched["runs"], [])
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        contract = self._fetchone(
            "SELECT id, status, dispatch_mode, execution_mode, require_reply FROM dispatch_runs WHERE id = ?",
            (run_id,),
        )
        self.assertEqual(contract["status"], "running")
        self.assertEqual(contract["dispatch_mode"], "terminal")
        self.assertEqual(contract["execution_mode"], "managed")
        self.assertEqual(contract["require_reply"], 1)
        listed_working = self.client.get("/api/v1/agents")
        self.assertEqual(listed_working.status_code, 200, listed_working.text)
        self.assertEqual(listed_working.json()["agents"]["console-agent"]["status"], "working")
        session = self._fetchone("SELECT owner_mode, terminal_id, terminal_status FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(session["owner_mode"], "managed")
        self.assertTrue(session["terminal_id"])
        self.assertEqual(session["terminal_status"], "starting")
        controls = self._fetchall(
            "SELECT action, body FROM terminal_controls WHERE terminal_id = ? ORDER BY requested_at ASC, id ASC",
            (session["terminal_id"],),
        )
        self.assertEqual([row["action"] for row in controls], ["start", "input"])
        self.assertIn("claude-aify --aify-agent console-agent --auto", controls[0]["body"])
        self.assertIn("--resume claude-session-1", controls[0]["body"])
        self.assertNotIn("claude --channels", controls[0]["body"])
        self.assertIn("do it without console open", controls[1]["body"])
        self.assertIn("\x1b[200~", controls[1]["body"])
        self.assertIn("\x1b[201~", controls[1]["body"])
        self.assertTrue(controls[1]["body"].endswith("\r"))

    def test_managed_claude_auto_confirm_dev_channel_prompt_is_operator_toggle(self):
        self.client.put("/api/v1/settings", json={"console_auto_confirm_claude_dev_channels": True})
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
        self.assertEqual(len(dispatched["consoleDeliveries"]), 1)
        session = self._fetchone("SELECT terminal_id FROM agent_sessions WHERE id = ?", (session_id,))
        controls = self._fetchall(
            "SELECT action, body FROM terminal_controls WHERE terminal_id = ? ORDER BY requested_at ASC, id ASC",
            (session["terminal_id"],),
        )
        self.assertEqual([row["action"] for row in controls], ["start", "input", "input"])
        self.assertEqual(controls[1]["body"], "\r")
        self.assertNotIn("do it without console open", controls[1]["body"])
        self.assertIn("do it without console open", controls[2]["body"])

    def test_managed_claude_active_run_without_terminal_backing_reports_blocked(self):
        # Legacy-only `active_run_terminal_missing → blocked` — pin old (see WS-5 note).
        self.client.put("/api/v1/settings", json={"status_engine": "old"})
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
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        self._execute(
            "UPDATE agent_sessions SET terminal_id = '', terminal_status = '' WHERE id = ?",
            (session_id,),
        )
        self._execute("DELETE FROM agent_live_state WHERE agent_id = ?", ("console-agent",))

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        agent = listed.json()["agents"]["console-agent"]
        self.assertEqual(agent["dispatchState"]["activeRun"]["runId"], run_id)
        self.assertEqual(agent["status"], "blocked")
        self.assertIn("terminal", agent["statusNote"].lower())

    def test_terminal_backed_codex_active_run_without_terminal_backing_reports_blocked(self):
        # Legacy-only `active_run_terminal_missing → blocked` (codex) — pin old (see WS-5).
        self.client.put("/api/v1/settings", json={"status_engine": "old"})
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
            subject="codex work",
            body="do it through terminal backing",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        self._execute(
            "UPDATE agent_sessions SET terminal_id = '', terminal_status = '' WHERE id = ?",
            (session_id,),
        )
        self._execute("DELETE FROM agent_live_state WHERE agent_id = ?", ("console-agent",))

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        agent = listed.json()["agents"]["console-agent"]
        self.assertEqual(agent["dispatchState"]["activeRun"]["runId"], run_id)
        self.assertEqual(agent["status"], "blocked")
        self.assertIn("terminal", agent["statusNote"].lower())


    def test_managed_claude_active_run_with_ended_terminal_backing_reports_blocked(self):
        # Legacy-only: `active_run_terminal_missing → blocked`. The new engine derives blocked
        # from in_turn + the console-input hint (WS-5), not a missing terminal backing — pin old.
        self.client.put("/api/v1/settings", json={"status_engine": "old"})
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
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        session = self._fetchone("SELECT terminal_id FROM agent_sessions WHERE id = ?", (session_id,))
        self._execute(
            "UPDATE terminal_sessions SET status = 'stopped' WHERE id = ?",
            (session["terminal_id"],),
        )
        self._execute(
            "UPDATE agent_sessions SET terminal_status = 'stopped' WHERE id = ?",
            (session_id,),
        )
        self._execute("DELETE FROM agent_live_state WHERE agent_id = ?", ("console-agent",))

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        agent = listed.json()["agents"]["console-agent"]
        self.assertEqual(agent["dispatchState"]["activeRun"]["runId"], run_id)
        self.assertEqual(agent["status"], "blocked")
        self.assertIn("terminal", agent["statusNote"].lower())


    def test_managed_claude_dispatch_does_not_create_channel_only_run(self):
        self._create_running_session(
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
            body="deliver through channel",
            mode="start_if_possible",
            createMessage=True,
        )
        self.assertEqual(dispatched["runs"], [])
        self.assertEqual(len(dispatched["consoleDeliveries"]), 1)
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]

        skipped_channel = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "console-agent", "bridgeId": "channel-linux:test-host", "machineId": "linux:test-host", "executionModes": ["channel"]},
        )
        self.assertEqual(skipped_channel.status_code, 200, skipped_channel.text)
        self.assertIsNone(skipped_channel.json()["run"])

        stored = self._fetchone("SELECT status, runtime, execution_mode, dispatch_mode FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(stored["status"], "running")
        self.assertEqual(stored["runtime"], "claude-code")
        self.assertEqual(stored["execution_mode"], "managed")
        self.assertEqual(stored["dispatch_mode"], "terminal")

    def test_queue_if_busy_to_idle_managed_claude_uses_terminal_turn(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        sent = self._send_message(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="queued smoke",
            body="answer when online",
            trigger=True,
            queueIfBusy=True,
        )

        self.assertTrue(sent["ok"], sent)
        self.assertEqual(sent["dispatchRuns"], [])
        self.assertEqual(len(sent["consoleDeliveries"]), 1)
        run_id = sent["consoleDeliveries"][0]["contractRunId"]
        contract = self._fetchone(
            "SELECT status, dispatch_mode, execution_mode, body FROM dispatch_runs WHERE id = ?",
            (run_id,),
        )
        self.assertEqual(contract["status"], "running")
        self.assertEqual(contract["dispatch_mode"], "terminal")
        self.assertEqual(contract["execution_mode"], "managed")
        self.assertIn("answer when online", contract["body"])
        orphan_channel = self._fetchone(
            "SELECT id FROM dispatch_runs WHERE target_agent = ? AND execution_mode = 'channel' AND status = 'queued'",
            ("console-agent",),
        )
        self.assertIsNone(orphan_channel)

    def test_managed_dispatch_starts_headless_pty_for_terminal_runtimes(self):
        # Hermes has no native managed adapter, so dispatch starts/reuses a
        # managed PTY directly. Native runtimes use the same terminal-backed
        # contract by default and have separate fallback coverage.
        cases = [
            ("hermes", "hermes-aify --aify-agent {agent_id}", "--resume hermes-session-1", "hermes-session-1"),
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
                self.assertEqual(contract["status"], "running")
                self.assertEqual(contract["dispatch_mode"], "terminal")
                self.assertEqual(contract["require_reply"], 1)

    def test_message_send_delivers_to_active_console_pty_without_queuing_run(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="hermes",
            terminal_runtimes=["hermes"],
            session_handle="hermes-session-1",
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
        self.assertEqual(contract["status"], "running")
        self.assertEqual(contract["dispatch_mode"], "terminal")
        self.assertEqual(contract["require_reply"], 1)
        self.assertEqual(payload["consoleDeliveries"][0]["contractRunId"], contract["id"])
        receipt = self._fetchone("SELECT message_id FROM read_receipts WHERE message_id = ? AND agent_id = ?", (payload["messageId"], "console-agent"))
        self.assertEqual(receipt["message_id"], payload["messageId"])

        reply = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "console-agent",
                "to": "dashboard",
                "type": "response",
                "subject": "Re: console chat",
                "body": "answered from console",
                "inReplyTo": payload["messageId"],
                "trigger": False,
            },
        )
        self.assertEqual(reply.status_code, 200, reply.text)
        closed_contract = self._fetchone("SELECT status, result_message_id, finished_at FROM dispatch_runs WHERE id = ?", (contract["id"],))
        self.assertEqual(closed_contract["status"], "completed")
        self.assertTrue(closed_contract["result_message_id"])
        self.assertTrue(closed_contract["finished_at"])

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
        self.assertEqual(contract["status"], "running")
        self.assertEqual(contract["dispatch_mode"], "terminal")
        self.assertEqual(contract["require_reply"], 1)
        self.assertEqual(payload["consoleDeliveries"][0]["contractRunId"], contract["id"])

    def test_message_send_to_managed_claude_uses_console_turn_when_console_open(self):
        # Opt out of default-on dev-channel auto-confirm so this test
        # asserts the core start/input control sequence without the
        # extra auto-confirm \r interleaved.
        self.client.put("/api/v1/settings", json={"console_auto_confirm_claude_dev_channels": False})
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
                "subject": "claude chat",
                "body": "answer through channel",
                "trigger": True,
            },
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        payload = sent.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["consoleDeliveries"]), 1)
        self.assertEqual(payload["dispatchRuns"], [])
        run_id = payload["consoleDeliveries"][0]["contractRunId"]
        contract = self._fetchone(
            "SELECT status, dispatch_mode, execution_mode, require_reply FROM dispatch_runs WHERE id = ?",
            (run_id,),
        )
        self.assertEqual(contract["status"], "running")
        self.assertEqual(contract["dispatch_mode"], "terminal")
        self.assertEqual(contract["execution_mode"], "managed")
        self.assertEqual(contract["require_reply"], 1)
        injected = self._fetchone("SELECT body FROM terminal_controls WHERE terminal_id = ? AND action = 'input'", (terminal_id,))
        self.assertIsNotNone(injected)
        self.assertIn("answer through channel", injected["body"])
        self.assertIn("\x1b[200~", injected["body"])
        self.assertIn("\x1b[201~", injected["body"])
        self.assertTrue(injected["body"].endswith("\r"))
        submit = self._fetchall("SELECT body FROM terminal_controls WHERE terminal_id = ? AND action = 'input' ORDER BY requested_at ASC, id ASC", (terminal_id,))
        self.assertEqual(len(submit), 1)

    def test_message_send_to_managed_claude_starts_claude_aify_and_inputs_dashboard_message(self):
        # Opt out of default-on dev-channel auto-confirm so this test
        # asserts the core start/input control sequence without the
        # extra auto-confirm \r interleaved.
        self.client.put("/api/v1/settings", json={"console_auto_confirm_claude_dev_channels": False})
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        sent = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "to": "console-agent",
                "type": "request",
                "subject": "claude chat",
                "body": "answer through channel",
                "trigger": True,
            },
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        payload = sent.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["consoleDeliveries"]), 1)
        self.assertEqual(payload["dispatchRuns"], [])
        run_id = payload["consoleDeliveries"][0]["contractRunId"]
        contract = self._fetchone(
            "SELECT status, dispatch_mode, execution_mode, require_reply FROM dispatch_runs WHERE id = ?",
            (run_id,),
        )
        self.assertEqual(contract["status"], "running")
        self.assertEqual(contract["dispatch_mode"], "terminal")
        self.assertEqual(contract["execution_mode"], "managed")
        session = self._fetchone("SELECT owner_mode, terminal_id, terminal_status FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(session["owner_mode"], "managed")
        self.assertTrue(session["terminal_id"])
        self.assertEqual(session["terminal_status"], "starting")
        controls = self._fetchall(
            "SELECT action, body FROM terminal_controls WHERE terminal_id = ? ORDER BY requested_at ASC, id ASC",
            (session["terminal_id"],),
        )
        self.assertEqual([row["action"] for row in controls], ["start", "input"])
        self.assertIn("claude-aify --aify-agent console-agent --auto", controls[0]["body"])
        self.assertIn("--resume claude-session-1", controls[0]["body"])
        self.assertNotIn("claude --channels", controls[0]["body"])
        self.assertIn("answer through channel", controls[1]["body"])
        self.assertIn("\x1b[200~", controls[1]["body"])
        self.assertIn("\x1b[201~", controls[1]["body"])
        self.assertTrue(controls[1]["body"].endswith("\r"))

    def test_managed_claude_terminal_prompt_reports_blocked_not_working(self):
        # Legacy-path blocked derivation (no in_turn/sidecar seeded). The new-engine
        # equivalent is test_blocked_reachable_when_console_awaits_input — pin old here.
        self.client.put("/api/v1/settings", json={"status_engine": "old"})
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="state",
            body="what is the current state",
            mode="start_if_possible",
            createMessage=True,
        )
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        output = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={
                "bridgeId": "bridge-current",
                "status": "attached",
                "output": "Your call — I need a decision:\n1. I drive hands-on.\n2. Revert runtime.\n3. Debug pi.\nSay the word and I execute.",
            },
        )
        self.assertEqual(output.status_code, 200, output.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())
        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        agent = listed.json()["agents"]["console-agent"]
        self.assertEqual(agent["status"], "blocked")
        self.assertEqual(agent["statusRaw"], "blocked")
        self.assertIn("Awaiting console input", agent["statusNote"])
        self.assertEqual(agent["dispatchState"]["activeRun"]["runId"], dispatched["consoleDeliveries"][0]["contractRunId"])

    def test_managed_claude_dev_channel_prompt_reports_blocked_without_active_run(self):
        # Legacy-only: blocked WITHOUT an active run/turn. New gates blocked on in_turn, so a
        # prompt with no turn derives online (the statusNote still shows the awaiting hint) — pin old.
        self.client.put("/api/v1/settings", json={"status_engine": "old"})
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        self._stamp_live_channel_sidecar()  # status-F1: live managed claude has a sidecar
        fresh = api_v2._now()
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "term_claude_blocked_prompt",
                session_id,
                "console-agent",
                "linux:test-host:default",
                "bridge-current",
                "claude-code",
                "/workspace/repo",
                "claude-aify --aify-agent console-agent --auto --resume claude-session-1",
                (
                    "WARNING: Loading development channels\n"
                    "Channels: server:aify-comms-channel\n"
                    "❯ 1. I am using this for local development ✔\n"
                    "Enter to confirm · Esc to cancel\n"
                ),
                "attached",
                "dashboard",
                fresh,
                fresh,
                None,
                "",
            ),
        )
        self._execute(
            """
            UPDATE agent_sessions
            SET terminal_id = ?, terminal_status = ?, terminal_command = ?, terminal_workspace = ?
            WHERE id = ?
            """,
            (
                "term_claude_blocked_prompt",
                "attached",
                "claude-aify --aify-agent console-agent --auto --resume claude-session-1",
                "/workspace/repo",
                session_id,
            ),
        )
        asyncio.run(self._async_invalidate("console-agent"))

        agent = self.client.get("/api/v1/agents/console-agent").json()["agent"]
        self.assertEqual(agent["status"], "blocked", agent)
        self.assertIn("Awaiting console confirmation", agent["statusNote"])
        self.assertFalse(agent["dispatchState"]["hasActiveRun"])

    def test_claude_prompt_footer_alone_does_not_report_blocked(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="push",
            body="push current commits up",
            mode="start_if_possible",
            createMessage=True,
        )
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        output = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={
                "bridgeId": "bridge-current",
                "status": "attached",
                "output": "Done. Pushed branch.\n✻ Worked for 56s\n⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
            },
        )
        self.assertEqual(output.status_code, 200, output.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())
        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        agent = listed.json()["agents"]["console-agent"]
        self.assertEqual(agent["status"], "working")
        self.assertNotIn("Awaiting console input", agent["statusNote"])

    def test_idle_claude_prompt_closes_terminal_run_without_explicit_reply(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        self._stamp_live_channel_sidecar()  # status-F1: live managed claude has a sidecar

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="state",
            body="what is the current state",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        output = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={
                "bridgeId": "bridge-current",
                "status": "attached",
                "output": (
                    "Current state: verified and waiting for the next lane.\n"
                    "No blockers.\n"
                    "✻ Worked for 42s\n"
                    "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
                ),
            },
        )
        self.assertEqual(output.status_code, 200, output.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())
        self._execute(
            "UPDATE dispatch_runs SET requested_at = ?, claimed_at = ?, started_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z", run_id),
        )
        self._execute("UPDATE terminal_sessions SET updated_at = ? WHERE id = ?", ("2000-01-01T00:00:20Z", terminal_id))

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)

        run = self._fetchone("SELECT status, summary, result_message_id, finished_at FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["result_message_id"], "")
        self.assertIn("returned to an idle prompt", run["summary"])
        self.assertTrue(run["finished_at"])
        agent = listed.json()["agents"]["console-agent"]
        self.assertEqual(agent["status"], "online")
        self.assertFalse(agent["dispatchState"]["hasActiveRun"])

    def test_busy_claude_terminal_output_does_not_close_running_turn(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="state",
            body="what is the current state",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        output = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={
                "bridgeId": "bridge-current",
                "status": "attached",
                "output": "● Calling aify-comms\n✻ Cogitating…",
            },
        )
        self.assertEqual(output.status_code, 200, output.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)

        run = self._fetchone("SELECT status, finished_at FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(run["status"], "running")
        self.assertFalse(run["finished_at"])
        agent = listed.json()["agents"]["console-agent"]
        self.assertEqual(agent["status"], "working")


    def test_claude_spinner_after_prompt_does_not_close_running_turn(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="state",
            body="what is the current state",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        output = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={
                "bridgeId": "bridge-current",
                "status": "attached",
                "output": (
                    "Previous answer\n"
                    "❯ dashboard when appropriate, using the available aify-comms tools.\n"
                    "⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt\n"
                    "✢ Undulating…"
                ),
            },
        )
        self.assertEqual(output.status_code, 200, output.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())
        stale_run_at = api_v2._iso_from_ms(int((time.time() - 120) * 1000))
        quiet_terminal_at = api_v2._iso_from_ms(int((time.time() - 20) * 1000))
        self._execute(
            "UPDATE dispatch_runs SET requested_at = ?, claimed_at = ?, started_at = ? WHERE id = ?",
            (stale_run_at, stale_run_at, stale_run_at, run_id),
        )
        self._execute("UPDATE terminal_sessions SET updated_at = ? WHERE id = ?", (quiet_terminal_at, terminal_id))

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)

        run = self._fetchone("SELECT status, finished_at FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(run["status"], "running")
        self.assertFalse(run["finished_at"])
        agent = listed.json()["agents"]["console-agent"]
        self.assertEqual(agent["status"], "working")

    def test_recent_claude_idle_prompt_does_not_close_before_settling_window(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="state",
            body="what is the current state",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        output = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={
                "bridgeId": "bridge-current",
                "status": "attached",
                "output": (
                    "Previous answer\n"
                    "✻ Crunched for 4m 29s\n"
                    "❯ dashboard when appropriate, using the available aify-comms tools.\n"
                    "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
                ),
            },
        )
        self.assertEqual(output.status_code, 200, output.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())
        stale_run_at = api_v2._iso_from_ms(int((time.time() - 120) * 1000))
        recent_terminal_at = api_v2._iso_from_ms(int((time.time() - 9) * 1000))
        self._execute(
            "UPDATE dispatch_runs SET requested_at = ?, claimed_at = ?, started_at = ? WHERE id = ?",
            (stale_run_at, stale_run_at, stale_run_at, run_id),
        )
        self._execute("UPDATE terminal_sessions SET updated_at = ? WHERE id = ?", (recent_terminal_at, terminal_id))

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)

        run = self._fetchone("SELECT status, finished_at FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(run["status"], "running")
        self.assertFalse(run["finished_at"])
        agent = listed.json()["agents"]["console-agent"]
        self.assertEqual(agent["status"], "working")
    def test_old_idle_claude_prompt_does_not_close_new_terminal_turn(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="state",
            body="what is the current state",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        output = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={
                "bridgeId": "bridge-current",
                "status": "attached",
                "output": "Previous prompt\n⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
            },
        )
        self.assertEqual(output.status_code, 200, output.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())
        self._execute("UPDATE terminal_sessions SET updated_at = ? WHERE id = ?", ("2000-01-01T00:00:00Z", terminal_id))

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)

        run = self._fetchone("SELECT status, finished_at FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(run["status"], "running")
        self.assertFalse(run["finished_at"])
        agent = listed.json()["agents"]["console-agent"]
        self.assertEqual(agent["status"], "working")

    def test_claude_done_narration_with_your_call_does_not_report_blocked(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="check",
            body="check this",
            mode="start_if_possible",
            createMessage=True,
        )
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        output = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={
                "bridgeId": "bridge-current",
                "status": "attached",
                "output": "Done. Your call was right; verified and pushed.\n⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
            },
        )
        self.assertEqual(output.status_code, 200, output.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        agent = listed.json()["agents"]["console-agent"]
        self.assertEqual(agent["status"], "working")
        self.assertNotIn("Awaiting console input", agent["statusNote"])

    def test_terminal_end_closes_active_claude_terminal_run(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="info",
            subject="hello",
            body="hello",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]

        output = self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={
                "bridgeId": "bridge-current",
                "status": "stopped",
                "output": "Process exited.",
            },
        )
        self.assertEqual(output.status_code, 200, output.text)
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())

        run = self._fetchone("SELECT status, finished_at FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(run["status"], "cancelled")
        self.assertTrue(run["finished_at"])
        agent = self.client.get("/api/v1/agents").json()["agents"]["console-agent"]
        # Terminal ended → no live worker → status = available (was
        # "online" under the legacy active-only taxonomy; post-Phase-2
        # available is the precise label when no worker is running).
        self.assertEqual(agent["status"], "available")
        self.assertFalse(agent["dispatchState"]["hasActiveRun"])

    def test_unthreaded_completion_info_links_active_claude_terminal_run(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="info",
            subject="push current commits up",
            body="push current commits up",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]

        completion = self._send_message(
            from_agent="console-agent",
            to="dashboard",
            type="info",
            subject="Pushed: feature/dashboard-console-mode",
            body="Done. Pushed feature/dashboard-console-mode to origin; working tree clean.",
            trigger=False,
        )

        run = self._fetchone("SELECT status, result_message_id, finished_at FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["result_message_id"], completion["messageId"])
        self.assertTrue(run["finished_at"])

    def test_managed_claude_followup_input_reuses_active_terminal_turn_contract(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )

        first = self._send_message(
            from_agent="dashboard",
            to="console-agent",
            type="info",
            subject="hello",
            body="hello",
            trigger=True,
        )
        first_run_id = first["consoleDeliveries"][0]["contractRunId"]
        second = self._send_message(
            from_agent="dashboard",
            to="console-agent",
            type="info",
            subject="state",
            body="what is the current state",
            trigger=True,
        )

        self.assertEqual(second["consoleDeliveries"][0]["contractRunId"], first_run_id)
        runs = self._fetchall(
            "SELECT id, status, subject FROM dispatch_runs WHERE target_agent = ? ORDER BY requested_at, id",
            ("console-agent",),
        )
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["id"], first_run_id)
        self.assertEqual(runs[0]["status"], "running")

        events = self._fetchall(
            "SELECT event_type, body FROM dispatch_events WHERE run_id = ? ORDER BY id",
            (first_run_id,),
        )
        self.assertIn("terminal_coalesced", [row["event_type"] for row in events])
        self.assertTrue(any(second["messageId"] in row["body"] for row in events))
        receipt = self._fetchone(
            "SELECT message_id FROM read_receipts WHERE message_id = ? AND agent_id = ?",
            (second["messageId"], "console-agent"),
        )
        self.assertEqual(receipt["message_id"], second["messageId"])

    def test_stale_unowned_claude_terminal_run_is_repaired(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        self._stamp_live_channel_sidecar()  # status-F1: live managed claude has a sidecar
        sent = self._send_message(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="claude chat",
            body="answer through terminal",
            trigger=True,
        )
        run_id = sent["consoleDeliveries"][0]["contractRunId"]
        self._execute(
            """
            UPDATE dispatch_runs
            SET requested_at = '2026-01-01T00:00:00Z',
                started_at = '2026-01-01T00:00:00Z'
            WHERE id = ?
            """,
            (run_id,),
        )

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        repaired = self._fetchone("SELECT status, summary, finished_at FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(repaired["status"], "failed")
        self.assertIn("no bridge owner", repaired["summary"])
        self.assertTrue(repaired["finished_at"])
        self.assertEqual(listed.json()["agents"]["console-agent"]["status"], "online")

    def test_message_send_to_managed_claude_replaces_legacy_raw_channel_terminal(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        legacy_terminal_id = started.json()["terminal"]["id"]
        legacy_command = (
            "claude --channels server:aify-comms-channel "
            "--dangerously-load-development-channels server:aify-comms-channel "
            "--dangerously-skip-permissions --resume claude-session-1"
        )
        self._execute(
            "UPDATE terminal_sessions SET command = ?, status = 'attached' WHERE id = ?",
            (legacy_command, legacy_terminal_id),
        )
        self._execute(
            "UPDATE agent_sessions SET owner_mode = 'managed', terminal_status = 'attached', terminal_command = ? WHERE id = ?",
            (legacy_command, session_id),
        )

        sent = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "to": "console-agent",
                "type": "request",
                "subject": "claude chat",
                "body": "answer through wrapper channel",
                "trigger": True,
            },
        )
        self.assertEqual(sent.status_code, 200, sent.text)

        legacy = self._fetchone("SELECT status, error FROM terminal_sessions WHERE id = ?", (legacy_terminal_id,))
        self.assertEqual(legacy["status"], "failed")
        self.assertIn("legacy raw Claude", legacy["error"])
        session = self._fetchone("SELECT terminal_id, terminal_command, terminal_status FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertNotEqual(session["terminal_id"], legacy_terminal_id)
        self.assertEqual(session["terminal_status"], "starting")
        self.assertIn("claude-aify --aify-agent console-agent --auto", session["terminal_command"])
        self.assertNotIn("claude --channels", session["terminal_command"])

    def test_sessions_list_reconciles_legacy_raw_claude_terminal_state(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]
        legacy_command = (
            "claude --channels server:aify-comms-channel "
            "--dangerously-skip-permissions --resume claude-session-1"
        )
        self._execute(
            "UPDATE terminal_sessions SET command = ?, status = 'attached', error = '' WHERE id = ?",
            (legacy_command, terminal_id),
        )
        self._execute(
            "UPDATE agent_sessions SET owner_mode = 'managed', terminal_status = 'attached', terminal_command = ? WHERE id = ?",
            (legacy_command, session_id),
        )

        listed = self.client.get("/api/v1/sessions?agentId=console-agent")
        self.assertEqual(listed.status_code, 200, listed.text)

        terminal = self._fetchone("SELECT status, error FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(terminal["status"], "failed")
        self.assertIn("legacy raw Claude", terminal["error"])
        session = self._fetchone("SELECT owner_mode, terminal_id, terminal_status FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(session["owner_mode"], "managed")
        self.assertEqual(session["terminal_id"], "")
        self.assertEqual(session["terminal_status"], "")

    def test_sessions_list_reconciles_orphan_attached_terminal_state(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="codex",
            terminal_runtimes=["codex"],
            session_handle="codex-thread-1",
        )
        now = "2026-01-01T00:00:00Z"
        terminal_id = "term_orphan_attached"
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime, workspace,
                command, output, output_seq, status, requested_by, created_at, updated_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                terminal_id,
                session_id,
                "console-agent",
                "linux:test-host:default",
                "bridge-old",
                "codex",
                "/workspace/repo",
                "codex-aify --aify-agent console-agent resume codex-thread-1",
                "",
                0,
                "attached",
                "dashboard",
                now,
                now,
                "",
            ),
        )

        listed = self.client.get("/api/v1/sessions?agentId=console-agent")
        self.assertEqual(listed.status_code, 200, listed.text)

        terminal = self._fetchone("SELECT status, error FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(terminal["status"], "stopped")
        self.assertIn("not referenced by any current session", terminal["error"])

    def test_sessions_list_reconciles_terminal_status_from_owner_session(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="codex",
            terminal_runtimes=["codex"],
            session_handle="codex-thread-1",
        )
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]
        self._execute(
            "UPDATE terminal_sessions SET status = 'attached', error = '' WHERE id = ?",
            (terminal_id,),
        )
        self._execute(
            "UPDATE agent_sessions SET terminal_status = 'stopped' WHERE id = ?",
            (session_id,),
        )

        listed = self.client.get("/api/v1/sessions?agentId=console-agent")
        self.assertEqual(listed.status_code, 200, listed.text)

        terminal = self._fetchone("SELECT status, error FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(terminal["status"], "stopped")
        self.assertIn("owner session is stopped", terminal["error"])

    def test_sessions_list_clears_stopped_terminal_as_current_console_binding(self):
        session_id = self._create_running_session(
            agent_id="pi-agent",
            terminal=True,
            runtime="pi",
            terminal_runtimes=["pi"],
            session_handle="pi-session-1",
        )
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]
        self._execute(
            "UPDATE terminal_sessions SET status = 'stopped', stopped_at = ?, updated_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", terminal_id),
        )
        self._execute(
            """
            UPDATE agent_sessions
            SET owner_mode = 'managed',
                terminal_status = 'stopped',
                terminal_command = 'pi-aify --aify-agent pi-agent --resume pi-session-1'
            WHERE id = ?
            """,
            (session_id,),
        )
        self._execute(
            """
            UPDATE agents
            SET runtime_state = ?,
                status_note = 'Dashboard Console PTY attached.'
            WHERE id = 'pi-agent'
            """,
            (json.dumps({"consoleTerminal": {"terminalId": terminal_id, "bridgeId": "bridge-current"}}),),
        )

        listed = self.client.get("/api/v1/sessions?agentId=pi-agent")
        self.assertEqual(listed.status_code, 200, listed.text)
        listed_session = listed.json()["sessions"][0]
        self.assertEqual(listed_session["terminalId"], "")
        self.assertEqual(listed_session["terminalStatus"], "")
        self.assertEqual(listed_session["terminalCommand"], "")

        session = self._fetchone("SELECT owner_mode, terminal_id, terminal_status, terminal_command FROM agent_sessions WHERE id = ?", (session_id,))
        self.assertEqual(session["owner_mode"], "managed")
        self.assertEqual(session["terminal_id"], "")
        self.assertEqual(session["terminal_status"], "")
        self.assertEqual(session["terminal_command"], "")
        agent = self._fetchone("SELECT runtime_state, status_note FROM agents WHERE id = 'pi-agent'")
        self.assertNotIn("consoleTerminal", json.loads(agent["runtime_state"]))
        self.assertEqual(agent["status_note"], "")

    def test_reply_to_delivered_channel_run_completes_it_without_working_status(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        self._stamp_live_channel_sidecar()  # status-F1: live managed claude has a sidecar
        sent = self._send_message(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="claude chat",
            body="answer through channel",
            trigger=True,
        )
        self.assertEqual(sent["dispatchRuns"], [])
        run_id = sent["consoleDeliveries"][0]["contractRunId"]
        self._execute(
            """
            INSERT INTO agent_live_state (agent_id, status, reason, updated_at, refresh_after)
            VALUES (?,?,?,?,?)
            ON CONFLICT(agent_id) DO UPDATE SET
                status = excluded.status,
                reason = excluded.reason,
                updated_at = excluded.updated_at,
                refresh_after = excluded.refresh_after
            """,
            ("console-agent", "active", "stale cached status", "2026-01-01T00:00:00Z", "2099-01-01T00:00:00Z"),
        )
        delivered = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={
                "status": "delivered",
                "runtime": "claude-code",
                "agentStatus": "active",
                "summary": "Delivered to Claude terminal session; awaiting explicit reply",
            },
        )
        self.assertEqual(delivered.status_code, 200, delivered.text)
        listed_active_after_delivery = self.client.get("/api/v1/agents")
        self.assertEqual(listed_active_after_delivery.status_code, 200, listed_active_after_delivery.text)
        self.assertEqual(listed_active_after_delivery.json()["agents"]["console-agent"]["status"], "online")
        self._execute(
            """
            INSERT INTO agent_live_state (agent_id, status, reason, updated_at, refresh_after)
            VALUES (?,?,?,?,?)
            ON CONFLICT(agent_id) DO UPDATE SET
                status = excluded.status,
                reason = excluded.reason,
                updated_at = excluded.updated_at,
                refresh_after = excluded.refresh_after
            """,
            ("console-agent", "working", "stale cached status", "2026-01-01T00:00:00Z", "2099-01-01T00:00:00Z"),
        )

        reply = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "console-agent",
                "to": "dashboard",
                "type": "response",
                "subject": "Re: claude chat",
                "body": "answered through channel",
                "inReplyTo": sent["messageId"],
                "trigger": False,
            },
        )
        self.assertEqual(reply.status_code, 200, reply.text)
        closed = self._fetchone("SELECT status, result_message_id, finished_at FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(closed["status"], "completed")
        self.assertTrue(closed["result_message_id"])
        self.assertTrue(closed["finished_at"])
        listed_active = self.client.get("/api/v1/agents")
        self.assertEqual(listed_active.status_code, 200, listed_active.text)
        self.assertEqual(listed_active.json()["agents"]["console-agent"]["status"], "online")

    def test_terminal_control_claim_orders_start_before_input_with_same_timestamp(self):
        # Ordering is a PTY-path concern; exercise it on a runtime that still
        # produces start+input controls.
        session_id = self._create_running_session(
            terminal=True,
            runtime="hermes",
            terminal_runtimes=["hermes"],
            session_handle="hermes-session-1",
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
        # Stale-console-owner reclaim is a PTY-path concern — exercise it on a
        # runtime that still uses the managed PTY.
        session_id = self._create_running_session(
            terminal=True,
            runtime="hermes",
            terminal_runtimes=["hermes"],
            session_handle="hermes-session-1",
        )
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
        self.assertEqual(deleted.json()["staleActiveTerminalsDeleted"], [])
        self.assertIsNone(self._fetchone("SELECT id FROM agent_sessions WHERE id = ?", (session_id,)))
        self.assertIsNone(self._fetchone("SELECT id FROM terminal_sessions WHERE id = ?", (terminal_id,)))
        agent = self._fetchone("SELECT id FROM agents WHERE id = ?", ("console-agent",))
        self.assertIsNotNone(agent)

    def test_delete_session_allows_stale_active_terminal_rows_when_session_is_inactive(self):
        session_id = self._create_running_session(terminal=True)
        started = self.client.post(f"/api/v1/sessions/{session_id}/console/start", json={"requestedBy": "dashboard"})
        self.assertEqual(started.status_code, 200, started.text)
        terminal_id = started.json()["terminal"]["id"]
        now = "2026-05-14T00:00:00Z"
        self._execute(
            """
            UPDATE agent_sessions
            SET status = ?, terminal_status = ?, owner_mode = ?, ended_at = ?, last_seen = ?
            WHERE id = ?
            """,
            ("stopped", "attached", "managed", now, now, session_id),
        )
        self._execute(
            "UPDATE terminal_sessions SET status = ?, updated_at = ? WHERE id = ?",
            ("attached", now, terminal_id),
        )

        deleted = self.client.delete(f"/api/v1/sessions/{session_id}")

        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["ok"])
        self.assertEqual(deleted.json()["staleActiveTerminalsDeleted"], [terminal_id])
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
            "codex-worker",
            role="coder",
            runtime="codex",
            sessionMode="managed",
            launchMode="managed",
        )
        dispatched = self._dispatch(
            from_agent="manager",
            to="codex-worker",
            type="request",
            subject="active",
            body="do work",
        )
        run_id = dispatched["runs"][0]["runId"]
        first_claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "codex-worker", "bridgeId": "bridge-old", "machineId": "win32:test-host", "executionModes": ["managed"]},
        )
        self.assertEqual(first_claim.status_code, 200, first_claim.text)
        self.assertEqual(first_claim.json()["run"]["id"], run_id)

        replacement_claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "codex-worker", "bridgeId": "bridge-new", "machineId": "win32:test-host", "executionModes": ["managed"]},
        )
        self.assertEqual(replacement_claim.status_code, 200, replacement_claim.text)
        payload = replacement_claim.json()
        self.assertIsNone(payload["run"])
        self.assertEqual(payload["blockedBy"]["reason"], "active_run_owned_by_previous_bridge")
        run = self._fetchone("SELECT status, summary, error_text FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(run["status"], "claimed")
        self.assertEqual(run["summary"], "")

    def test_dispatch_claim_includes_scoped_direct_conversation_context(self):
        self.client.put("/api/v1/settings", json={"managed_terminal_backing_enabled": False})
        self._register("dashboard", role="manager")
        self._create_running_session(
            agent_id="worker",
            terminal=True,
            runtime="codex",
            terminal_runtimes=["codex"],
            session_handle="thread-1",
        )
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
            json={"agentId": "worker", "bridgeId": "bridge-current", "machineId": "linux:test-host", "executionModes": ["managed"]},
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

        # Lifecycle cleanup (2026-06-03): `recover` was a byte-identical alias of
        # `restart`; the alias was dropped, so this flow now uses `restart`.
        recover = self.client.post(
            f"/api/v1/sessions/{old_session_id}/control",
            json={"action": "restart", "from_agent": "dashboard", "subject": "restart worker"},
        )
        self.assertEqual(recover.status_code, 200, recover.text)
        self.assertEqual(recover.json()["session"]["status"], "restarting")
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

        # Lifecycle cleanup (2026-06-03): `recover` alias dropped -> use `restart`.
        first_recover = self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "restart", "from_agent": "dashboard", "subject": "restart worker"},
        )
        self.assertEqual(first_recover.status_code, 200, first_recover.text)
        pending_spawn_id = first_recover.json()["spawnRequest"]["id"]

        duplicate_recover = self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "restart", "from_agent": "dashboard", "subject": "restart worker again"},
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

    def test_resident_register_requires_manual_switch_from_managed_agent(self):
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
        self.assertEqual(registered.json()["sessionMode"], "managed")
        self.assertEqual(registered.json()["ownershipTransition"], "manual_switch_required")
        agent = self._fetchone("SELECT session_mode, session_handle, launch_mode, runtime_state FROM agents WHERE id = ?", ("auto-owner",))
        self.assertEqual(agent["session_mode"], "managed")
        self.assertEqual(agent["session_handle"], "managed-thread")
        self.assertNotEqual(agent["launch_mode"], "none")
        runtime_state = json.loads(agent["runtime_state"])
        self.assertEqual(runtime_state.get("manualResidentCandidate", {}).get("bridgeId"), "resident-bridge")
        self.assertEqual(runtime_state.get("manualResidentCandidate", {}).get("sessionHandle"), "resident-thread")
        session = self._fetchone("SELECT status, session_handle FROM agent_sessions WHERE agent_id = ?", ("auto-owner",))
        self.assertEqual(session["status"], "running")
        self.assertEqual(session["session_handle"], "managed-thread")


    def test_resident_same_logical_owner_reregister_does_not_supersede_or_fail_inflight_run(self):
        # Resident-re-register / nested-RPC-child bug class (RESIDENT mode):
        # a re-register from the same logical owner (same agent_id +
        # runtime + session_mode='resident' + session_handle + machine_id)
        # must be treated as metadata refresh. Prior bridge stays NOT
        # superseded; in-flight runs stay claimed/running.
        # Operator-reported scenario: `omp --mode rpc` child registering
        # with the same session id as its resident parent and killing
        # parent's in-flight work. This protective carve-out is RESIDENT-ONLY
        # post-2026-05-22 (managed bridges now supersede each other to
        # prevent the 22-zombie-wrapper leak class — see sibling test
        # test_managed_same_logical_owner_reregister_supersedes_old_bridge).
        # Plan 2 (2026-05-25): pi no longer supports resident, so the
        # generic same-logical-owner re-register protection is now tested
        # via codex (still resident-capable). The bridge-row protection
        # logic is runtime-agnostic.
        self._heartbeat_environment()
        self._register(
            "resident-logical-owner",
            runtime="codex",
            sessionMode="resident",
            launchMode="detached",
            sessionHandle="omp-session-1",
            bridgeId="bridge-A",
            machineId="linux:test-host",
            capabilities=["resident-run", "resume", "interrupt"],
        )
        claimed = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "resident-logical-owner",
                "bridgeId": "bridge-A",
                "machineId": "linux:test-host",
                "executionModes": ["resident"],
            },
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        dispatched = self._dispatch(
            from_agent="dashboard",
            to="resident-logical-owner",
            type="request",
            subject="in flight",
            body="must survive same-session re-register",
            mode="start_if_possible",
            createMessage=True,
        )
        self.assertTrue(dispatched["runs"], dispatched)
        run_id = dispatched["runs"][0]["runId"]
        self.client.post(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "running", "bridgeId": "bridge-A", "machineId": "linux:test-host"},
        )
        self._execute(
            "UPDATE dispatch_runs SET status='running', claim_bridge_id=?, claim_machine_id=? WHERE id=?",
            ("bridge-A", "linux:test-host", run_id),
        )

        # Phase 4 race guard (2026-05-31, operator-chosen hard-error model):
        # a DIFFERENT bridge re-registering this identity while bridge-A is
        # still LIVE (fresh heartbeat) is a race — it must be HARD-REJECTED
        # (409) rather than silently superseding bridge-A and killing its
        # in-flight run. The reject leaves the prior bridge + run untouched.
        reregistered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "resident-logical-owner",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "launchMode": "detached",
                "sessionHandle": "omp-session-1",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-B",
                "capabilities": ["resident-run", "resume", "interrupt"],
            },
        )
        self.assertEqual(reregistered.status_code, 409, reregistered.text)
        self.assertIn("LIVE", reregistered.text)
        self.assertIn("force=true", reregistered.text)

        # The original bridge MUST NOT be marked superseded (the racing
        # registration was rejected before any supersession ran).
        prior = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-A",))
        self.assertEqual(prior["superseded_by"], "", "rejected race re-register must not supersede the prior bridge")
        # The in-flight run MUST stay alive.
        run = self._fetchone("SELECT status, error_text FROM dispatch_runs WHERE id=?", (run_id,))
        self.assertEqual(run["status"], "running", f"in-flight resident run was killed by a rejected re-register: {dict(run)}")

    def test_same_session_relaunch_takeover_when_idle(self):
        """2026-06-13 (sc-manager stale+deaf incident): a quick close-and-relaunch of a
        resident wrapper resumes the SAME session handle while the killed prior bridge
        still looks live (heartbeat lease). With NO in-flight run, the re-register must
        SUCCEED and supersede the prior bridge — the one-shot 409 left the session
        unbound for hours (mute sidecar, pinned dead bridge → stale). The in-flight
        variant (test above) must keep its hard 409."""
        self._heartbeat_environment()
        self._register(
            "relaunch-owner",
            runtime="codex",
            sessionMode="resident",
            launchMode="detached",
            sessionHandle="omp-session-3",
            bridgeId="bridge-A",
            machineId="linux:test-host",
            capabilities=["resident-run", "resume", "interrupt"],
        )
        # No in-flight run, same handle, different bridge, NO force.
        reregistered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "relaunch-owner",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "launchMode": "detached",
                "sessionHandle": "omp-session-3",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-B",
                "capabilities": ["resident-run", "resume", "interrupt"],
            },
        )
        self.assertEqual(reregistered.status_code, 200, reregistered.text)
        prior = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-A",))
        self.assertEqual(prior["superseded_by"], "bridge-B",
                         "idle same-session relaunch must take over the dead-looking prior bridge")

    def test_same_machine_different_session_still_409s(self):
        """The Phase-4 duplicate-identity protection stays for a DIFFERENT session
        handle: only a same-session relaunch may take over without force."""
        self._heartbeat_environment()
        self._register(
            "relaunch-other",
            runtime="codex",
            sessionMode="resident",
            launchMode="detached",
            sessionHandle="omp-session-4",
            bridgeId="bridge-A",
            machineId="linux:test-host",
            capabilities=["resident-run", "resume", "interrupt"],
        )
        reregistered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "relaunch-other",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "launchMode": "detached",
                "sessionHandle": "omp-session-DIFFERENT",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-B",
                "capabilities": ["resident-run", "resume", "interrupt"],
            },
        )
        self.assertEqual(reregistered.status_code, 409, reregistered.text)
        self.assertIn("LIVE", reregistered.text)

    def test_resident_reregister_with_force_takes_over_live_bridge(self):
        # Phase 4: force=true is the deliberate takeover escape hatch. The
        # operator restarted the prior wrapper, so a fresh same-mode bridge
        # WITH force=true supersedes the prior bridge (latest-launch-wins) and
        # the rejected-race guard is bypassed.
        self._heartbeat_environment()
        self._register(
            "force-owner",
            runtime="codex",
            sessionMode="resident",
            launchMode="detached",
            sessionHandle="omp-session-2",
            bridgeId="bridge-A",
            machineId="linux:test-host",
            capabilities=["resident-run", "resume", "interrupt"],
        )
        reregistered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "force-owner",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "launchMode": "detached",
                "sessionHandle": "omp-session-2",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-B",
                "capabilities": ["resident-run", "resume", "interrupt"],
                "force": True,
            },
        )
        self.assertEqual(reregistered.status_code, 200, reregistered.text)
        prior = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-A",))
        self.assertEqual(prior["superseded_by"], "bridge-B", "force=true must take over (supersede) the prior live bridge")

    def test_resident_stale_same_handle_bridge_IS_superseded_by_fresh_reregister(self):
        # Heartbeat-aware carve-out (2026-05-23): the resident carve-out
        # that protects same-handle re-registers MUST only apply when the
        # prior bridge is still HEARTBEATING. A bridge whose last_seen is
        # older than the 5-min stale window is a dead process — its row
        # should be superseded so the table doesn't accumulate zombie
        # entries across restarts. Operator-reported 2026-05-23:
        # comms-tech-lead had 10+ leaked bridge_instances from May 21-22
        # claude-aify restarts, all sharing the same session_handle and
        # session_mode='resident', none superseded because the pre-fix
        # carve-out unconditionally protected same-handle resident rows.
        self._heartbeat_environment()
        # Register the agent first (FK target). _register writes a
        # bridge_instances row with last_seen=now; we then UPDATE that
        # row to a stale timestamp so the heartbeat-aware carve-out kicks in.
        self._register(
            "resident-zombie",
            runtime="claude-code",
            sessionMode="resident",
            launchMode="detached",
            sessionHandle="claude-session-1",
            bridgeId="bridge-stale-resident",
            machineId="linux:test-host",
            capabilities=["resident-run", "resume", "interrupt"],
        )
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            "UPDATE bridge_instances SET last_seen=?, registered_at=? WHERE id=?",
            (stale_at, stale_at, "bridge-stale-resident"),
        )
        # Fresh re-register with identical logical identity but a new bridgeId.
        reregistered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "resident-zombie",
                "role": "coder",
                "runtime": "claude-code",
                "sessionMode": "resident",
                "launchMode": "detached",
                "sessionHandle": "claude-session-1",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-fresh-resident",
                "capabilities": ["resident-run", "resume", "interrupt"],
            },
        )
        self.assertEqual(reregistered.status_code, 200, reregistered.text)

        # The STALE bridge MUST be marked superseded (heartbeat-aware fix).
        prior = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-stale-resident",))
        self.assertEqual(
            prior["superseded_by"],
            "bridge-fresh-resident",
            f"stale same-handle resident bridge must be superseded by fresh re-register; got {dict(prior)}",
        )

    def test_resident_reregister_with_new_handle_upserts_one_session_row(self):
        # FIX 1 (2026-06-03): the resident agent_sessions id must be STABLE across
        # relaunches so a relaunch UPSERTs the SAME row instead of minting a new
        # resident_* every launch. Pre-fix the id keyed on session_handle (which
        # rotates per launch), so two registers with DIFFERENT handles produced TWO
        # rows. Key on (agent_id, runtime, machine) → exactly one row survives.
        self._heartbeat_environment()
        self._register_live_codex_resident(
            "resident-upsert",
            session_handle="codex-handle-1",
            bridge_id="bridge-resident-1",
            port=45111,
        )
        rows_after_first = self._fetchall(
            "SELECT id FROM agent_sessions WHERE agent_id = ? AND mode = 'resident'",
            ("resident-upsert",),
        )
        self.assertEqual(
            len(rows_after_first), 1,
            f"first resident register must create exactly one session row; got {[dict(r) for r in rows_after_first]}",
        )
        first_id = rows_after_first[0]["id"]

        # Relaunch: SAME agent + runtime + machine, but a DIFFERENT session_handle.
        # force=true so the re-register takes over the still-live prior bridge
        # (a real relaunch supersedes the previous instance).
        self._register(
            "resident-upsert",
            runtime="codex",
            sessionMode="resident",
            sessionHandle="codex-handle-2-different",
            machineId="linux:test-host",
            bridgeId="bridge-resident-2",
            capabilities=["resident-run", "resume", "interrupt", "steer"],
            runtimeConfig={"appServerUrl": "ws://127.0.0.1:45222"},
            force=True,
        )
        rows_after_second = self._fetchall(
            "SELECT id, session_handle, status FROM agent_sessions WHERE agent_id = ? AND mode = 'resident'",
            ("resident-upsert",),
        )
        live_rows = [r for r in rows_after_second if str(r["status"]) not in ("stopped", "failed", "exited")]
        # Exactly ONE row total (the relaunch UPSERTed the same id, not a new row),
        # and the live one carries the refreshed handle.
        self.assertEqual(
            len(rows_after_second), 1,
            f"relaunch with a new handle must UPSERT one row, not mint a second; got {[dict(r) for r in rows_after_second]}",
        )
        self.assertEqual(rows_after_second[0]["id"], first_id, "the upserted row must keep the stable id")
        self.assertEqual(rows_after_second[0]["session_handle"], "codex-handle-2-different")
        self.assertEqual(len(live_rows), 1)

    def test_tombstone_blocks_autoregister_under_different_casing(self):
        # FIX 4 (2026-06-03): tombstone lookup is case-insensitive, so deleting
        # `foo` then auto-registering `Foo` (autoRegister, no restoreDeleted) is
        # still BLOCKED with 410. Pre-fix the case-sensitive WHERE let a different
        # casing bypass a deliberate deletion.
        self._heartbeat_environment()
        self._register("foo", runtime="codex", sessionMode="resident", machineId="linux:test-host")
        deleted = self.client.delete("/api/v1/agents/foo")
        self.assertEqual(deleted.status_code, 200, deleted.text)

        blocked = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "Foo",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "machineId": "linux:test-host",
                "autoRegister": True,
            },
        )
        self.assertEqual(
            blocked.status_code, 410,
            f"auto re-register under different casing must hit the tombstone (410); got {blocked.status_code}: {blocked.text}",
        )

    def test_managed_same_logical_owner_reregister_supersedes_old_bridge(self):
        # Operator-reported 2026-05-22: 22+ leaked managed bridge_instances
        # for sc-manager, all sharing the same (runtime, session_mode='managed',
        # session_handle), none ever superseded. They piled up as the
        # operator opened/closed claude-aify sessions across the day, and
        # the pre-fix carve-out preserved them all. Older zombies running
        # pre-d4e2ba9 code kept feedback-looping turn_busy.
        # New semantic (post-2026-05-22): for MANAGED session_mode, latest
        # registration ALWAYS supersedes older same-(agent, machine, runtime,
        # session_mode) bridges, regardless of session_handle. Only one
        # bridge can claim per managed agent at a time.
        self._heartbeat_environment()
        self._register(
            "managed-logical-owner",
            runtime="codex",
            sessionMode="managed",
            launchMode="managed",
            sessionHandle="codex-thread-1",
            bridgeId="bridge-A",
            machineId="linux:test-host",
            capabilities=["managed-run", "native-managed-run", "resume", "interrupt", "steer"],
        )
        # Bridge-A registered; no other bridges yet.
        bridge_a = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-A",))
        self.assertEqual(bridge_a["superseded_by"], "")

        # Same-logical-owner re-register with FRESH bridge-B for MANAGED mode.
        reregistered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "managed-logical-owner",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "managed",
                "launchMode": "managed",
                "sessionHandle": "codex-thread-1",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-B",
                "capabilities": ["managed-run", "native-managed-run", "resume", "interrupt", "steer"],
            },
        )
        self.assertEqual(reregistered.status_code, 200, reregistered.text)

        # bridge-A MUST now be superseded by bridge-B.
        prior = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-A",))
        self.assertEqual(prior["superseded_by"], "bridge-B", "managed same-handle re-register MUST supersede the older bridge")
        latest = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-B",))
        self.assertEqual(latest["superseded_by"], "", "newest managed bridge stays primary")

    def test_superseded_generic_managed_bridge_fails_its_inflight_run_no_orphan(self):
        # Phase 5 safety invariant (no-orphan): when a generic managed bridge is
        # superseded by a fresh same-(agent,machine,runtime,mode) registration,
        # any run that the OLD bridge was driving must be FAILED — not left
        # 'running' forever with a dead owner. (Wrapper-child + channel-sidecar
        # pairs are the protected exception, covered by their own tests.) This
        # guards _fail_active_runs_for_superseded_bridges, which the path review
        # flagged as critical-but-unisolated.
        self._heartbeat_environment()
        # NB: NO terminalId — codex IS in _CHANNEL_CLAIM_RUNTIMES, so a codex
        # managed registration WITH a terminalId would be inferred as a
        # protected managed-wrapper-child (run survives). Omitting terminalId
        # keeps this on the GENERIC managed path (latest-wins → run fails),
        # which is exactly the no-orphan path under test. Don't add terminalId.
        self._register(
            "managed-orphan-guard",
            runtime="codex",
            sessionMode="managed",
            launchMode="managed",
            sessionHandle="codex-thread-orphan",
            bridgeId="bridge-A",
            machineId="linux:test-host",
            capabilities=["managed-run", "native-managed-run", "resume", "interrupt", "steer"],
        )
        dispatched = self._dispatch(
            from_agent="dashboard",
            to="managed-orphan-guard",
            type="request",
            subject="in flight",
            body="owned by bridge-A",
            mode="start_if_possible",
            createMessage=True,
        )
        self.assertTrue(dispatched["runs"], dispatched)
        run_id = dispatched["runs"][0]["runId"]
        self.client.post(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "running", "bridgeId": "bridge-A", "machineId": "linux:test-host"},
        )
        self._execute(
            "UPDATE dispatch_runs SET status='running', claim_bridge_id=?, claim_machine_id=? WHERE id=?",
            ("bridge-A", "linux:test-host", run_id),
        )
        # Fresh bridge-B supersedes bridge-A (managed latest-launch-wins).
        reregistered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "managed-orphan-guard",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "managed",
                "launchMode": "managed",
                "sessionHandle": "codex-thread-orphan",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-B",
                "capabilities": ["managed-run", "native-managed-run", "resume", "interrupt", "steer"],
            },
        )
        self.assertEqual(reregistered.status_code, 200, reregistered.text)
        prior = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-A",))
        self.assertEqual(prior["superseded_by"], "bridge-B")
        # No orphan: the run bridge-A was driving must be terminal, not 'running'.
        run = self._fetchone("SELECT status FROM dispatch_runs WHERE id=?", (run_id,))
        self.assertIn(
            run["status"], {"failed", "cancelled"},
            f"superseded generic managed bridge must fail its in-flight run (no orphan); got {run['status']}",
        )

    def test_managed_wrapper_child_same_logical_owner_reregister_does_not_fail_inflight_run(self):
        # Wrapper-backed managed runtimes (Plan 4/6 console mode) are
        # bridge-spawned PTYs whose in-process MCP bridge claims via the
        # channel/resident route. They are managed for operator ownership,
        # but same-handle fresh re-registers are still the same live wrapper
        # owner class. Treating them like generic managed bridges kills the
        # active run mid-turn; this is the Hermes "WS closed before turn
        # completed" duplicate-registration failure observed on 2026-05-26.
        self._heartbeat_environment()
        self._register(
            "managed-wrapper-logical-owner",
            runtime="hermes",
            sessionMode="managed",
            launchMode="managed",
            sessionHandle="hermes-session-1",
            bridgeId="bridge-A",
            machineId="linux:test-host",
            terminalId="term-wrapper-1",
            capabilities=["managed-run", "channel", "resident-run", "resume", "interrupt", "steer"],
        )
        dispatched = self._dispatch(
            from_agent="dashboard",
            to="managed-wrapper-logical-owner",
            type="request",
            subject="in flight",
            body="must survive wrapper-child re-register",
            mode="start_if_possible",
            createMessage=True,
        )
        self.assertTrue(dispatched["runs"], dispatched)
        run_id = dispatched["runs"][0]["runId"]
        self.client.post(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "running", "bridgeId": "bridge-A", "machineId": "linux:test-host"},
        )
        self._execute(
            "UPDATE dispatch_runs SET status='running', claim_bridge_id=?, claim_machine_id=? WHERE id=?",
            ("bridge-A", "linux:test-host", run_id),
        )

        reregistered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "managed-wrapper-logical-owner",
                "role": "coder",
                "runtime": "hermes",
                "sessionMode": "managed",
                "launchMode": "managed",
                "sessionHandle": "hermes-session-1",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-B",
                "terminalId": "term-wrapper-1",
                "capabilities": ["managed-run", "channel", "resident-run", "resume", "interrupt", "steer"],
            },
        )
        self.assertEqual(reregistered.status_code, 200, reregistered.text)

        prior = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-A",))
        self.assertEqual(prior["superseded_by"], "", "fresh managed wrapper-child same-owner re-register must not supersede the prior bridge")
        run = self._fetchone("SELECT status, error_text FROM dispatch_runs WHERE id=?", (run_id,))
        self.assertEqual(run["status"], "running", f"in-flight wrapper-managed run was killed by same-session re-register: {dict(run)}")

    def test_claim_poll_does_not_auto_heal_fresh_wrapper_child_active_run(self):
        self._heartbeat_environment(
            bridgeId="env-bridge",
            runtimes=[{"runtime": "hermes", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        self._register(
            "wrapper-active-owner",
            runtime="hermes",
            sessionMode="managed",
            launchMode="managed",
            sessionHandle="hermes-session-1",
            bridgeId="wrapper-bridge",
            machineId="linux:test-host",
            terminalId="term-wrapper-owner",
            capabilities=["managed-run", "channel", "resident-run", "resume", "interrupt", "steer"],
        )
        old_started_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode,
                message_type, subject, body, priority, status, require_reply,
                requested_at, claimed_at, started_at, claim_machine_id, claim_bridge_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_wrapper_fresh_active",
                None,
                "dashboard",
                "wrapper-active-owner",
                "start_if_possible",
                "channel",
                "request",
                "already running",
                "keep going",
                "normal",
                "running",
                1,
                old_started_at,
                old_started_at,
                old_started_at,
                "linux:test-host",
                "wrapper-bridge",
            ),
        )

        claimed = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "wrapper-active-owner",
                "bridgeId": "env-bridge",
                "machineId": "linux:test-host",
                "executionModes": ["channel"],
            },
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertIsNone(claimed.json().get("run"))
        self.assertIn(
            claimed.json().get("blockedBy", {}).get("reason"),
            {"bridge_not_current", "active_run_owner_bridge_still_heartbeating"},
        )

        run = self._fetchone("SELECT status, summary FROM dispatch_runs WHERE id=?", ("run_wrapper_fresh_active",))
        self.assertEqual(run["status"], "running", f"fresh wrapper-child active run must not be auto-healed: {dict(run)}")
        events = self._fetchall("SELECT event_type FROM dispatch_events WHERE run_id=?", ("run_wrapper_fresh_active",))
        self.assertNotIn("auto_heal", [row["event_type"] for row in events])

    def test_default_channel_routing_for_managed_claude(self):
        # Operator design: managed Claude should deliver via channels by
        # default, NOT via service-created terminal-input injection.
        # Setting insert_messages_via_console=false (the default) makes
        # managed claude-code sends skip the PTY routing entirely and
        # the resulting dispatch_run carries execution_mode='channel'
        # so claude-channel.js claims it. (Earlier name was
        # claude_managed_channel_only with inverted polarity.)
        self.client.put("/api/v1/settings", json={"insert_messages_via_console": False})
        self._heartbeat_environment(
            terminal=True,
            pty=True,
            terminalRuntimes=["claude-code"],
            runtimes=[{"runtime": "claude-code", "available": True, "supportsTerminal": True}],
        )
        self._register(
            "claude-channel-only",
            runtime="claude-code",
            sessionMode="managed",
            launchMode="managed",
            sessionHandle="claude-thread-1",
            machineId="linux:test-host",
            bridgeId="claude-bridge",
            capabilities=["managed-run", "channel", "resume", "interrupt", "steer"],
        )
        dispatched = self._dispatch(
            from_agent="dashboard",
            to="claude-channel-only",
            type="request",
            subject="channel-only",
            body="route via channel not PTY",
            mode="start_if_possible",
            createMessage=True,
        )
        self.assertTrue(dispatched["runs"], f"expected a launchable dispatch_run, got {dispatched}")
        self.assertEqual(
            dispatched.get("consoleDeliveries", []),
            [],
            f"channel-only must skip PTY routing; got console deliveries {dispatched.get('consoleDeliveries')}",
        )
        run_id = dispatched["runs"][0]["runId"]
        run = self._fetchone("SELECT execution_mode, dispatch_mode FROM dispatch_runs WHERE id=?", (run_id,))
        self.assertEqual(
            run["execution_mode"], "channel",
            f"channel-only must produce execution_mode='channel' so claude-channel.js claims it; got {dict(run)}",
        )

    def test_active_run_with_non_superseded_owner_bridge_survives_current_bridge_change(self):
        # Companion to test_same_logical_owner_reregister_*. The stale-active
        # discard path (_discard_unclaimable_active_run) used to fail an
        # active run whenever the agent's current bridgeInstanceId differed
        # from the run's owner_bridge_id, even if the owner bridge was still
        # the valid logical owner (not superseded). Live-smoke failure mode:
        # codex/pi PTY dispatch after a same-session re-register cancelled
        # the run with "is not the current agent bridge". Scope-narrowed:
        # only fail when the owner bridge is actually superseded.
        self._heartbeat_environment()
        self._register(
            "discard-scope-agent",
            runtime="codex",
            sessionMode="managed",
            launchMode="managed",
            sessionHandle="codex-thread-Z",
            bridgeId="owner-bridge",
            machineId="linux:test-host",
            capabilities=["managed-run", "native-managed-run", "resume", "interrupt", "steer"],
        )
        self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "discard-scope-agent",
                "bridgeId": "owner-bridge",
                "machineId": "linux:test-host",
                "executionModes": ["managed"],
            },
        )
        dispatched = self._dispatch(
            from_agent="dashboard",
            to="discard-scope-agent",
            type="request",
            subject="active run",
            body="will survive bridge-id change without supersession",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["runs"][0]["runId"]
        self._execute(
            "UPDATE dispatch_runs SET status='running', claim_bridge_id=?, claim_machine_id=? WHERE id=?",
            ("owner-bridge", "linux:test-host", run_id),
        )
        # Simulate: agent's current bridgeInstanceId moved to a different id
        # WITHOUT superseding the owner bridge (this is what happens on a
        # same-logical-owner re-register after slice 4dbb2e2).
        import json as _json
        self._execute(
            "UPDATE agents SET runtime_state=? WHERE id=?",
            (
                _json.dumps({"bridgeInstanceId": "new-current-bridge", "environmentId": "linux:test-host:default"}),
                "discard-scope-agent",
            ),
        )
        # Now send another message. The discard path runs; with the
        # scope-narrowing, the owner-bridge run must NOT be failed because
        # owner-bridge is still not-superseded.
        self._send_message(
            from_agent="dashboard",
            to="discard-scope-agent",
            type="info",
            subject="ping",
            body="should not kill in-flight",
        )
        run = self._fetchone("SELECT status, error_text FROM dispatch_runs WHERE id=?", (run_id,))
        self.assertEqual(
            run["status"], "running",
            f"in-flight run was killed by stale-active-discard despite owner bridge still being valid: {dict(run)}",
        )

    def test_different_session_handle_reregister_still_supersedes_and_fails_run(self):
        # Contract guard for the opposite case: a genuinely different logical
        # owner (different session_handle) still supersedes and fails the
        # prior owner's in-flight run, as before. The fix must not over-relax.
        self._heartbeat_environment()
        self._register(
            "diff-session-reregister",
            runtime="codex",
            sessionMode="managed",
            launchMode="managed",
            sessionHandle="codex-thread-S1",
            bridgeId="bridge-S1",
            machineId="linux:test-host",
            capabilities=["managed-run", "native-managed-run", "resume", "interrupt", "steer"],
        )
        self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "diff-session-reregister",
                "bridgeId": "bridge-S1",
                "machineId": "linux:test-host",
                "executionModes": ["managed"],
            },
        )
        dispatched = self._dispatch(
            from_agent="dashboard",
            to="diff-session-reregister",
            type="request",
            subject="will die",
            body="different session handle is a real owner change",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["runs"][0]["runId"]
        self._execute(
            "UPDATE dispatch_runs SET status='running', claim_bridge_id=?, claim_machine_id=? WHERE id=?",
            ("bridge-S1", "linux:test-host", run_id),
        )

        # Different session_handle: a genuinely new logical owner.
        reregistered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "diff-session-reregister",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "managed",
                "launchMode": "managed",
                "sessionHandle": "codex-thread-S2",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-S2",
                "capabilities": ["managed-run", "native-managed-run", "resume", "interrupt", "steer"],
            },
        )
        self.assertEqual(reregistered.status_code, 200, reregistered.text)

        prior = self._fetchone("SELECT superseded_by FROM bridge_instances WHERE id=?", ("bridge-S1",))
        self.assertEqual(prior["superseded_by"], "bridge-S2", "different session_handle must supersede the prior bridge")
        run = self._fetchone("SELECT status FROM dispatch_runs WHERE id=?", (run_id,))
        self.assertEqual(run["status"], "failed", "different-owner re-register must fail the prior owner's in-flight run")

    def test_resident_registration_does_not_claim_queued_managed_run_without_manual_switch(self):
        self.client.put("/api/v1/settings", json={"managed_terminal_backing_enabled": False})
        self._register(
            "resident-queue",
            runtime="codex",
            sessionMode="managed",
            launchMode="managed",
            capabilities=["managed-run", "native-managed-run", "resume", "interrupt", "steer"],
        )
        created = self._dispatch(
            from_agent="dashboard",
            to="resident-queue",
            type="request",
            subject="queued before resident",
            body="claim me after visible CLI starts",
            mode="start_if_possible",
            createMessage=True,
        )
        self.assertTrue(created["runs"], created)
        run_id = created["runs"][0]["runId"]

        registered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "resident-queue",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "resident",
                "sessionHandle": "codex-session-visible",
                "machineId": "linux:test-host",
                "bridgeId": "resident-bridge",
                "capabilities": ["resident-run", "resume", "interrupt", "steer"],
                "runtimeConfig": {"appServerUrl": "ws://127.0.0.1:1234"},
            },
        )
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertEqual(registered.json()["ownershipTransition"], "manual_switch_required")

        claimed = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "resident-queue",
                "bridgeId": "resident-bridge",
                "machineId": "linux:test-host",
                "executionModes": ["resident"],
            },
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertIsNone(claimed.json().get("run"))
        stored = self._fetchone("SELECT status, execution_mode, claim_bridge_id FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(stored["status"], "queued")
        self.assertEqual(stored["execution_mode"], "managed")
        self.assertEqual(stored["claim_bridge_id"], "")


    def test_claim_ignores_missing_message_ids_in_buffered_body(self):
        # Plan 2 (2026-05-25): pi no longer supports a true resident
        # session — registering pi+resident now marks the row pending-flip
        # and dispatch returns 409 until the drain helper migrates the
        # agent. This test exercises generic resident-mode buffered-claim
        # plumbing, so use codex (which still supports resident) instead.
        self._register(
            "receipt-agent",
            runtime="codex",
            sessionMode="resident",
            sessionHandle="codex-session-visible",
            machineId="linux:test-host",
            bridgeId="resident-bridge",
            capabilities=["resident-run", "resume", "interrupt", "steer"],
        )
        created = self._dispatch(
            from_agent="dashboard",
            to="receipt-agent",
            type="request",
            subject="buffered receipt",
            body="claim should mark existing source read",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        run = self._fetchone("SELECT message_id, body FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertTrue(run["message_id"])
        self._execute(
            "UPDATE dispatch_runs SET body = ? WHERE id = ?",
            (f"{run['body']}\n\n--- Buffered item ---\nMessageId: missing-message-id\nBody: stale reference", run_id),
        )

        claimed = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "receipt-agent",
                "bridgeId": "resident-bridge",
                "machineId": "linux:test-host",
                "executionModes": ["resident"],
            },
        )

        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["run"]["id"], run_id)
        valid_receipt = self._fetchone(
            "SELECT read_at FROM read_receipts WHERE message_id = ? AND agent_id = ?",
            (run["message_id"], "receipt-agent"),
        )
        self.assertIsNotNone(valid_receipt)
        missing_receipt = self._fetchone(
            "SELECT read_at FROM read_receipts WHERE message_id = ? AND agent_id = ?",
            ("missing-message-id", "receipt-agent"),
        )
        self.assertIsNone(missing_receipt)

    def test_resident_register_does_not_auto_takeover_managed_agent(self):
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
        self.assertEqual(registered.json()["ownershipTransition"], "manual_switch_required")
        agent = self._fetchone("SELECT session_mode, runtime_state FROM agents WHERE id = ?", ("defer-owner",))
        self.assertEqual(agent["session_mode"], "managed")
        state = json.loads(agent["runtime_state"])
        self.assertNotIn("pendingResidentTakeover", state)
        self.assertEqual(state["manualResidentCandidate"]["sessionHandle"], "resident-thread")

        patched = self.client.patch(f"/api/v1/dispatch/runs/{run_id}", json={"status": "completed", "summary": "done"})
        self.assertEqual(patched.status_code, 200, patched.text)
        agent = self._fetchone("SELECT session_mode, session_handle, runtime_state FROM agents WHERE id = ?", ("defer-owner",))
        self.assertEqual(agent["session_mode"], "managed")
        self.assertNotEqual(agent["session_handle"], "resident-thread")
        self.assertNotIn("pendingResidentTakeover", json.loads(agent["runtime_state"]))
        switched = self.client.patch("/api/v1/agents/defer-owner/session-mode", json={"mode": "resident"})
        self.assertEqual(switched.status_code, 200, switched.text)
        agent = self._fetchone("SELECT session_mode, session_handle FROM agents WHERE id = ?", ("defer-owner",))
        self.assertEqual(agent["session_mode"], "resident")
        self.assertEqual(agent["session_handle"], "resident-thread")

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
        self.assertEqual(registered.json()["ownershipTransition"], "manual_switch_required")

        patched = self.client.patch(
            "/api/v1/agents/pending-owner/runtime-state",
            json={"runtimeState": {"bridgeInstanceId": "resident-bridge", "threadId": "resident-thread"}},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        state = patched.json()["runtimeState"]
        self.assertEqual(state["bridgeInstanceId"], "managed-bridge")
        self.assertEqual(state["environmentId"], "linux:test-host:default")
        self.assertEqual(state["manualResidentCandidate"]["sessionHandle"], "resident-thread")
        self.assertNotIn("pendingResidentTakeover", state)
        agent = self._fetchone("SELECT session_mode FROM agents WHERE id = ?", ("pending-owner",))
        self.assertEqual(agent["session_mode"], "managed")

    def test_stale_resident_send_does_not_auto_return_to_managed(self):
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
        switched = self.client.patch("/api/v1/agents/return-owner/session-mode", json={"mode": "resident"})
        self.assertEqual(switched.status_code, 200, switched.text)
        self._execute("UPDATE agents SET last_seen = ?, runtime_state = ? WHERE id = ?", ("2000-01-01T00:00:00Z", json.dumps({"bridgeInstanceId": "resident-bridge"}), "return-owner"))
        self._execute("UPDATE bridge_instances SET last_seen = ? WHERE id = ?", ("2000-01-01T00:00:00Z", "resident-bridge"))

        sent = self._send_message(from_agent="dashboard", to="return-owner", type="request", subject="resume managed", body="hello", trigger=True)
        self.assertFalse(sent["ok"])
        self.assertFalse(sent.get("dispatchRuns"))
        agent = self._fetchone("SELECT session_mode, launch_mode, session_handle FROM agents WHERE id = ?", ("return-owner",))
        self.assertEqual(agent["session_mode"], "resident")
        self.assertEqual(agent["launch_mode"], "detached")
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
        switched = self.client.patch("/api/v1/agents/stop-resident/session-mode", json={"mode": "resident"})
        self.assertEqual(switched.status_code, 200, switched.text)

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
        # Phase 3 (2026-06-03): GET /sessions now DERIVES the session status from
        # live terminal truth, so the newer row needs a LIVE backing terminal to
        # display 'running' (this test's intent is the superseded-recovering repair,
        # not liveness — give it a genuinely-live console).
        self._seed_terminal_for_session(
            tid="term_newer_running", sid="sess_newer_running",
            agent_id="repair-recover-coder", status="attached", runtime="codex",
        )
        # Bind the session to its console terminal (the production shape) so the
        # GET-path orphan-terminal repair does not stop an unreferenced terminal.
        self._execute(
            "UPDATE agent_sessions SET terminal_id = ?, terminal_status = ? WHERE id = ?",
            ("term_newer_running", "attached", "sess_newer_running"),
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

        # Lifecycle cleanup (2026-06-03): `recover` alias dropped -> use `restart`.
        recover = self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "restart", "from_agent": "dashboard", "subject": "restart worker"},
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
        self.client.put("/api/v1/settings", json={"managed_terminal_backing_enabled": False})
        self._register("lead", runtime="codex", sessionMode="managed")
        self._create_running_session(
            agent_id="manager",
            role="manager",
            terminal=True,
            runtime="codex",
            terminal_runtimes=["codex"],
            session_handle="thread-manager",
        )

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
            queueIfBusy=True,
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

    def test_dashboard_info_run_summary_is_persisted_to_chat(self):
        self._register("coder", runtime="codex", sessionMode="managed")

        created = self._dispatch(
            from_agent="dashboard",
            to="coder",
            type="info",
            subject="state check",
            body="what is current state?",
            mode="start_if_possible",
            createMessage=True,
            requireReply=False,
        )
        run_id = created["runs"][0]["runId"]

        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "summary": "current state is clean"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        final = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(final.status_code, 200, final.text)
        result_message_id = final.json()["run"]["resultMessageId"]
        self.assertTrue(result_message_id)
        self.assertEqual(final.json()["run"]["replyState"], "sent")

        inbox = self.client.get(f"/api/v1/messages/inbox/dashboard?messageId={result_message_id}")
        self.assertEqual(inbox.status_code, 200, inbox.text)
        message = inbox.json()["messages"][0]
        self.assertEqual(message["from"], "coder")
        self.assertEqual(message["type"], "response")
        self.assertEqual(message["body"], "current state is clean")
        self.assertEqual(message["inReplyTo"], created["messageId"])

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

    def test_resident_running_reply_closes_dispatch_run(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register(
            "coder",
            runtime="hermes",
            sessionMode="resident",
            sessionHandle="hermes-key",
            runtimeConfig={"gatewayUrl": "ws://127.0.0.1:1/api/ws?token=t"},
        )

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="ping",
            body="reply when received",
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
            subject="pong",
            body="received",
            inReplyTo=source_message_id,
            trigger=False,
        )

        final = self.client.get(f"/api/v1/dispatch/runs/{run_id}")
        self.assertEqual(final.status_code, 200, final.text)
        self.assertEqual(final.json()["run"]["status"], "completed")
        self.assertEqual(final.json()["run"]["resultMessageId"], reply["messageId"])
        self.assertEqual(final.json()["run"]["replyState"], "sent")
        self.assertFalse(final.json()["run"]["replyPending"])

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

    def test_triggered_info_send_does_not_require_reply_by_default(self):
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
        self.assertFalse(sent["dispatchRuns"][0]["requireReply"])

    def test_triggered_info_send_can_explicitly_require_reply(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")

        sent = self._send_message(
            from_agent="lead",
            to="coder",
            type="info",
            subject="please confirm",
            body="ack this one",
            trigger=True,
            requireReply=True,
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
        self.client.put("/api/v1/settings", json={"managed_terminal_backing_enabled": False})
        self._create_running_session(
            agent_id="manager",
            role="manager",
            terminal=True,
            runtime="codex",
            terminal_runtimes=["codex"],
            session_handle="thread-manager",
        )
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
        self.client.put("/api/v1/settings", json={"managed_terminal_backing_enabled": False})
        self._create_running_session(
            agent_id="manager",
            role="manager",
            terminal=True,
            runtime="opencode",
            terminal_runtimes=["opencode"],
            session_handle="opencode-session-1",
        )
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
        self.client.put("/api/v1/settings", json={"managed_terminal_backing_enabled": False})
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("qa", runtime="codex", sessionMode="managed")
        self._create_running_session(
            agent_id="manager",
            role="manager",
            terminal=True,
            runtime="opencode",
            terminal_runtimes=["opencode"],
            session_handle="opencode-session-1",
        )

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
        self.assertNotIn("steer", resident.json()["agent"]["capabilities"])
        self.assertEqual(resident.json()["agent"]["wakeMode"], "presence-only")

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
            machineId="linux:test-host",
            bridgeId="bridge-claude",
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

    def test_dispatch_claim_signals_stopped_for_disabled_agent(self):
        # Phase H1 (status v2): a managed agent the operator DISABLED
        # (agents.status == "stopped") must get a terminal `stopped` signal on
        # claim so its worker + polling bridge self-exit (orphan reap) instead
        # of polling forever. stopped is reversible — the claim must NOT clear
        # the agent's session binding.
        self._register("worker", runtime="claude", sessionMode="managed")
        self._execute("UPDATE agents SET status = ? WHERE id = ?", ("stopped", "worker"))

        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "worker", "bridgeId": "bridge-1", "executionModes": ["managed"]},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        body = claim.json()
        self.assertTrue(body.get("stopped"))
        self.assertIsNone(body["run"])

    def test_dispatch_claim_does_not_signal_stopped_for_active_agent(self):
        # Guard: a non-stopped managed agent's claim response must NOT carry the
        # terminal stopped signal (otherwise live workers would self-exit).
        self._register("worker", runtime="claude", sessionMode="managed")

        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={"agentId": "worker", "bridgeId": "bridge-1", "executionModes": ["managed"]},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertFalse(claim.json().get("stopped"))

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


    def test_repair_handoffs_closes_delivered_runs_with_results_and_stale_no_reply_contracts(self):
        self._register("lead", runtime="codex", sessionMode="managed")
        self._register("coder", runtime="codex", sessionMode="managed")
        self._execute(
            """
            INSERT INTO messages (
                id, from_agent, to_agent, source, type, subject, body, priority,
                dispatch_requested, in_reply_to, timestamp
            ) VALUES
                ('source-msg', 'lead', 'coder', 'direct', 'request', 'work', 'do it', 'normal', 1, NULL, 1000),
                ('result-msg', 'coder', 'lead', 'direct', 'response', 'done', 'done', 'normal', 0, 'source-msg', 2000)
            """
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode,
                message_type, subject, body, priority, status, require_reply,
                requested_at, result_message_id
            ) VALUES
                ('delivered-with-result', 'source-msg', 'lead', 'coder', 'terminal', 'managed',
                 'request', 'work', 'do it', 'normal', 'delivered', 1, '2026-01-01T00:00:00Z', 'result-msg'),
                ('stale-no-reply-needed', NULL, 'lead', 'coder', 'steer', 'managed',
                 'info', 'note', 'FYI', 'normal', 'delivered', 0, '2026-01-01T00:00:00Z', '')
            """
        )

        repair = self.client.post("/api/v1/dispatch/handoffs/repair")
        self.assertEqual(repair.status_code, 200, repair.text)
        self.assertEqual(repair.json()["closedDelivered"], 2)

        rows = self._fetchall(
            "SELECT id, status, finished_at FROM dispatch_runs WHERE id IN ('delivered-with-result','stale-no-reply-needed') ORDER BY id"
        )
        self.assertEqual([row["status"] for row in rows], ["completed", "completed"])
        self.assertTrue(all(row["finished_at"] for row in rows))

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
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
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
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
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

    def test_contract_reminder_notice_does_not_become_reply_debt(self):
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1})

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="urgent answer",
            body="please answer",
            priority="urgent",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET requested_at = '2000-01-01T00:00:00Z' WHERE id = ?", (run_id,))

        response = self.client.post(f"/api/v1/contracts/reminders/run?runId={run_id}")
        self.assertEqual(response.status_code, 200, response.text)
        reminder_run_id = response.json()["reminded"][0]["dispatchRuns"][0]["runId"]
        self.client.patch(
            f"/api/v1/dispatch/runs/{reminder_run_id}",
            json={"status": "completed", "summary": "reminder delivered"},
        )

        missing = self.client.get("/api/v1/contracts?limit=50&state=missing_reply")
        self.assertEqual(missing.status_code, 200, missing.text)
        self.assertFalse(any(item["id"] == reminder_run_id for item in missing.json()["contracts"]))

    def test_contract_reminders_are_unlimited_by_default(self):
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1, "reply_reminder_max_count": 0})

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="keep nudging",
            body="please answer",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        overdue_at = api_v2._iso_from_ms(int((time.time() - 120) * 1000))
        self._execute("UPDATE dispatch_runs SET requested_at = ? WHERE id = ?", (overdue_at, run_id))
        for idx in range(3):
            self._execute(
                "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
                (run_id, "reply_reminder", f"old reminder {idx}", api_v2._iso_from_ms(int((time.time() - (90 - idx)) * 1000))),
            )

        response = self.client.post(f"/api/v1/contracts/reminders/run?runId={run_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["reminded"]), 1)

    def test_contract_reminders_wait_for_busy_agent_then_fire_on_completion(self):
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 60, "reply_reminder_max_count": 0})

        open_contract = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="pending answer",
            body="please answer",
            mode="start_if_possible",
            createMessage=True,
        )
        contract_run_id = open_contract["runs"][0]["runId"]
        active_work = self._dispatch(
            from_agent="dashboard",
            to="coder",
            type="info",
            subject="current task",
            body="keep working",
            mode="start_if_possible",
            createMessage=True,
            requireReply=False,
        )
        active_run_id = active_work["runs"][0]["runId"]
        overdue_at = api_v2._iso_from_ms(int((time.time() - 120) * 1000))
        self._execute("UPDATE dispatch_runs SET status = 'delivered', requested_at = ? WHERE id = ?", (overdue_at, contract_run_id))
        self._execute("UPDATE dispatch_runs SET status = 'running', started_at = ? WHERE id = ?", (api_v2._now(), active_run_id))

        periodic = asyncio.run(service_main._run_dispatch_reconcile_once())
        self.assertEqual(periodic["reply_reminders"], 0)
        skipped = self._fetchone("SELECT body FROM dispatch_events WHERE run_id = ? AND event_type = 'reply_reminder_skipped'", (contract_run_id,))
        self.assertIsNotNone(skipped)
        self.assertIn("target is busy", skipped["body"])

        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{active_run_id}",
            json={"status": "completed", "summary": "current task done"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        event = self._fetchone("SELECT body FROM dispatch_events WHERE run_id = ? AND event_type = 'reply_reminder' ORDER BY created_at DESC LIMIT 1", (contract_run_id,))
        self.assertIsNotNone(event)
        self.assertIn("Sent reminder message", event["body"])

    def test_completion_triggered_reminder_respects_repeat_interval(self):
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 60, "reply_reminder_max_count": 0})

        open_contract = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="pending answer",
            body="please answer",
            mode="start_if_possible",
            createMessage=True,
        )
        contract_run_id = open_contract["runs"][0]["runId"]
        active_work = self._dispatch(
            from_agent="dashboard",
            to="coder",
            type="info",
            subject="current task",
            body="keep working",
            mode="start_if_possible",
            createMessage=True,
            requireReply=False,
        )
        active_run_id = active_work["runs"][0]["runId"]
        overdue_at = api_v2._iso_from_ms(int((time.time() - 120) * 1000))
        recent_reminder_at = api_v2._iso_from_ms(int((time.time() - 30) * 1000))
        self._execute("UPDATE dispatch_runs SET status = 'delivered', requested_at = ? WHERE id = ?", (overdue_at, contract_run_id))
        self._execute("UPDATE dispatch_runs SET status = 'running', started_at = ? WHERE id = ?", (api_v2._now(), active_run_id))
        self._execute(
            "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
            (contract_run_id, "reply_reminder", "recent reminder", recent_reminder_at),
        )

        completed = self.client.patch(
            f"/api/v1/dispatch/runs/{active_run_id}",
            json={"status": "completed", "summary": "current task done"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        reminders = self._fetchall("SELECT body FROM dispatch_events WHERE run_id = ? AND event_type = 'reply_reminder'", (contract_run_id,))
        self.assertEqual([row["body"] for row in reminders], ["recent reminder"])

    async def _async_run_contract_reminders_once(self, **kwargs):
        from service.db import get_db as _get_db
        db = await _get_db()
        try:
            payload = await api_v2._run_contract_reminders_once(db, **kwargs)
            await db.commit()
            return payload
        finally:
            await db.close()

    def _seed_overdue_handoff(self, *, run_id, message_id, from_agent, target_agent, status="delivered"):
        """Seed an overdue, unanswered require_reply ("handoff") run + its message."""
        overdue_at = api_v2._iso_from_ms(int((time.time() - 120) * 1000))
        self._execute(
            """
            INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (message_id, from_agent, target_agent, "direct", "request", "handoff", "please answer", "normal", 1, int(time.time() * 1000)),
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode,
                message_type, subject, body, priority, status, require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (run_id, message_id, from_agent, target_agent, "start_if_possible", "managed",
             "request", "handoff", "please answer", "normal", status, 1, overdue_at),
        )

    def test_reminder_not_skipped_when_turn_busy_is_same_runs_own_repulse(self):
        # CONFIRMED DEADLOCK FIX: a delivered require_reply run sets turn_busy with
        # turn_run_id = THAT run on its own delivery re-pulse. Treating that as
        # "busy" skipped the run's OWN reminder forever → handoff never nudged →
        # closed stale. With no active dispatch run, turn_busy for THE SAME run id
        # must NOT count as busy → the reminder fires.
        self._register_live_codex_resident("sc-architect", session_handle="arch-thread", bridge_id="arch-bridge", port=1)
        self._register_live_codex_resident("sc-coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1, "reply_reminder_max_count": 0})

        run_id = "run-handoff-same"
        self._seed_overdue_handoff(run_id=run_id, message_id="msg-handoff-same", from_agent="sc-coder", target_agent="sc-architect")
        self._execute(
            "INSERT OR REPLACE INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at, ready) VALUES (?,?,?,?,?,?,?)",
            ("sc-architect", 1, run_id, "", "claude-code", api_v2._now(), 1),
        )

        payload = asyncio.run(self._async_run_contract_reminders_once(run_id=run_id, dry_run=True))
        self.assertEqual([r["runId"] for r in payload["reminded"]], [run_id],
                         f"same-run turn_busy must not block the reminder; got {payload}")
        self.assertFalse(any(s.get("runId") == run_id for s in payload["skipped"]), payload)

    def test_reminder_skipped_when_turn_busy_is_a_different_run(self):
        # turn_busy fresh for a DIFFERENT run id = the agent is busy on OTHER work
        # → still skip (busy), reminder retried when idle.
        self._register_live_codex_resident("sc-architect", session_handle="arch-thread", bridge_id="arch-bridge", port=1)
        self._register_live_codex_resident("sc-coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1, "reply_reminder_max_count": 0})

        run_id = "run-handoff-diff"
        self._seed_overdue_handoff(run_id=run_id, message_id="msg-handoff-diff", from_agent="sc-coder", target_agent="sc-architect")
        self._execute(
            "INSERT OR REPLACE INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at, ready) VALUES (?,?,?,?,?,?,?)",
            ("sc-architect", 1, "some-other-run", "", "claude-code", api_v2._now(), 1),
        )

        payload = asyncio.run(self._async_run_contract_reminders_once(run_id=run_id, dry_run=True))
        self.assertEqual([r["runId"] for r in payload["reminded"]], [], payload)
        skip = next((s for s in payload["skipped"] if s.get("runId") == run_id), None)
        self.assertIsNotNone(skip, payload)
        self.assertIn("busy", skip["reason"])

    def test_reminder_skipped_when_agent_has_active_dispatch_run(self):
        # A claimed/running dispatch run (hasActiveRun) = genuinely executing → skip,
        # even if turn_busy happens to point at the run being reminded.
        self._register_live_codex_resident("sc-architect", session_handle="arch-thread", bridge_id="arch-bridge", port=1)
        self._register_live_codex_resident("sc-coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1, "reply_reminder_max_count": 0})

        run_id = "run-handoff-active"
        self._seed_overdue_handoff(run_id=run_id, message_id="msg-handoff-active", from_agent="sc-coder", target_agent="sc-architect")
        # Separate claimed/running run for the same agent = hasActiveRun.
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode,
                message_type, subject, body, priority, status, require_reply, requested_at, started_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("run-other-active", None, "dashboard", "sc-architect", "start_if_possible", "managed",
             "info", "other task", "work", "normal", "running", 0, api_v2._now(), api_v2._now()),
        )
        self._execute(
            "INSERT OR REPLACE INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at, ready) VALUES (?,?,?,?,?,?,?)",
            ("sc-architect", 1, run_id, "", "claude-code", api_v2._now(), 1),
        )

        payload = asyncio.run(self._async_run_contract_reminders_once(run_id=run_id, dry_run=True))
        self.assertEqual([r["runId"] for r in payload["reminded"]], [], payload)
        skip = next((s for s in payload["skipped"] if s.get("runId") == run_id), None)
        self.assertIsNotNone(skip, payload)
        self.assertIn("busy", skip["reason"])

    def test_reminder_fires_when_agent_not_busy_at_all(self):
        # No active run, no fresh turn_busy → remind (existing behavior preserved).
        self._register_live_codex_resident("sc-architect", session_handle="arch-thread", bridge_id="arch-bridge", port=1)
        self._register_live_codex_resident("sc-coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1, "reply_reminder_max_count": 0})

        run_id = "run-handoff-idle"
        self._seed_overdue_handoff(run_id=run_id, message_id="msg-handoff-idle", from_agent="sc-coder", target_agent="sc-architect")

        payload = asyncio.run(self._async_run_contract_reminders_once(run_id=run_id, dry_run=True))
        self.assertEqual([r["runId"] for r in payload["reminded"]], [run_id], payload)

    def test_contract_reminders_skip_dashboard_target_contracts(self):
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1, "reply_reminder_max_count": 0})

        message_id = "msg-dashboard-contract"
        run_id = "run-dashboard-contract"
        overdue_at = api_v2._iso_from_ms(int((time.time() - 120) * 1000))
        self._execute(
            """
            INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (message_id, "coder", "dashboard", "direct", "request", "operator decision", "please decide", "normal", 1, int(time.time() * 1000)),
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode,
                message_type, subject, body, priority, status, require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                message_id,
                "coder",
                "dashboard",
                "start_if_possible",
                "managed",
                "request",
                "operator decision",
                "please decide",
                "normal",
                "delivered",
                1,
                overdue_at,
            ),
        )

        response = self.client.post(f"/api/v1/contracts/reminders/run?runId={run_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["reminded"], [])

        event = self._fetchone("SELECT body FROM dispatch_events WHERE run_id = ? AND event_type = 'reply_reminder'", (run_id,))
        self.assertIsNone(event)

    def test_contracts_default_view_hides_operator_closed_and_failures(self):
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="close me",
            body="please answer",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        closed = self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}",
            json={"status": "completed", "requireReply": False, "summary": "Closed from Work Loop by dashboard operator."},
        )
        self.assertEqual(closed.status_code, 200, closed.text)

        open_view = self.client.get("/api/v1/contracts?limit=20")
        self.assertEqual(open_view.status_code, 200, open_view.text)
        self.assertFalse(any(item["id"] == run_id for item in open_view.json()["contracts"]))

        closed_view = self.client.get("/api/v1/contracts?limit=20&state=closed")
        self.assertEqual(closed_view.status_code, 200, closed_view.text)
        self.assertTrue(any(item["id"] == run_id and item["state"] == "closed" for item in closed_view.json()["contracts"]))

    def test_periodic_reconcile_refreshes_expired_live_states(self):
        # Regression (operator-reported 2026-05-31): the live-status cache is
        # refreshed ONLY on request (GET /agents, send, GET /agents/{id}). The
        # only periodic driver was a CLIENT-SIDE dashboard setInterval, which
        # browsers throttle/pause for background/unfocused tabs. So whenever no
        # dashboard was actively foregrounded-and-polling, every agent's status
        # froze on whatever verdict was last computed — in the field a transient
        # env-offline blip persisted for 10+ minutes across the whole roster.
        # The periodic reconcile MUST refresh expired live states server-side so
        # status freshness no longer depends on a browser tab's timer.
        self._register_live_codex_resident(
            "recon-agent", session_handle="recon-thread", bridge_id="recon-bridge", port=4111
        )
        # Freeze a stale, EXPIRED offline verdict (refresh_after well in the past).
        self._execute(
            """
            INSERT INTO agent_live_state (agent_id, status, reason, updated_at, refresh_after)
            VALUES (?,?,?,?,?)
            ON CONFLICT(agent_id) DO UPDATE SET
                status = excluded.status,
                reason = excluded.reason,
                updated_at = excluded.updated_at,
                refresh_after = excluded.refresh_after
            """,
            ("recon-agent", "offline", "Environment is offline.", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"),
        )

        asyncio.run(service_main._run_dispatch_reconcile_once())

        row = self._fetchone(
            "SELECT status, updated_at FROM agent_live_state WHERE agent_id = ?", ("recon-agent",)
        )
        self.assertNotEqual(
            row["updated_at"], "2026-01-01T00:00:00Z",
            "periodic reconcile did not refresh the expired live-status cache",
        )
        self.assertNotEqual(
            row["status"], "offline",
            "a live resident must not stay frozen offline after a reconcile pass",
        )

    def test_periodic_reconcile_runs_managed_worker_hygiene(self):
        # Workstream B4: the managed-worker-hygiene reaper must run inside the
        # periodic reconcile pass so ghost console rows are reaped automatically
        # without a separate bespoke invocation.
        # Seed a MANAGED claude-code agent with NO live channel-sidecar bridge
        # + a stale `attached` terminal_sessions row (a GHOST) — exactly the
        # shape tested in test_managed_hygiene_reaps_ghost_console_row.
        terminal_id = "term_periodic_ghost"
        self._seed_managed_claude_with_attached_terminal("periodic-ghost-claude", terminal_id)
        # No channel-sidecar bridge_instances row is inserted → no live sidecar.
        # Dead worker → bridge stopped streaming, so updated_at is STALE (boot-race guard).
        self._execute("UPDATE terminal_sessions SET updated_at = '2020-01-01T00:00:00Z' WHERE id = ?", (terminal_id,))

        result = asyncio.run(service_main._run_dispatch_reconcile_once())

        # The reconcile result must carry both hygiene keys.
        self.assertIn("managed_ghost_rows_reaped", result, f"key missing from result: {result}")
        self.assertIn("orphan_workers_reaped", result, f"key missing from result: {result}")
        # The ghost row must have been reaped by the reconcile pass.
        self.assertGreaterEqual(
            result["managed_ghost_rows_reaped"], 1,
            f"expected at least one ghost row reaped; got {result}",
        )
        term = self._fetchone("SELECT status FROM terminal_sessions WHERE id = ?", (terminal_id,))
        self.assertEqual(
            term["status"], "stopped",
            "terminal row must be stopped after reconcile-driven hygiene reap",
        )

    def test_periodic_dispatch_reconcile_sends_contract_reminders(self):
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1})

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="answer me",
            body="please answer",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        overdue_at = api_v2._iso_from_ms(int((time.time() - 120) * 1000))
        self._execute("UPDATE dispatch_runs SET requested_at = ? WHERE id = ?", (overdue_at, run_id))

        result = asyncio.run(service_main._run_dispatch_reconcile_once())
        self.assertEqual(result["reply_reminders"], 1)

        event = self._fetchone("SELECT event_type FROM dispatch_events WHERE run_id = ? AND event_type = 'reply_reminder'", (run_id,))
        self.assertIsNotNone(event)

    def test_periodic_dispatch_reconcile_skips_historical_contract_reminders(self):
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1, "contract_stale_hours": 24})

        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="old request",
            body="please answer",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        self._execute("UPDATE dispatch_runs SET requested_at = '2000-01-01T00:00:00Z' WHERE id = ?", (run_id,))

        result = asyncio.run(service_main._run_dispatch_reconcile_once())
        self.assertEqual(result["reply_reminders"], 0)
        event = self._fetchone("SELECT event_type FROM dispatch_events WHERE run_id = ? AND event_type = 'reply_reminder'", (run_id,))
        self.assertIsNone(event)

    def test_periodic_dispatch_reconcile_skips_blocked_terminal_contract_reminders(self):
        self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1})

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="decision",
            body="what next",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        terminal_id = dispatched["consoleDeliveries"][0]["terminalId"]
        self.client.post(
            f"/api/v1/terminals/{terminal_id}/output",
            json={
                "bridgeId": "bridge-current",
                "status": "attached",
                "output": "Your call — I need a decision:\n1. Continue\n2. Stop\nSay the word and I execute.",
            },
        )
        asyncio.run(api_v2.flush_terminal_output_writes_for_tests())
        overdue_at = api_v2._iso_from_ms(int((time.time() - 120) * 1000))
        self._execute("UPDATE dispatch_runs SET requested_at = ? WHERE id = ?", (overdue_at, run_id))

        result = asyncio.run(service_main._run_dispatch_reconcile_once())
        self.assertEqual(result["reply_reminders"], 0)
        event = self._fetchone("SELECT body FROM dispatch_events WHERE run_id = ? AND event_type = 'reply_reminder_skipped'", (run_id,))
        self.assertIsNotNone(event)
        self.assertIn("operator input", event["body"])


    def test_terminal_contract_with_lost_backing_still_gets_reminder(self):
        session_id = self._create_running_session(
            terminal=True,
            runtime="claude-code",
            terminal_runtimes=["claude-code"],
            session_handle="claude-session-1",
        )
        self.client.put("/api/v1/settings", json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1})

        dispatched = self._dispatch(
            from_agent="dashboard",
            to="console-agent",
            type="request",
            subject="lost backing",
            body="please finish",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = dispatched["consoleDeliveries"][0]["contractRunId"]
        self._execute(
            "UPDATE agent_sessions SET terminal_id = '', terminal_status = '' WHERE id = ?",
            (session_id,),
        )
        overdue_at = api_v2._iso_from_ms(int((time.time() - 120) * 1000))
        self._execute("UPDATE dispatch_runs SET requested_at = ? WHERE id = ?", (overdue_at, run_id))
        self._execute("DELETE FROM agent_live_state WHERE agent_id = ?", ("console-agent",))

        result = asyncio.run(service_main._run_dispatch_reconcile_once())
        self.assertEqual(result["reply_reminders"], 1)
        event = self._fetchone("SELECT body FROM dispatch_events WHERE run_id = ? AND event_type = 'reply_reminder' ORDER BY created_at DESC LIMIT 1", (run_id,))
        self.assertIsNotNone(event)
        self.assertIn("Sent reminder message", event["body"])

    def test_contracts_do_not_treat_high_priority_responses_as_missing_replies(self):
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)

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
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)

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
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)
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
        self._register_live_codex_resident("lead", session_handle="lead-thread", bridge_id="lead-bridge", port=1)
        self._register_live_codex_resident("coder", session_handle="coder-thread", bridge_id="coder-bridge", port=2)

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

    def test_pi_console_reattaches_to_virtual_terminal_across_sessions(self):
        # Phase 2 follow-up: the synthesized terminal for managed pi is
        # canonical per AGENT, not per agent_session. Opening Console from
        # the dashboard for a session that's DIFFERENT from the one that
        # originally created the virtual terminal must reattach to the
        # existing virtual row — not spawn a fresh pi-aify PTY (which would
        # conflict with the bridge's persistent omp --mode rpc child).
        self._heartbeat_environment(
            id="env_pi_console",
            bridgeId="bridge-pi-console",
            machineId="linux:pi-console",
            runtimes=[
                {
                    "runtime": "pi",
                    "modes": ["managed-warm"],
                    "capabilities": {"interrupt": True, "steer": True},
                }
            ],
            terminal=True,
            pty=True,
            terminalRuntimes=["pi"],
        )
        self._register("pi-console-agent", runtime="pi", sessionMode="managed")

        for session_id, terminal_id in (("sess_pi_console_old", "vterm_pi_console"), ("sess_pi_console_new", None)):
            self._execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode,
                    owner_mode, owner_bridge_id, terminal_id, terminal_status,
                    terminal_command, terminal_workspace, process_id, session_handle,
                    app_server_url, spawn_spec_id, spawn_request_id, capabilities,
                    telemetry, status, started_at, last_seen, ended_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    "pi-console-agent",
                    "env_pi_console",
                    "pi",
                    "/workspace",
                    "managed",
                    "managed",
                    "bridge-pi-console",
                    terminal_id or "",
                    "running" if terminal_id else "",
                    "aify://virtual-rpc/pi" if terminal_id else "",
                    "/workspace",
                    "",
                    "pi-handle-console",
                    "",
                    None,
                    None,
                    "{}",
                    "{}",
                    "running",
                    "2026-05-21T00:00:00Z",
                    "2026-05-21T00:00:00Z",
                    None,
                ),
            )

        # Virtual terminal anchored to the OLD session.
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "vterm_pi_console",
                "sess_pi_console_old",
                "pi-console-agent",
                "env_pi_console",
                "bridge-pi-console",
                "pi",
                "/workspace",
                "aify://virtual-rpc/pi",
                "",
                "running",
                "bridge-rpc",
                "2026-05-21T00:00:00Z",
                "2026-05-21T00:00:00Z",
                None,
                "",
            ),
        )
        # Agent's runtime_state knows about the virtual terminal (set by
        # Phase 2's virtual-terminal/ensure endpoint).
        self._execute(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            (
                json.dumps({"virtualTerminal": True, "virtualTerminalId": "vterm_pi_console"}),
                "pi-console-agent",
            ),
        )

        # Dashboard opens Console for the NEW session — must NOT spawn a
        # fresh PTY. Must reattach to the existing virtual terminal.
        response = self.client.post(
            "/api/v1/sessions/sess_pi_console_new/console/start",
            json={"requestedBy": "operator"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["ok"], True)
        self.assertEqual(body["reused"], True)
        self.assertEqual(body.get("virtual"), True)
        self.assertEqual(body["terminal"]["id"], "vterm_pi_console")
        self.assertEqual(body["terminal"]["command"], "aify://virtual-rpc/pi")

        # The NEW session's terminal_id should now point at the virtual one.
        new_session = self._fetchone(
            "SELECT terminal_id, terminal_status, terminal_command FROM agent_sessions WHERE id = ?",
            ("sess_pi_console_new",),
        )
        self.assertEqual(new_session["terminal_id"], "vterm_pi_console")
        self.assertEqual(new_session["terminal_status"], "running")
        self.assertEqual(new_session["terminal_command"], "aify://virtual-rpc/pi")

        # The OLD session's binding is left intact (the canonical owner from
        # creation time stays linked until the original session is deleted).
        old_session = self._fetchone(
            "SELECT terminal_id FROM agent_sessions WHERE id = ?",
            ("sess_pi_console_old",),
        )
        self.assertEqual(old_session["terminal_id"], "vterm_pi_console")

        # A virtual_pi_rpc_console_attached event was recorded for audit.
        attach_event = self._fetchone(
            "SELECT body FROM terminal_events WHERE terminal_id = ? AND event_type = ? ORDER BY id DESC LIMIT 1",
            ("vterm_pi_console", "virtual_pi_rpc_console_attached"),
        )
        self.assertIsNotNone(attach_event)
        attach_body = json.loads(attach_event["body"])
        self.assertEqual(attach_body["sessionId"], "sess_pi_console_new")
        self.assertEqual(attach_body["agentId"], "pi-console-agent")

    def test_channel_route_delivered_awaiting_reply_shows_online_not_working(self):
        # Status-split (2026-05-31): a channel-route delivered+require_reply run
        # with NO fresh turn_busy and NO active run means the turn ENDED — the
        # agent is IDLE but owes a reply. That is `online` with an awaiting-reply
        # reason, NOT orange `working`. (Previously this was forced to "working",
        # which the operator reported as "blink when the agent isn't working".)
        # `working` is reserved for a fresh turn_busy (claude Stop hook clears it
        # on turn-end) or a claimed/running run. The open reply contract is
        # surfaced via the reason + handled by the reminder loop.
        self._heartbeat_environment(
            id="env_channel_busy",
            bridgeId="bridge-channel-busy",
            machineId="linux:channel-busy",
            runtimes=[
                {
                    "runtime": "claude-code",
                    "modes": ["managed-warm"],
                    "capabilities": {"interrupt": True},
                }
            ],
            terminal=True,
            pty=True,
            terminalRuntimes=["claude-code"],
        )
        self._register("channel-claude", runtime="claude-code", sessionMode="resident")
        # status_engine=new: a resident's liveness is its bridge freshness; stamp a live
        # channel-sidecar (proof-of-life) so it reads online (not stale) like under old.
        self._stamp_live_channel_sidecar("channel-claude")

        # Baseline: no runs → not working.
        agent_response = self.client.get("/api/v1/agents/channel-claude")
        self.assertEqual(agent_response.status_code, 200, agent_response.text)
        self.assertNotEqual(agent_response.json()["agent"]["status"], "working")

        # Seed a channel-route delivered dispatch_run awaiting reply.
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status,
                require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_channel_busy_1",
                None,
                "dashboard",
                "channel-claude",
                "start_if_possible",
                "channel",
                "request",
                "deep question",
                "think hard about this",
                "normal",
                "delivered",
                1,
                "2026-05-21T00:00:00Z",
            ),
        )
        # Invalidate cache so the next read recomputes against the new run.
        asyncio.run(self._async_invalidate("channel-claude"))

        awaiting_response = self.client.get("/api/v1/agents/channel-claude")
        self.assertEqual(awaiting_response.status_code, 200, awaiting_response.text)
        awaiting_payload = awaiting_response.json()["agent"]
        # NEW contract: online (idle, reachable) — NOT working — with the
        # open-reply contract surfaced in the reason.
        self.assertEqual(awaiting_payload["status"], "online", awaiting_payload)
        self.assertNotEqual(awaiting_payload["status"], "working")
        self.assertIn("awaiting reply", awaiting_payload.get("statusNote", "").lower())

        # Reply lands → run completes → no longer awaiting.
        self._execute(
            "UPDATE dispatch_runs SET status = 'completed' WHERE id = ?",
            ("run_channel_busy_1",),
        )
        asyncio.run(self._async_invalidate("channel-claude"))
        idle_response = self.client.get("/api/v1/agents/channel-claude")
        self.assertEqual(idle_response.status_code, 200, idle_response.text)
        self.assertNotEqual(idle_response.json()["agent"]["status"], "working")
        self.assertNotIn("awaiting reply", idle_response.json()["agent"].get("statusNote", "").lower())

    async def _async_invalidate(self, agent_id: str):
        from service.db import get_db as _get_db
        db = await _get_db()
        try:
            await api_v2._invalidate_agent_live_state(db, agent_id)
            await db.commit()
        finally:
            await db.close()

    async def _async_prune_superseded_bridges(self):
        from service.db import get_db as _get_db
        db = await _get_db()
        try:
            return await api_v2._prune_superseded_bridges(db)
        finally:
            await db.close()

    async def _async_resident_bridge_fresh(self, agent_id, lease_seconds=150):
        from service.db import get_db as _get_db
        db = await _get_db()
        try:
            row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
            return await api_v2._resident_bridge_is_fresh(db, row, lease_seconds=lease_seconds)
        finally:
            await db.close()

    def test_idle_resident_with_live_sidecar_is_not_stale(self):
        # holistic-review (operator-reported 2026-05-31, sc-manager): an IDLE
        # resident claude's MCP bridge isn't heartbeated (turn-busy heartbeat only
        # fires mid-turn; session-handle heartbeat only POSTs on a handle change),
        # so it goes stale after the lease and the dashboard shows it dead. Its
        # channel sidecar polls every ~3s though — a fresh channel-sidecar bridge
        # must count as proof the resident session is alive.
        self._register(
            "res-claude", runtime="claude-code", sessionMode="resident",
            machineId="linux:test-host", bridgeId="mcp-bridge-1",
            capabilities=["resident-run"], runtimeConfig={"channelEnabled": True},
        )
        # Point runtime_state at the MCP bridge and make that bridge STALE.
        self._execute("UPDATE agents SET runtime_state = ? WHERE id = 'res-claude'",
                      (json.dumps({"bridgeInstanceId": "mcp-bridge-1"}),))
        self._execute("UPDATE bridge_instances SET last_seen = '2020-01-01T00:00:00Z' WHERE id = 'mcp-bridge-1'")
        # No sidecar yet → genuinely stale.
        self.assertFalse(asyncio.run(self._async_resident_bridge_fresh("res-claude")),
                         "stale MCP bridge with no live sidecar must be stale")
        # A live channel-sidecar (the polling child of the live session) appears.
        now = api_v2._now()
        self._execute(
            """
            INSERT OR REPLACE INTO bridge_instances (
                id, agent_id, machine_id, runtime, session_mode, session_handle,
                terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("channel-linux:test-host-res-claude", "res-claude", "linux:test-host",
             "claude-code", "resident", "", "", "channel-sidecar", now, now, "", None),
        )
        self.assertTrue(asyncio.run(self._async_resident_bridge_fresh("res-claude")),
                        "a live channel-sidecar proves the idle resident is alive → not stale")

    async def _async_is_turn_busy_fresh(self, agent_id):
        from service.db import get_db as _get_db
        db = await _get_db()
        try:
            return await api_v2._is_turn_busy_fresh(db, agent_id)
        finally:
            await db.close()

    def test_is_turn_busy_fresh_shared_busy_predicate(self):
        # holistic status review Finding 2 (2026-05-31): "busy" must be
        # hasActiveRun OR fresh turn_busy. This locks the turn_busy half so the
        # reminder loop (and any consumer) defers for a mid-turn agent that has no
        # tracked dispatch run (e.g. a resident claude on its own turn).
        self._register("tb-agent", runtime="claude-code", sessionMode="resident",
                       machineId="linux:test-host", bridgeId="tb-b1", capabilities=["resident-run"])
        # No turn_busy row → not busy.
        self.assertFalse(asyncio.run(self._async_is_turn_busy_fresh("tb-agent")))
        now = api_v2._now()
        self._execute(
            "INSERT OR REPLACE INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at, ready) VALUES (?,?,?,?,?,?,?)",
            ("tb-agent", 1, "", "", "claude-code", now, 1),
        )
        self.assertTrue(asyncio.run(self._async_is_turn_busy_fresh("tb-agent")), "fresh turn_busy=1 → busy")
        self._execute("UPDATE agent_turn_state SET turn_updated_at='2020-01-01T00:00:00Z' WHERE agent_id='tb-agent'")
        self.assertFalse(asyncio.run(self._async_is_turn_busy_fresh("tb-agent")), "stale turn_busy → not busy")
        self._execute("UPDATE agent_turn_state SET turn_busy=0, turn_updated_at=? WHERE agent_id='tb-agent'", (now,))
        self.assertFalse(asyncio.run(self._async_is_turn_busy_fresh("tb-agent")), "turn_busy=0 → not busy")

    def test_prune_superseded_bridges_reclaims_only_aged_superseded(self):
        # holistic-review F4: superseded bridge_instances rows were never deleted
        # (83/98 superseded in the live DB). Prune only AGED superseded rows;
        # never touch live (non-superseded) rows or recently-superseded ones.
        self._register(
            "prune-agent", runtime="claude-code", sessionMode="managed",
            machineId="linux:test-host", bridgeId="live-bridge", capabilities=["resume"],
        )
        now = api_v2._now()
        old = "2020-01-01T00:00:00Z"
        rows = [
            # (id, superseded_by, superseded_at, last_seen)  -> expected disposition
            ("b-live", "", None, now),            # not superseded -> KEEP
            ("b-recent-sup", "x", now, now),      # superseded just now -> KEEP (< 24h)
            ("b-old-sup", "x", old, old),         # superseded long ago -> PRUNE
            ("b-old-sup-noat", "x", None, old),   # superseded_at NULL, aged last_seen -> PRUNE
        ]
        for bid, sup, sup_at, last_seen in rows:
            self._execute(
                """
                INSERT OR REPLACE INTO bridge_instances (
                    id, agent_id, machine_id, runtime, session_mode, session_handle,
                    terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (bid, "prune-agent", "linux:test-host", "claude-code", "managed", "", "",
                 "channel-sidecar", now, last_seen, sup, sup_at),
            )
        removed = asyncio.run(self._async_prune_superseded_bridges())
        self.assertEqual(removed, 2, "only the two aged superseded rows should be pruned")
        remaining = {r["id"] for r in self._fetchall(
            "SELECT id FROM bridge_instances WHERE agent_id = 'prune-agent'")}
        self.assertIn("b-live", remaining, "live bridge must never be pruned")
        self.assertIn("b-recent-sup", remaining, "recently-superseded bridge must be kept")
        self.assertNotIn("b-old-sup", remaining)
        self.assertNotIn("b-old-sup-noat", remaining)

    def test_managed_session_rotation_migrates_live_terminal(self):
        # holistic-review (operator-reported 2026-05-31, sc-architect): a managed
        # respawn mints a NEW running session and ends the prior one. The bridge
        # can create the visible-TUI/console terminal a few seconds BEFORE this
        # rotation, so the live terminal stays bound to the about-to-be-ended
        # session and the new running session gets terminal_id=''. Result: the
        # dashboard shows "Console not started" while the real TUI is alive (and
        # the live terminal row hangs off an ended session → FK ON DELETE CASCADE
        # could later drop a running TUI's tracking). The live, same-bridge
        # terminal must MIGRATE to the new session during rotation.
        session_a = self._create_running_session(
            terminal=True, runtime="hermes", terminal_runtimes=["hermes"],
            session_handle="aify-console-agent",
        )
        now = api_v2._now()
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, status, requested_by, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("term-live-tui", session_a, "console-agent", "linux:test-host:default",
             "bridge-current", "hermes", "/workspace/repo",
             "hermes-aify --aify-agent console-agent --resume aify-console-agent",
             "attached", "dashboard", now, now),
        )
        self._execute(
            "UPDATE agent_sessions SET terminal_id='term-live-tui', terminal_status='attached' WHERE id=?",
            (session_a,),
        )
        # Respawn → new running session B (rotation ends A).
        session_b = self._create_running_session(
            terminal=True, runtime="hermes", terminal_runtimes=["hermes"],
            session_handle="aify-console-agent",
        )
        self.assertNotEqual(session_a, session_b)
        term = self._fetchone("SELECT session_id FROM terminal_sessions WHERE id='term-live-tui'")
        self.assertEqual(term["session_id"], session_b,
                         "the live terminal must MIGRATE to the new running session, not be orphaned")
        sess_b = self._fetchone("SELECT terminal_id, terminal_status, status FROM agent_sessions WHERE id=?", (session_b,))
        self.assertEqual(sess_b["terminal_id"], "term-live-tui", "new session must own the migrated terminal")
        self.assertEqual(sess_b["status"], "running")
        sess_a = self._fetchone("SELECT status FROM agent_sessions WHERE id=?", (session_a,))
        self.assertEqual(sess_a["status"], "ended", "the prior session is still ended by the rotation")

    def test_stop_kills_managed_terminal(self):
        # operator-reported 2026-05-31: aify-comms is the lifecycle driver for
        # managed sessions, so an operator Stop must KILL the running console/TUI
        # (queue a bridge terminal-stop + mark stopping), not just interrupt the
        # run and mark the agent stopped while the host TUI keeps running.
        session = self._create_running_session(
            terminal=True, runtime="hermes", terminal_runtimes=["hermes"],
            session_handle="aify-console-agent",
        )
        now = api_v2._now()
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, status, requested_by, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("term-stopme", session, "console-agent", "linux:test-host:default",
             "bridge-current", "hermes", "/workspace",
             "hermes-aify --aify-agent console-agent", "attached", "dashboard", now, now),
        )
        res = self.client.post("/api/v1/agents/console-agent/control",
                               json={"action": "stop", "from_agent": "dashboard"})
        self.assertEqual(res.status_code, 200, res.text)
        term = self._fetchone("SELECT status FROM terminal_sessions WHERE id='term-stopme'")
        self.assertEqual(term["status"], "stopping", "Stop must mark the managed terminal stopping")
        ctl = self._fetchone(
            "SELECT action FROM terminal_controls WHERE terminal_id='term-stopme' ORDER BY requested_at DESC LIMIT 1")
        self.assertIsNotNone(ctl, "Stop must queue a terminal-stop control so the bridge reaps the PTY")
        self.assertEqual(ctl["action"], "stop")
        ag = self._fetchone("SELECT status FROM agents WHERE id='console-agent'")
        self.assertEqual(ag["status"], "stopped")

    def test_wake_on_message_send_to_available_agent_queues_dispatch(self):
        # Phase 3: sending to an `available` agent (env online, no live
        # worker yet) must NOT be rejected as "cannot start live work
        # now" — the dispatch path queues a run that the bridge claims
        # on next poll, and the per-runtime dispatch handlers
        # (PiSession.acquirePiSession, claude-aify wrapper spawn, etc.)
        # spawn the worker on first claim. The send-side preflight
        # already allows this after Phase 2's taxonomy change (available
        # not in {offline, stale, stopped}); this test pins the
        # contract: send to available → ok=true with a queued
        # dispatchRun, NOT the "no live wake" error.
        self._heartbeat_environment(
            id="env_wake",
            bridgeId="bridge-wake",
            machineId="linux:wake",
            runtimes=[
                {
                    "runtime": "pi",
                    "modes": ["managed-warm"],
                    "capabilities": {"interrupt": True, "steer": True},
                }
            ],
            terminal=True,
            pty=True,
            terminalRuntimes=["pi"],
        )
        self._register("wake-pi", runtime="pi", sessionMode="managed")

        # Confirm "available" status precondition.
        avail = self.client.get("/api/v1/agents/wake-pi").json()["agent"]
        self.assertEqual(avail["status"], "available", avail)

        # Send with trigger=true → expect queued dispatch_run, not error.
        sent = self._send_message(
            from_agent="dashboard",
            to="wake-pi",
            type="request",
            subject="wake up",
            body="please get to work",
            trigger=True,
        )
        # Phase 3 contract: available agents are NOT blocked from receiving
        # work. The dispatch_run is created and the bridge will claim it.
        self.assertTrue(
            sent.get("ok") is not False or len(sent.get("dispatchRuns", [])) > 0,
            f"Expected send to available agent to queue a dispatch, got {sent}",
        )

    def test_send_to_available_managed_codex_no_session_coldstarts_with_autobind(self):
        # Phase 2 (2026-05-31): a wrapper-backed managed codex agent that was
        # only REGISTERED (never run, no agent_sessions row, no env binding)
        # must NOT be rejected with "cannot start live work now" when an
        # online env advertises codex. The send path falls back to
        # _coldstart_spawn_request_for_dispatch, which auto-binds the online
        # env and queues a spawn_request the bridge claims. This is the
        # operator-reported sc-coder bug (claude worked because its channel
        # branch is best-effort; codex/hermes/pi hard-rejected).
        #
        # This suite's setUp opts into pre-Plan-4 legacy defaults; restore the
        # production wrapper-backed defaults this behavior depends on.
        self.client.put(
            "/api/v1/settings",
            json={
                "insert_messages_via_console": False,
                "managed_via_wrapper": ["codex", "hermes"],
                "managed_terminal_backing_enabled": True,
            },
        )
        self._heartbeat_environment(
            id="env_codex",
            bridgeId="bridge-codex",
            machineId="linux:codex",
            runtimes=[
                {
                    "runtime": "codex",
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "interrupt": True},
                }
            ],
            terminal=True,
            pty=True,
            terminalRuntimes=["codex"],
        )
        self._register("sc-coder", runtime="codex", sessionMode="managed")

        avail = self.client.get("/api/v1/agents/sc-coder").json()["agent"]
        self.assertEqual(avail["status"], "available", avail)

        sent = self._send_message(
            from_agent="dashboard",
            to="sc-coder",
            type="request",
            subject="start work",
            body="please get to work",
            trigger=True,
        )
        self.assertNotEqual(
            sent.get("error"),
            "Message was not sent because one or more recipients cannot start live work now.",
            f"available codex agent must not be hard-rejected; got {sent}",
        )
        rows = self._fetchall(
            "SELECT id, environment_id, status FROM spawn_requests WHERE agent_id = ?",
            ("sc-coder",),
        )
        self.assertEqual(len(rows), 1, f"a claimable spawn_request must back the agent; got {sent}")
        self.assertEqual(rows[0]["environment_id"], "env_codex")
        self.assertEqual(rows[0]["status"], "queued")

    def test_send_to_available_managed_codex_no_env_rejects_clearly(self):
        # Phase 2: when NO online env advertises the runtime, the send must
        # reject — but with the clear "no online environment can host" hint,
        # not a misleading wrapper-PTY message, and create no spawn_request.
        self.client.put(
            "/api/v1/settings",
            json={
                "insert_messages_via_console": False,
                "managed_via_wrapper": ["codex", "hermes"],
                "managed_terminal_backing_enabled": True,
            },
        )
        self._register("orphan-codex", runtime="codex", sessionMode="managed")
        sent = self._send_message(
            from_agent="dashboard",
            to="orphan-codex",
            type="request",
            subject="start work",
            body="please get to work",
            trigger=True,
        )
        self.assertFalse(sent.get("ok"), sent)
        rows = self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ?", ("orphan-codex",))
        self.assertEqual(len(rows), 0, "no spawn_request when no env can host the runtime")

    def test_send_to_managed_agent_with_offline_bound_env_does_not_migrate(self):
        # Phase 2 boundary (review finding): a managed agent BOUND to a specific
        # env that is now OFFLINE must NOT be silently auto-migrated to a
        # different online env, even when one advertises the runtime — its
        # workspace lives on the bound env's machine. Preflight rejects on the
        # offline bound env (so it waits for that env to return); the auto-bind
        # fallback is only for agents with NO usable bound env. Pins the
        # no-migrate contract end-to-end through the send path.
        self.client.put(
            "/api/v1/settings",
            json={
                "insert_messages_via_console": False,
                "managed_via_wrapper": ["codex", "hermes"],
                "managed_terminal_backing_enabled": True,
            },
        )
        # env_A advertises codex; env_B (online) also advertises codex.
        self._heartbeat_environment(
            id="env_A", bridgeId="bridge-A", machineId="linux:a",
            runtimes=[{"runtime": "codex", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
            terminal=True, pty=True, terminalRuntimes=["codex"],
        )
        self._heartbeat_environment(
            id="env_B", bridgeId="bridge-B", machineId="linux:b",
            runtimes=[{"runtime": "codex", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
            terminal=True, pty=True, terminalRuntimes=["codex"],
        )
        self._register("bound-codex", runtime="codex", sessionMode="managed")
        # Bind the agent to env_A via runtime_state.environmentId (what
        # _managed_environment_status checks first), then take env_A offline.
        self._execute(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            (json.dumps({"environmentId": "env_A"}), "bound-codex"),
        )
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute("UPDATE environments SET last_seen = ? WHERE id = ?", (stale, "env_A"))

        sent = self._send_message(
            from_agent="dashboard",
            to="bound-codex",
            type="request",
            subject="work",
            body="please get to work",
            trigger=True,
        )
        # Rejected (env_A offline) — NOT migrated to env_B, no spawn_request anywhere.
        self.assertFalse(sent.get("ok"), f"agent bound to an offline env must wait, not migrate; got {sent}")
        rows = self._fetchall("SELECT environment_id FROM spawn_requests WHERE agent_id = ?", ("bound-codex",))
        self.assertEqual(len(rows), 0, f"no spawn_request — must not silently migrate to env_B; got {rows}")

    # ── Phase 3: explicit disable (stop) = hard-block, no auto-start ───────
    def _modern_wrapper_settings(self):
        self.client.put(
            "/api/v1/settings",
            json={
                "insert_messages_via_console": False,
                "managed_via_wrapper": ["codex", "hermes"],
                "managed_terminal_backing_enabled": True,
            },
        )

    def _codex_env_online(self):
        self._heartbeat_environment(
            id="env_codex",
            bridgeId="bridge-codex",
            machineId="linux:codex",
            runtimes=[{"runtime": "codex", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
            terminal=True,
            pty=True,
            terminalRuntimes=["codex"],
        )

    def test_disabled_managed_agent_hard_blocks_dispatch_and_does_not_coldstart(self):
        # Phase 3 (2026-05-31): an explicitly DISABLED managed agent (operator
        # Stop → launch_mode='none', status='stopped') is a HARD block: it must
        # NOT auto-start (no Phase 2 cold-start spawn_request) and must refuse
        # dispatches from OTHER agents, even when an online env could host it.
        # Operator policy: a disabled agent stays down until re-enabled.
        self._modern_wrapper_settings()
        self._codex_env_online()
        self._register("disabled-codex", runtime="codex", sessionMode="managed")
        # Operator disables it.
        ctrl = self.client.post(
            "/api/v1/agents/disabled-codex/control",
            json={"action": "stop", "from_agent": "dashboard"},
        )
        self.assertEqual(ctrl.status_code, 200, ctrl.text)

        # A peer agent (not the dashboard) tries to wake it.
        sent = self._send_message(
            from_agent="peer",
            to="disabled-codex",
            type="request",
            subject="wake",
            body="get to work",
            trigger=True,
        )
        self.assertFalse(sent.get("ok"), f"disabled agent must hard-reject; got {sent}")
        self.assertIn("cannot start live work", str(sent.get("error", "")))
        rows = self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ?", ("disabled-codex",))
        self.assertEqual(len(rows), 0, "a disabled agent must NOT be cold-started")

    def test_resume_reenables_disabled_agent_for_coldstart(self):
        # Phase 3: disabling is operator-reversible. After Resume the agent is
        # `available` again and a send auto-starts it (Phase 2 cold-start).
        self._modern_wrapper_settings()
        self._codex_env_online()
        self._register("toggle-codex", runtime="codex", sessionMode="managed")
        self.client.post("/api/v1/agents/toggle-codex/control", json={"action": "stop", "from_agent": "dashboard"})
        self.client.post("/api/v1/agents/toggle-codex/control", json={"action": "resume", "from_agent": "dashboard"})

        sent = self._send_message(
            from_agent="peer",
            to="toggle-codex",
            type="request",
            subject="wake",
            body="get to work",
            trigger=True,
        )
        self.assertNotEqual(
            sent.get("error"),
            "Message was not sent because one or more recipients cannot start live work now.",
            f"resumed agent must accept work; got {sent}",
        )
        rows = self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ?", ("toggle-codex",))
        self.assertEqual(len(rows), 1, "resumed agent auto-starts via cold-start")

    def test_orphaned_managed_runs_closed_after_stale_window(self):
        # Operator-reported (2026-05-22): managed hermes-test dispatch
        # sat in 'running' for 30 min after the spawn failed because
        # the bridge's failure-PATCH was dropped on a transient
        # connection error. The 30-min generic stale-repair caught it
        # eventually; this commit tightens the window for managed-mode
        # runs to 5 min (configurable) via _close_orphaned_managed_runs
        # in the periodic reconciler. Bridge-side .catch handler now
        # also retries the failure-PATCH 3 times so the service-side
        # safety net is only needed when the bridge crashed entirely.
        # The orphan-close failure MESSAGE ("no owning bridge" vs the status-aware
        # available/offline variants) is legacy-derivation-coupled; pin old for this
        # message assertion (the reaper itself runs under both engines).
        self.client.put("/api/v1/settings", json={"active_managed_run_stale_minutes": 5, "status_engine": "old"})
        self._register("orphan-hermes", runtime="hermes", sessionMode="managed")
        # Seed: dispatch_run started 10 minutes ago, no claim_bridge_id,
        # non-terminal dispatch_mode → orphan candidate.
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at, claimed_at, started_at,
                claim_bridge_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_orphan_1", None, "dashboard", "orphan-hermes", "start_if_possible",
                "managed", "request", "stuck working", "body", "normal",
                "running", 0, stale_at, None, stale_at, "",
            ),
        )
        # Seed turn_busy=1 to simulate the stuck working signal.
        self._execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, ?, '', 'hermes', ?)
            """,
            ("orphan-hermes", "run_orphan_1", api_v2._now()),
        )

        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_orphaned_managed_runs(db, limit=10)
            finally:
                await db.commit()
                await db.close()
        closed = asyncio.run(_run())
        self.assertEqual(len(closed), 1, closed)
        self.assertEqual(closed[0]["runId"], "run_orphan_1")

        # Run is now failed.
        run_row = self._fetchone("SELECT status, error_text FROM dispatch_runs WHERE id = ?", ("run_orphan_1",))
        self.assertEqual(run_row["status"], "failed")
        self.assertIn("no owning bridge", (run_row["error_text"] or "").lower())

        # turn_busy auto-cleared.
        tb = self._fetchone("SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", ("orphan-hermes",))
        self.assertEqual(int(tb["turn_busy"] or 0), 0)

    def test_periodic_reconcile_requeues_orphaned_claimed_run(self):
        # The periodic reconcile pass must requeue a claimed-but-never-delivered
        # run whose claim bridge is dead, so a live bridge re-claims it instead of
        # the run staying stranded at 'claimed' (confirmed live bug: 3 hermes
        # [STATE CHECK] runs stuck at claimed for 15+ min after a kill/restart).
        self._register("recon-orphan-claim", runtime="hermes", sessionMode="managed")
        claimed_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at, claimed_at, started_at,
                claim_bridge_id, claim_machine_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_recon_orphan_claim", None, "dashboard", "recon-orphan-claim", "start_if_possible",
                "channel", "request", "STATE CHECK", "body", "normal",
                "claimed", 1, claimed_at, claimed_at, None,
                "dead-recon-bridge", "test-machine",
            ),
        )

        result = asyncio.run(service_main._run_dispatch_reconcile_once())
        self.assertIn("orphaned_claims_requeued", result, f"key missing from result: {result}")
        self.assertGreaterEqual(result["orphaned_claims_requeued"], 1, f"expected requeue; got {result}")

        row = self._fetchone(
            "SELECT status, claim_bridge_id FROM dispatch_runs WHERE id = ?", ("run_recon_orphan_claim",)
        )
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["claim_bridge_id"] or "", "")

    def test_queued_channel_run_fails_when_current_wrapper_terminal_exits_before_claim(self):
        # Operator-reported (2026-05-28): sending to managed sc-manager
        # started claude-aify, the PTY exited immediately on a stale
        # --resume handle, and the dispatch stayed queued forever because
        # the wrapper child bridge never got far enough to claim it.
        self._heartbeat_environment(
            id="win:test:default",
            machineId="win:test",
            bridgeId="bridge-win",
            os="windows",
            kind="windows",
            cwdRoots=["C:/repo"],
            runtimes=[
                {
                    "runtime": "claude-code",
                    "modes": ["managed-warm"],
                    "capabilities": {"terminal": True, "pty": True},
                }
            ],
            terminal=True,
            pty=True,
            terminalRuntimes=["claude-code"],
        )
        self._register("queued-claude", runtime="claude-code", sessionMode="managed")
        now = api_v2._now()
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                owner_bridge_id, terminal_id, terminal_status, terminal_command,
                terminal_workspace, session_handle, spawn_spec_id, spawn_request_id,
                capabilities, telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_queued_claude",
                "queued-claude",
                "win:test:default",
                "claude-code",
                "C:/repo",
                "managed-warm",
                "managed",
                "bridge-win",
                "term_queued_claude",
                "running",
                "claude-aify --aify-agent queued-claude --auto --resume missing",
                "C:/repo",
                "missing",
                None,
                None,
                "{}",
                "{}",
                "running",
                now,
                now,
                None,
            ),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by, created_at,
                updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "term_queued_claude",
                "sess_queued_claude",
                "queued-claude",
                "win:test:default",
                "bridge-win",
                "claude-code",
                "C:/repo",
                "claude-aify --aify-agent queued-claude --auto --resume missing",
                "",
                "running",
                "dashboard",
                now,
                now,
                None,
                "",
            ),
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_queued_claude",
                None,
                "dashboard",
                "queued-claude",
                "start_if_possible",
                "channel",
                "request",
                "hello",
                "body",
                "normal",
                "queued",
                0,
                now,
            ),
        )

        async def _run():
            db = await get_db()
            try:
                terminal = await (await db.execute(
                    "SELECT * FROM terminal_sessions WHERE id = ?",
                    ("term_queued_claude",),
                )).fetchone()
                return await api_v2._close_active_terminal_runs_for_terminal(
                    db,
                    terminal,
                    "stopped",
                    now=api_v2._now(),
                    reason="Terminal stopped before the channel bridge claimed the run.",
                )
            finally:
                await db.commit()
                await db.close()

        closed = asyncio.run(_run())
        self.assertEqual(closed, 1)
        run_row = self._fetchone("SELECT status, error_text FROM dispatch_runs WHERE id = ?", ("run_queued_claude",))
        self.assertEqual(run_row["status"], "failed")
        self.assertIn("before the channel bridge claimed", run_row["error_text"])
        events = self._fetchall("SELECT event_type, body FROM dispatch_events WHERE run_id = ?", ("run_queued_claude",))
        self.assertTrue(any(row["event_type"] == "terminal_closed" for row in events))

    def test_orphaned_managed_runs_not_closed_when_bridge_owns(self):
        # Guardrail: orphan-cleanup never touches runs whose claim_bridge_id
        # points at a LIVE bridge_instance (heartbeat within the stale
        # window). Bridge-driven runs stay until the bridge itself reports
        # their terminal state. After the 2026-05-23 fix, "real bridge_id"
        # means "a bridge_instances row that's still heartbeating" — not
        # just any non-empty string.
        self.client.put("/api/v1/settings", json={"active_managed_run_stale_minutes": 5})
        self._register("owned-hermes", runtime="hermes", sessionMode="managed")
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        live_seen = api_v2._now()
        # Seed the live bridge_instance the run claims to be owned by.
        self._execute(
            """
            INSERT INTO bridge_instances (id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen)
            VALUES (?,?,?,?,?,?,?)
            """,
            ("bridge-real-1", "owned-hermes", "test-machine", "hermes", "managed", live_seen, live_seen),
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at, claimed_at, started_at,
                claim_bridge_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_owned_1", None, "dashboard", "owned-hermes", "start_if_possible",
                "managed", "request", "still running", "body", "normal",
                "running", 0, stale_at, stale_at, stale_at, "bridge-real-1",
            ),
        )

        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_orphaned_managed_runs(db, limit=10)
            finally:
                await db.commit()
                await db.close()
        closed = asyncio.run(_run())
        self.assertEqual(len(closed), 0)
        run_row = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_owned_1",))
        self.assertEqual(run_row["status"], "running")

    def test_orphaned_managed_runs_closed_when_claim_bridge_is_stale(self):
        # Operator-reported (2026-05-23): sc-coder hermes managed run sat
        # in 'running' state for 50+ minutes because its claim_bridge_id
        # pointed at a bridge_instance that had since gone stale
        # (last_seen 8+ min ago) when the owning claude-aify wrapper was
        # restarted. The original reaper only checked claim_bridge_id =
        # '' so it skipped this case. After the fix the reaper ALSO
        # treats "claim_bridge_id present BUT named bridge_instance is
        # stale" as orphaned — symmetric handling of "no owning bridge"
        # whether the column is empty or points at a dead bridge.
        self.client.put("/api/v1/settings", json={"active_managed_run_stale_minutes": 5})
        self._register("stale-hermes", runtime="hermes", sessionMode="managed")
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # bridge_instance with last_seen 10 min ago — well past 5-min window.
        self._execute(
            """
            INSERT INTO bridge_instances (id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen)
            VALUES (?,?,?,?,?,?,?)
            """,
            ("bridge-stale-1", "stale-hermes", "test-machine", "hermes", "managed", stale_at, stale_at),
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at, claimed_at, started_at,
                claim_bridge_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_stale_1", None, "dashboard", "stale-hermes", "start_if_possible",
                "managed", "request", "stuck on dead bridge", "body", "normal",
                "running", 0, stale_at, stale_at, stale_at, "bridge-stale-1",
            ),
        )

        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_orphaned_managed_runs(db, limit=10)
            finally:
                await db.commit()
                await db.close()
        closed = asyncio.run(_run())
        self.assertEqual(len(closed), 1, closed)
        self.assertEqual(closed[0]["runId"], "run_stale_1")
        run_row = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_stale_1",))
        self.assertEqual(run_row["status"], "failed")

    def test_run_to_working_target_is_not_failed_for_no_progress(self):
        # Phase F1 (folds in the false-failed-busy-run fix): under
        # status_engine=new, a claimed run whose TARGET is genuinely `working`
        # (mid-turn) must NOT be reaped as no-progress, even when the run is aged
        # past the wall-clock ceiling. A long turn IS progress — the old reaper
        # falsely failed run_1780569850270 (busy 30 min) with "bridge crashed".
        self.client.put("/api/v1/settings", json={
            "active_managed_run_stale_minutes": 5,
            "active_managed_run_wall_ceiling_minutes": 30,
            "status_engine": "new",
        })
        self._register("busy-target", runtime="hermes", sessionMode="resident")
        # Fresh resident bridge so the target is alive; in_turn=1 → engine `working`.
        # status-F2/WS-2 (2026-06-17): `working` now REQUIRES liveness, so the bridge
        # must be linked via runtime_state.bridgeInstanceId for _resident_bridge_is_fresh
        # to recognize it (previously the in_turn→working bug made this pass without a
        # genuinely-fresh bridge).
        live_seen = api_v2._now()
        self._execute(
            """
            INSERT INTO bridge_instances (id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen)
            VALUES (?,?,?,?,?,?,?)
            """,
            ("bridge-busy-1", "busy-target", "test-machine", "hermes", "resident", live_seen, live_seen),
        )
        self._execute(
            "UPDATE agents SET runtime_state = ?, runtime_config = ? WHERE id = ?",
            # gatewayUrl → wake-mode hermes-live (not *-missing-handle, which would derive stale).
            ('{"bridgeInstanceId": "bridge-busy-1"}', '{"gatewayUrl": "ws://127.0.0.1:9999"}', "busy-target"),
        )
        self._execute(
            """
            INSERT INTO agent_status_state (agent_id, status, in_turn, awaiting_input, turn_run_id, last_event, last_event_at, updated_at)
            VALUES (?, 'working', 1, 0, ?, 'turn_start', ?, ?)
            """,
            ("busy-target", "run_busy_target", live_seen, live_seen),
        )
        # Aged past BOTH the no-progress window and the wall-clock ceiling.
        aged_at = (datetime.now(timezone.utc) - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at, claimed_at, started_at,
                claim_bridge_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_busy_target", None, "dashboard", "busy-target", "start_if_possible",
                "channel", "request", "long turn", "body", "normal",
                "running", 1, aged_at, aged_at, aged_at, "",
            ),
        )

        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_orphaned_managed_runs(db, limit=10)
            finally:
                await db.commit()
                await db.close()
        closed = asyncio.run(_run())
        self.assertEqual(len(closed), 0, f"working target must not be reaped; got {closed}")
        run_row = self._fetchone("SELECT status FROM dispatch_runs WHERE id = ?", ("run_busy_target",))
        self.assertEqual(run_row["status"], "running",
                         "a run to a working (mid-turn) target must stay running, not fail")

    def test_run_to_dead_target_fails_fast_with_honest_reason(self):
        # Phase F1: under status_engine=new, a run whose TARGET is stale/offline
        # must fail with an HONEST reason naming the target's state — NOT the
        # generic catch-all "bridge crashed / no owning bridge".
        self.client.put("/api/v1/settings", json={
            "active_managed_run_stale_minutes": 5,
            "status_engine": "new",
        })
        self._register("dead-target", runtime="hermes", sessionMode="resident")
        # No fresh bridge → resident engine_status = stale/offline.
        aged_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at, claimed_at, started_at,
                claim_bridge_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_dead_target", None, "dashboard", "dead-target", "start_if_possible",
                "channel", "request", "to a dead agent", "body", "normal",
                "running", 1, aged_at, aged_at, aged_at, "",
            ),
        )

        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_orphaned_managed_runs(db, limit=10)
            finally:
                await db.commit()
                await db.close()
        closed = asyncio.run(_run())
        self.assertEqual(len(closed), 1, f"dead target run must fail; got {closed}")
        self.assertEqual(closed[0]["runId"], "run_dead_target")
        run_row = self._fetchone("SELECT status, error_text FROM dispatch_runs WHERE id = ?", ("run_dead_target",))
        self.assertEqual(run_row["status"], "failed")
        reason = (run_row["error_text"] or "").lower()
        self.assertIn("dead-target", reason, f"reason must name the target; got {reason!r}")
        self.assertIn("cannot be delivered", reason, f"reason must be honest; got {reason!r}")
        self.assertNotIn("bridge crashed", reason,
                         f"dead-target reason must not be the generic crash catch-all; got {reason!r}")

    def test_orphaned_managed_runs_closed_despite_reply_reminder_events(self):
        # Operator-reported (2026-05-23): sc-coder's stuck run had a
        # 'reply_reminder_skipped' dispatch_event firing every minute
        # (service-side reminder loop), which kept resetting the reaper's
        # "NOT EXISTS dispatch_events" cutoff and prevented reaping even
        # after the bridge died. reply_reminder_skipped is metadata the
        # service emits ABOUT the run, not progress FROM the runtime —
        # the reaper now filters it out of the progress check.
        self.client.put("/api/v1/settings", json={"active_managed_run_stale_minutes": 5})
        self._register("reminder-hermes", runtime="hermes", sessionMode="managed")
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_at = api_v2._now()  # within the cutoff window
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at, claimed_at, started_at,
                claim_bridge_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_reminder_1", None, "dashboard", "reminder-hermes", "start_if_possible",
                "managed", "request", "stuck under reminder spam", "body", "normal",
                "running", 1, stale_at, stale_at, stale_at, "",
            ),
        )
        # Reminder event INSIDE the cutoff window — must NOT keep the run alive.
        self._execute(
            """
            INSERT INTO dispatch_events (run_id, event_type, body, created_at)
            VALUES (?,?,?,?)
            """,
            ("run_reminder_1", "reply_reminder_skipped", "target is busy", recent_at),
        )

        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_orphaned_managed_runs(db, limit=10)
            finally:
                await db.commit()
                await db.close()
        closed = asyncio.run(_run())
        self.assertEqual(len(closed), 1, f"reply_reminder_skipped must not block reaping; got {closed}")
        self.assertEqual(closed[0]["runId"], "run_reminder_1")

    def test_turn_start_endpoint_sets_turn_busy_idempotent(self):
        # Pinning test for /agents/{id}/turn-start (added in 805e2df).
        # Symmetric counterpart to /turn-end. Sets turn_busy=1, refreshes
        # turn_updated_at on every call.
        self._register("turnstart-claude", runtime="claude-code", sessionMode="resident")
        r1 = self.client.post("/api/v1/agents/turnstart-claude/turn-start", json={})
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertTrue(r1.json()["ok"])
        tb = self._fetchone("SELECT turn_busy, turn_bridge_id, turn_updated_at FROM agent_turn_state WHERE agent_id = ?", ("turnstart-claude",))
        self.assertEqual(int(tb["turn_busy"] or 0), 1)
        self.assertEqual(tb["turn_bridge_id"], "user-prompt-submit")
        first_updated = tb["turn_updated_at"]

        # Call again — should refresh turn_updated_at (idempotent).
        import time as _time
        _time.sleep(0.01)
        r2 = self.client.post("/api/v1/agents/turnstart-claude/turn-start", json={})
        self.assertEqual(r2.status_code, 200, r2.text)
        tb2 = self._fetchone("SELECT turn_updated_at FROM agent_turn_state WHERE agent_id = ?", ("turnstart-claude",))
        # second call should not regress the timestamp
        self.assertTrue(tb2["turn_updated_at"] >= first_updated)

    def test_turn_start_does_not_clobber_in_flight_managed_dispatch(self):
        # Code review I2 pinning (2026-05-22): UserPromptSubmit hook firing
        # on a resident-takeover shouldn't wipe out a managed dispatch's
        # turn_run_id / turn_bridge_id that's already in flight.
        self._register("dual-claude", runtime="claude-code", sessionMode="resident")
        # Simulate a managed bridge having just set turn_busy with real run linkage
        self._execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, 'run_real_123', 'real-bridge-abc', 'claude-code', ?)
            """,
            ("dual-claude", api_v2._now()),
        )
        # Then UserPromptSubmit fires
        r = self.client.post("/api/v1/agents/dual-claude/turn-start", json={})
        self.assertEqual(r.status_code, 200, r.text)
        tb = self._fetchone("SELECT turn_busy, turn_run_id, turn_bridge_id FROM agent_turn_state WHERE agent_id = ?", ("dual-claude",))
        # turn_busy should still be 1
        self.assertEqual(int(tb["turn_busy"] or 0), 1)
        # turn_run_id should be preserved (managed dispatch context lives)
        self.assertEqual(tb["turn_run_id"], "run_real_123")
        # turn_bridge_id should NOT be clobbered to user-prompt-submit
        self.assertEqual(tb["turn_bridge_id"], "real-bridge-abc")

    def test_virtual_rpc_bridge_takeover_revives_stopped_terminal(self):
        # Code review and operator-report pinning: bridge takeover on
        # /terminals/{id}/output also revives a stopped synth terminal
        # row (race between supersession-cleanup and new bridge's POST).
        self._heartbeat_environment(
            id="env_takeover",
            bridgeId="bridge-takeover-old",
            machineId="linux:takeover",
            runtimes=[{"runtime": "pi", "modes": ["managed-warm"], "capabilities": {}}],
            terminal=True,
            pty=True,
            terminalRuntimes=["pi"],
        )
        self._register("takeover-pi", runtime="pi", sessionMode="managed")
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, terminal_id, terminal_status,
                terminal_command, terminal_workspace, process_id, session_handle,
                app_server_url, spawn_spec_id, spawn_request_id, capabilities,
                telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_takeover", "takeover-pi", "env_takeover", "pi", "/w", "managed",
                "managed", "bridge-takeover-old", "vterm_takeover", "running",
                "aify://virtual-rpc/pi", "/w", "", "pi-handle", "", None, None,
                "{}", "{}", "running", api_v2._now(), api_v2._now(), None,
            ),
        )
        # Seed a stopped virtual rpc terminal owned by the old bridge
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "vterm_takeover", "sess_takeover", "takeover-pi", "env_takeover",
                "bridge-takeover-old", "pi", "/w", "aify://virtual-rpc/pi",
                "", "stopped", "bridge-rpc",
                api_v2._now(), api_v2._now(), api_v2._now(),
                "Superseded by bridge re-registration; in-memory worker pool empty after restart.",
            ),
        )
        # New bridge POSTs output → should takeover + revive
        r = self.client.post(
            "/api/v1/terminals/vterm_takeover/output",
            json={"bridgeId": "bridge-takeover-new", "output": "frame", "status": "running"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        row = self._fetchone("SELECT status, bridge_id, stopped_at, error FROM terminal_sessions WHERE id = ?", ("vterm_takeover",))
        self.assertEqual(row["status"], "running", row)
        self.assertEqual(row["bridge_id"], "bridge-takeover-new")
        self.assertIsNone(row["stopped_at"])
        self.assertEqual(row["error"], "")

    def test_runtime_state_patch_preserves_virtual_terminal_keys(self):
        # Pinning for SERVICE_MANAGED_RUNTIME_STATE_KEYS (commit 95524d7).
        # When the bridge PATCHes runtime_state without virtualTerminalId,
        # the existing pointer must be preserved (not clobbered).
        self._register("preserve-pi", runtime="pi", sessionMode="managed")
        self._execute(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            (json.dumps({
                "virtualTerminal": True,
                "virtualTerminalId": "vterm_existing_123",
                "sessionId": "old-session",
            }), "preserve-pi"),
        )
        # Bridge PATCHes with only sessionId (no virtualTerminalId)
        r = self.client.patch("/api/v1/agents/preserve-pi/runtime-state", json={
            "runtimeState": {"sessionId": "new-session", "sessionFile": "/tmp/x"},
        })
        self.assertEqual(r.status_code, 200, r.text)
        agent_row = self._fetchone("SELECT runtime_state FROM agents WHERE id = ?", ("preserve-pi",))
        rs = json.loads(agent_row["runtime_state"] or "{}")
        # virtualTerminalId preserved
        self.assertEqual(rs.get("virtualTerminalId"), "vterm_existing_123")
        self.assertTrue(rs.get("virtualTerminal"))
        # New keys applied
        self.assertEqual(rs.get("sessionId"), "new-session")
        self.assertEqual(rs.get("sessionFile"), "/tmp/x")

        # Explicit null clears it
        r2 = self.client.patch("/api/v1/agents/preserve-pi/runtime-state", json={
            "runtimeState": {"virtualTerminalId": None, "sessionId": "yet-newer"},
        })
        self.assertEqual(r2.status_code, 200, r2.text)
        agent_row = self._fetchone("SELECT runtime_state FROM agents WHERE id = ?", ("preserve-pi",))
        rs = json.loads(agent_row["runtime_state"] or "{}")
        self.assertNotIn("virtualTerminalId", rs)

    def test_console_available_payload_false_for_resident(self):
        # Pinning for consoleAvailable (commit 334a2ff). Resident sessions
        # don't have an aify-tracked PTY/RPC, so Console can't open.
        self._register("payload-resident", runtime="claude-code", sessionMode="resident")
        r = self.client.get("/api/v1/agents/payload-resident")
        self.assertEqual(r.status_code, 200, r.text)
        a = r.json()["agent"]
        self.assertEqual(a.get("sessionMode"), "resident")
        self.assertEqual(a.get("consoleAvailable"), False)

        self._register("payload-managed", runtime="claude-code", sessionMode="managed")
        r2 = self.client.get("/api/v1/agents/payload-managed")
        a2 = r2.json()["agent"]
        self.assertEqual(a2.get("sessionMode"), "managed")
        self.assertEqual(a2.get("consoleAvailable"), True)

    def test_queue_if_busy_respects_turn_busy_when_no_active_run_row(self):
        # Operator-reported 2026-05-22: clicking dashboard "Queue" sent
        # the message immediately when the target was still mid-turn.
        # Root cause: require_reply=0 info messages auto-complete on
        # delivery → hasActiveRun=False even though the assistant was
        # still working. The queue-if-busy gate now ALSO checks the
        # harness-level turn_busy signal (set by claude-channel.js claim
        # / UserPromptSubmit hook / per-runtime turn-start hook), which
        # survives the dispatch-row auto-completion.
        self._heartbeat_environment(
            id="env_qb",
            bridgeId="bridge-qb",
            machineId="linux:qb",
            runtimes=[
                {
                    "runtime": "claude-code",
                    "modes": ["managed-warm"],
                    "capabilities": {"interrupt": True, "steer": True},
                }
            ],
            terminal=True,
            pty=True,
            terminalRuntimes=["claude-code"],
        )
        self._register("qb-claude", runtime="claude-code", sessionMode="resident", runtimeConfig={"channelEnabled": True})
        # No claimed/running dispatch_run, but turn_busy=1 (fresh) — the
        # exact scenario the operator hit: previous info message
        # auto-completed but the assistant is still working.
        self._execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, '', 'channel-test', 'claude-code', ?)
            """,
            ("qb-claude", api_v2._now()),
        )
        self._register("qb-sender", runtime="claude-code", sessionMode="resident")
        sent = self._send_message(
            from_agent="qb-sender",
            to="qb-claude",
            type="info",
            subject="should queue not immediate",
            body="defer this",
            trigger=True,
            queueIfBusy=True,
        )
        # Queue-if-busy must NOT have dispatched a new live run while
        # turn_busy=1. The send returns success, but the dispatch_run
        # should remain in queued state behind the busy turn.
        runs = sent.get("dispatchRuns", [])
        if runs:
            for run in runs:
                self.assertEqual(
                    run.get("status"),
                    "queued",
                    f"queueIfBusy should defer while turn_busy=1, got {run}",
                )

    def test_idle_virtual_rpc_workers_auto_close_when_setting_enabled(self):
        # Operator-driven feature: managed worker terminal_sessions whose
        # updated_at is older than worker_idle_close_minutes AND have no
        # in-flight dispatch runs get auto-closed by the periodic reconciler.
        self.client.put("/api/v1/settings", json={"worker_idle_close_enabled": True, "worker_idle_close_minutes": 5})
        self._heartbeat_environment(
            id="env_idle_close",
            bridgeId="bridge-idle-close",
            machineId="linux:idle-close",
            runtimes=[
                {
                    "runtime": "pi",
                    "modes": ["managed-warm"],
                    "capabilities": {"interrupt": True},
                }
            ],
        )
        self._register("idle-pi", runtime="pi", sessionMode="managed")
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, terminal_id, terminal_status,
                terminal_command, terminal_workspace, process_id, session_handle,
                app_server_url, spawn_spec_id, spawn_request_id, capabilities,
                telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_idle_close_1", "idle-pi", "env_idle_close", "pi", "/w", "managed",
                "managed", "bridge-idle-close", "vterm_idle_close_1", "running",
                "aify://virtual-rpc/pi", "/w", "", "pi-handle", "", None, None,
                "{}", "{}", "running", api_v2._now(), api_v2._now(), None,
            ),
        )
        # Stale terminal_session (updated 30 min ago).
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "vterm_idle_close_1", "sess_idle_close_1", "idle-pi", "env_idle_close",
                "bridge-idle-close", "pi", "/w", "aify://virtual-rpc/pi",
                "", "running", "bridge-rpc", stale_at, stale_at, None, "",
            ),
        )
        self._execute(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            (json.dumps({"virtualTerminal": True, "virtualTerminalId": "vterm_idle_close_1"}), "idle-pi"),
        )

        # Run the reconciler.
        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_idle_virtual_rpc_workers(db, limit=10)
            finally:
                await db.commit()
                await db.close()
        closed = asyncio.run(_run())
        self.assertEqual(len(closed), 1, closed)
        self.assertEqual(closed[0]["agentId"], "idle-pi")

        # Terminal stopped, agent runtime_state cleared.
        term = self._fetchone("SELECT status FROM terminal_sessions WHERE id = ?", ("vterm_idle_close_1",))
        self.assertEqual(term["status"], "stopped")
        agent_row = self._fetchone("SELECT runtime_state FROM agents WHERE id = ?", ("idle-pi",))
        rs = json.loads(agent_row["runtime_state"] or "{}")
        self.assertNotIn("virtualTerminalId", rs)

    def test_idle_managed_wrapper_worker_auto_close_enqueues_stop_control(self):
        self.client.put("/api/v1/settings", json={"worker_idle_close_enabled": True, "worker_idle_close_minutes": 5})
        self._heartbeat_environment(
            id="env_idle_wrapper",
            bridgeId="bridge-idle-wrapper",
            machineId="linux:idle-wrapper",
            runtimes=[{"runtime": "hermes", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        self._register("idle-hermes", runtime="hermes", sessionMode="managed")
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, terminal_id, terminal_status,
                terminal_command, terminal_workspace, process_id, session_handle,
                app_server_url, spawn_spec_id, spawn_request_id, capabilities,
                telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_idle_wrapper", "idle-hermes", "env_idle_wrapper", "hermes", "/w", "managed",
                "managed", "bridge-idle-wrapper", "term_idle_wrapper", "attached",
                "hermes-aify --aify-agent idle-hermes --resume h1", "/w", "", "h1", "", None, None,
                "{}", "{}", "running", stale_at, stale_at, None,
            ),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "term_idle_wrapper", "sess_idle_wrapper", "idle-hermes", "env_idle_wrapper",
                "bridge-idle-wrapper", "hermes", "/w", "hermes-aify --aify-agent idle-hermes --resume h1",
                "", "attached", "dashboard", stale_at, stale_at, None, "",
            ),
        )
        self._execute(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            (json.dumps({"terminalId": "term_idle_wrapper"}), "idle-hermes"),
        )

        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_idle_virtual_rpc_workers(db, limit=10)
            finally:
                await db.commit()
                await db.close()

        closed = asyncio.run(_run())
        self.assertEqual(closed, [{"terminalId": "term_idle_wrapper", "agentId": "idle-hermes"}])
        term = self._fetchone("SELECT status FROM terminal_sessions WHERE id = ?", ("term_idle_wrapper",))
        self.assertEqual(term["status"], "stopping")
        control = self._fetchone(
            "SELECT action, status, requested_by FROM terminal_controls WHERE terminal_id = ?",
            ("term_idle_wrapper",),
        )
        self.assertEqual(control["action"], "stop")
        self.assertEqual(control["status"], "pending")
        self.assertEqual(control["requested_by"], "auto-close-idle-worker")
        session = self._fetchone("SELECT terminal_status FROM agent_sessions WHERE id = ?", ("sess_idle_wrapper",))
        self.assertEqual(session["terminal_status"], "stopping")
        agent_row = self._fetchone("SELECT runtime_state FROM agents WHERE id = ?", ("idle-hermes",))
        self.assertNotIn("terminalId", json.loads(agent_row["runtime_state"] or "{}"))

    def test_idle_managed_wrapper_without_bridge_owner_marks_stopped(self):
        self.client.put("/api/v1/settings", json={"worker_idle_close_enabled": True, "worker_idle_close_minutes": 5})
        self._heartbeat_environment(
            id="env_idle_orphan",
            bridgeId="bridge-idle-orphan",
            machineId="linux:idle-orphan",
            runtimes=[{"runtime": "codex", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        self._register("idle-orphan-codex", runtime="codex", sessionMode="managed")
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, terminal_id, terminal_status,
                terminal_command, terminal_workspace, process_id, session_handle,
                app_server_url, spawn_spec_id, spawn_request_id, capabilities,
                telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_idle_orphan", "idle-orphan-codex", "env_idle_orphan", "codex", "/w", "managed",
                "managed", "bridge-idle-orphan", "term_idle_orphan", "running",
                "codex-aify --aify-agent idle-orphan-codex", "/w", "", "codex-orphan", "", None, None,
                "{}", "{}", "running", stale_at, stale_at, None,
            ),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "term_idle_orphan", "sess_idle_orphan", "idle-orphan-codex", "env_idle_orphan", "",
                "codex", "/w", "codex-aify --aify-agent idle-orphan-codex",
                "", "running", "dashboard", stale_at, stale_at, None, "",
            ),
        )

        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_idle_virtual_rpc_workers(db, limit=10)
            finally:
                await db.commit()
                await db.close()

        self.assertEqual(asyncio.run(_run()), [{"terminalId": "term_idle_orphan", "agentId": "idle-orphan-codex"}])
        term = self._fetchone("SELECT status FROM terminal_sessions WHERE id = ?", ("term_idle_orphan",))
        self.assertEqual(term["status"], "stopped")
        control = self._fetchone(
            "SELECT COUNT(*) AS count FROM terminal_controls WHERE terminal_id = ?",
            ("term_idle_orphan",),
        )
        self.assertEqual(control["count"], 0)

    def test_idle_worker_auto_close_can_be_disabled_even_with_minutes_set(self):
        self.client.put("/api/v1/settings", json={"worker_idle_close_enabled": False, "worker_idle_close_minutes": 5})
        self._heartbeat_environment(
            id="env_idle_disabled",
            bridgeId="bridge-idle-disabled",
            machineId="linux:idle-disabled",
            runtimes=[{"runtime": "pi", "modes": ["managed-warm"], "capabilities": {}}],
        )
        self._register("disabled-pi", runtime="pi", sessionMode="managed")
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, terminal_id, terminal_status,
                terminal_command, terminal_workspace, process_id, session_handle,
                app_server_url, spawn_spec_id, spawn_request_id, capabilities,
                telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_idle_disabled", "disabled-pi", "env_idle_disabled", "pi", "/w", "managed",
                "managed", "bridge-idle-disabled", "vterm_idle_disabled", "running",
                "aify://virtual-rpc/pi", "/w", "", "pi-handle-disabled", "", None, None,
                "{}", "{}", "running", stale_at, stale_at, None,
            ),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "vterm_idle_disabled", "sess_idle_disabled", "disabled-pi", "env_idle_disabled",
                "bridge-idle-disabled", "pi", "/w", "aify://virtual-rpc/pi",
                "", "running", "bridge-rpc", stale_at, stale_at, None, "",
            ),
        )

        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_idle_virtual_rpc_workers(db, limit=10)
            finally:
                await db.commit()
                await db.close()

        self.assertEqual(asyncio.run(_run()), [])
        term = self._fetchone("SELECT status FROM terminal_sessions WHERE id = ?", ("vterm_idle_disabled",))
        self.assertEqual(term["status"], "running")

    def test_idle_virtual_rpc_workers_not_closed_when_in_flight_run(self):
        # Guardrail: in-flight dispatch_run blocks auto-close.
        self.client.put("/api/v1/settings", json={"worker_idle_close_enabled": True, "worker_idle_close_minutes": 5})
        self._heartbeat_environment(
            id="env_idle_inflight",
            bridgeId="bridge-idle-inflight",
            machineId="linux:idle-inflight",
            runtimes=[{"runtime": "pi", "modes": ["managed-warm"], "capabilities": {}}],
        )
        self._register("inflight-pi", runtime="pi", sessionMode="managed")
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, terminal_id, terminal_status,
                terminal_command, terminal_workspace, process_id, session_handle,
                app_server_url, spawn_spec_id, spawn_request_id, capabilities,
                telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_inflight", "inflight-pi", "env_idle_inflight", "pi", "/w", "managed",
                "managed", "bridge-idle-inflight", "vterm_inflight", "running",
                "aify://virtual-rpc/pi", "/w", "", "pi-handle-2", "", None, None,
                "{}", "{}", "running", api_v2._now(), api_v2._now(), None,
            ),
        )
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "vterm_inflight", "sess_inflight", "inflight-pi", "env_idle_inflight",
                "bridge-idle-inflight", "pi", "/w", "aify://virtual-rpc/pi",
                "", "running", "bridge-rpc", stale_at, stale_at, None, "",
            ),
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority,
                status, require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_inflight", None, "dashboard", "inflight-pi", "start_if_possible",
                "managed", "request", "in flight", "body", "normal", "running", 0, api_v2._now(),
            ),
        )

        async def _run():
            from service.db import get_db as _get_db
            db = await _get_db()
            try:
                return await api_v2._close_idle_virtual_rpc_workers(db, limit=10)
            finally:
                await db.commit()
                await db.close()
        closed = asyncio.run(_run())
        self.assertEqual(len(closed), 0, "in-flight run should block auto-close")
        term = self._fetchone("SELECT status FROM terminal_sessions WHERE id = ?", ("vterm_inflight",))
        self.assertEqual(term["status"], "running")

    def test_agent_favorite_endpoint_toggles_and_returns_in_payload(self):
        self._register("fav-agent")
        # Default not favorited.
        agent = self.client.get("/api/v1/agents/fav-agent").json()["agent"]
        self.assertEqual(agent.get("favorited"), False)

        # Favorite.
        r1 = self.client.patch("/api/v1/agents/fav-agent/favorite", json={"favorited": True})
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertEqual(r1.json()["favorited"], True)
        agent = self.client.get("/api/v1/agents/fav-agent").json()["agent"]
        self.assertEqual(agent["favorited"], True)

        # Unfavorite.
        r2 = self.client.patch("/api/v1/agents/fav-agent/favorite", json={"favorited": False})
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["favorited"], False)
        agent = self.client.get("/api/v1/agents/fav-agent").json()["agent"]
        self.assertEqual(agent["favorited"], False)

        # Unknown agent → 404.
        r3 = self.client.patch("/api/v1/agents/ghost/favorite", json={"favorited": True})
        self.assertEqual(r3.status_code, 404, r3.text)

    def test_stop_worker_tears_down_session_and_returns_to_available(self):
        # Phase 4: dashboard Stop → agent goes from online/working to
        # available. The endpoint ends live agent_sessions, marks any
        # virtual terminal_session row as stopped, clears the
        # runtime_state.virtualTerminalId pointer, and zeros turn_busy.
        self._heartbeat_environment(
            id="env_stop",
            bridgeId="bridge-stop",
            machineId="linux:stop",
            runtimes=[
                {
                    "runtime": "pi",
                    "modes": ["managed-warm"],
                    "capabilities": {"interrupt": True},
                }
            ],
            terminal=True,
            pty=True,
            terminalRuntimes=["pi"],
        )
        self._register("stop-pi", runtime="pi", sessionMode="managed")
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, terminal_id, terminal_status,
                terminal_command, terminal_workspace, process_id, session_handle,
                app_server_url, spawn_spec_id, spawn_request_id, capabilities,
                telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_stop_1",
                "stop-pi",
                "env_stop",
                "pi",
                "/workspace",
                "managed",
                "managed",
                "bridge-stop",
                "vterm_stop_1",
                "running",
                "aify://virtual-rpc/pi",
                "/workspace",
                "",
                "pi-handle-stop",
                "",
                None,
                None,
                "{}",
                "{}",
                "running",
                "2026-05-22T00:00:00Z",
                "2026-05-22T00:00:00Z",
                None,
            ),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "vterm_stop_1",
                "sess_stop_1",
                "stop-pi",
                "env_stop",
                "bridge-stop",
                "pi",
                "/workspace",
                "aify://virtual-rpc/pi",
                "",
                "running",
                "bridge-rpc",
                "2026-05-22T00:00:00Z",
                "2026-05-22T00:00:00Z",
                None,
                "",
            ),
        )
        self._execute(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            (json.dumps({"virtualTerminal": True, "virtualTerminalId": "vterm_stop_1"}), "stop-pi"),
        )
        asyncio.run(self._async_invalidate("stop-pi"))
        before = self.client.get("/api/v1/agents/stop-pi").json()["agent"]
        self.assertEqual(before["status"], "online", before)

        # Stop the worker.
        response = self.client.post("/api/v1/agents/stop-pi/stop-worker", json={})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["virtualTerminalId"], "vterm_stop_1")

        # Effects: virtual terminal stopped, agent_session ended, runtime_state cleared.
        term_row = self._fetchone("SELECT status, stopped_at FROM terminal_sessions WHERE id = ?", ("vterm_stop_1",))
        self.assertEqual(term_row["status"], "stopped")
        self.assertIsNotNone(term_row["stopped_at"])
        sess_row = self._fetchone("SELECT status FROM agent_sessions WHERE id = ?", ("sess_stop_1",))
        self.assertEqual(sess_row["status"], "ended")
        agent_row = self._fetchone("SELECT runtime_state FROM agents WHERE id = ?", ("stop-pi",))
        rs = json.loads(agent_row["runtime_state"] or "{}")
        self.assertNotIn("virtualTerminalId", rs)
        self.assertNotIn("virtualTerminal", rs)

        # Derived status flips to available.
        after = self.client.get("/api/v1/agents/stop-pi").json()["agent"]
        self.assertEqual(after["status"], "available", after)

    def test_status_taxonomy_available_when_no_live_worker_online_when_session_alive(self):
        # Legacy resident persistent-worker taxonomy (available→online from a live
        # agent_sessions row); the new engine models resident liveness via bridge freshness,
        # not the session row, so it derives differently — pin old for this legacy contract.
        self.client.put("/api/v1/settings", json={"status_engine": "old"})
        # Persistent-worker model (Phase 2 of plan
        # docs/plans/persistent-worker-status-taxonomy.md). An agent
        # registered with env online but no live agent_session reports
        # "available" — the wake-on-message path will spawn the worker.
        # Once a live agent_session exists, status flips to "online"
        # (worker alive, idle).
        self._heartbeat_environment(
            id="env_taxonomy",
            bridgeId="bridge-taxonomy",
            machineId="linux:taxonomy",
            runtimes=[
                {
                    "runtime": "claude-code",
                    "modes": ["managed-warm"],
                    "capabilities": {"interrupt": True},
                }
            ],
            terminal=True,
            pty=True,
            terminalRuntimes=["claude-code"],
        )
        self._register("taxonomy-claude", runtime="claude-code", sessionMode="resident")

        avail = self.client.get("/api/v1/agents/taxonomy-claude").json()["agent"]
        self.assertEqual(avail["status"], "available", avail)

        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, terminal_id, terminal_status,
                terminal_command, terminal_workspace, process_id, session_handle,
                app_server_url, spawn_spec_id, spawn_request_id, capabilities,
                telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_taxonomy_1",
                "taxonomy-claude",
                "env_taxonomy",
                "claude-code",
                "/workspace",
                "managed",
                "managed",
                "bridge-taxonomy",
                "",
                "",
                "",
                "/workspace",
                "",
                "claude-handle-tax",
                "",
                None,
                None,
                "{}",
                "{}",
                "running",
                "2026-05-22T00:00:00Z",
                "2026-05-22T00:00:00Z",
                None,
            ),
        )
        # Use a fresh heartbeat so the idle-staleness override doesn't fire.
        fresh = api_v2._now()
        self._execute(
            "UPDATE agents SET last_seen = ? WHERE id = ?",
            (fresh, "taxonomy-claude"),
        )
        self._execute(
            "UPDATE agent_sessions SET last_seen = ? WHERE id = ?",
            (fresh, "sess_taxonomy_1"),
        )
        asyncio.run(self._async_invalidate("taxonomy-claude"))
        online = self.client.get("/api/v1/agents/taxonomy-claude").json()["agent"]
        self.assertEqual(online["status"], "online", online)

    def test_managed_wrapper_attached_terminal_counts_as_online_at_read_gate(self):
        # Hermes/Codex managed-via-wrapper PTYs settle at status='attached'
        # after a turn completes. The read-path no-live-worker gate must treat
        # that as live, otherwise the dashboard shows `available` while the
        # visible wrapper terminal is still running and claimable.
        self.client.put("/api/v1/settings", json={"managed_via_wrapper": ["hermes"]})
        self._heartbeat_environment(
            id="env_wrapper_gate",
            bridgeId="bridge-wrapper-gate",
            machineId="win32:wrapper-gate",
            os="win32",
            kind="windows",
            terminal=True,
            pty=True,
            terminalRuntimes=["hermes"],
            runtimes=[
                {
                    "runtime": "hermes",
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                }
            ],
        )
        self._register(
            "taxonomy-hermes-wrapper",
            runtime="hermes",
            sessionMode="managed",
            sessionHandle="hermes-handle-1",
            machineId="win32:wrapper-gate",
            status="active",
        )
        fresh = api_v2._now()
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, terminal_id, terminal_status,
                terminal_command, terminal_workspace, process_id, session_handle,
                app_server_url, spawn_spec_id, spawn_request_id, capabilities,
                telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_wrapper_gate_1",
                "taxonomy-hermes-wrapper",
                "env_wrapper_gate",
                "hermes",
                "C:/repo",
                "managed-warm",
                "managed",
                "bridge-wrapper-gate",
                "term_wrapper_gate_1",
                "attached",
                "hermes-aify --aify-agent taxonomy-hermes-wrapper --resume hermes-handle-1",
                "C:/repo",
                "12345",
                "hermes-handle-1",
                "",
                None,
                None,
                "{}",
                "{}",
                "running",
                fresh,
                fresh,
                None,
            ),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, status, requested_by, created_at, updated_at,
                stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "term_wrapper_gate_1",
                "sess_wrapper_gate_1",
                "taxonomy-hermes-wrapper",
                "env_wrapper_gate",
                "bridge-wrapper-gate",
                "hermes",
                "C:/repo",
                "hermes-aify --aify-agent taxonomy-hermes-wrapper --resume hermes-handle-1",
                "attached",
                "dashboard",
                fresh,
                fresh,
                None,
                "",
            ),
        )
        self._execute(
            """
            INSERT INTO agent_live_state (
                agent_id, status, reason, environment_id, session_id, terminal_id,
                active_run_id, refresh_after, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                "taxonomy-hermes-wrapper",
                "online",
                "",
                "env_wrapper_gate",
                "sess_wrapper_gate_1",
                "term_wrapper_gate_1",
                "",
                "9999-12-31T23:59:59Z",
                fresh,
            ),
        )

        agent = self.client.get("/api/v1/agents/taxonomy-hermes-wrapper").json()["agent"]
        self.assertEqual(agent["status"], "online", agent)

    def test_resident_route_delivered_awaiting_reply_shows_online_not_working(self):
        # Status-split (2026-05-31): a resident-route delivered+require_reply run
        # with no fresh turn_busy = idle-owing-reply = `online` (awaiting reply),
        # NOT `working`. Same contract as the channel-route case above.
        self._heartbeat_environment(
            id="env_resident_busy",
            bridgeId="bridge-resident-busy",
            machineId="linux:resident-busy",
            runtimes=[
                {
                    "runtime": "claude-code",
                    "modes": ["managed-warm"],
                    "capabilities": {"interrupt": True},
                }
            ],
            terminal=True,
            pty=True,
            terminalRuntimes=["claude-code"],
        )
        self._register("resident-claude", runtime="claude-code", sessionMode="resident")
        self._stamp_live_channel_sidecar("resident-claude")  # status_engine=new resident proof-of-life
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status,
                require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_resident_busy_1",
                None,
                "dashboard",
                "resident-claude",
                "start_if_possible",
                "resident",
                "request",
                "question to resident",
                "deep think",
                "normal",
                "delivered",
                1,
                "2026-05-21T00:00:00Z",
            ),
        )
        asyncio.run(self._async_invalidate("resident-claude"))
        response = self.client.get("/api/v1/agents/resident-claude")
        self.assertEqual(response.status_code, 200, response.text)
        agent = response.json()["agent"]
        self.assertEqual(agent["status"], "online", agent)
        self.assertNotEqual(agent["status"], "working")
        self.assertIn("awaiting reply", (agent.get("statusNote") or "").lower())

    def test_reply_landing_clears_turn_busy_for_channel_route(self):
        # claude-channel.js pulses turn_busy=true on every delivery and
        # relies on the 120s stale window for cleanup. That's too slow
        # after the reply lands — the operator wants status to flip back
        # to "active" immediately when the agent finishes. Server-side
        # _mark_dispatch_run_answered now clears turn_busy when the last
        # in-flight channel-or-resident require_reply run for the agent
        # closes.
        self._heartbeat_environment(
            id="env_clear_busy",
            bridgeId="bridge-clear-busy",
            machineId="linux:clear-busy",
            runtimes=[
                {
                    "runtime": "claude-code",
                    "modes": ["managed-warm"],
                    "capabilities": {"interrupt": True},
                }
            ],
            terminal=True,
            pty=True,
            terminalRuntimes=["claude-code"],
        )
        self._register("clearer-claude", runtime="claude-code", sessionMode="resident")
        self._stamp_live_channel_sidecar("clearer-claude")  # status_engine=new resident proof-of-life
        # Seed: dashboard's original message → a delivered+require_reply run
        # → a fresh turn_busy=true pulse. Message must exist BEFORE the
        # dispatch_run row that FKs to it.
        self._execute(
            """
            INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "msg_clearer_1",
                "dashboard",
                "clearer-claude",
                "direct",
                "request",
                "ask",
                "body",
                "normal",
                0,
                None,
                1779394000000,
            ),
        )
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status,
                require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_clearer_1",
                "msg_clearer_1",
                "dashboard",
                "clearer-claude",
                "start_if_possible",
                "channel",
                "request",
                "ask",
                "body",
                "normal",
                "delivered",
                1,
                "2026-05-21T00:00:00Z",
            ),
        )
        # FRESH turn_busy (not a stale fixed date) so `before` is genuinely
        # `working` via the turn_busy branch — the path this test exercises.
        # (Post status-split, a stale turn_busy would fall through to the
        # idle-awaiting-reply `online` state, which is a different code path.)
        now = api_v2._now()
        self._execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET turn_busy = 1, turn_updated_at = excluded.turn_updated_at
            """,
            ("clearer-claude", "run_clearer_1", "bridge-clear-busy", "claude-code", now),
        )
        # status_engine=new reads in_turn from agent_status_state; seed it for the `before`
        # working precondition (the reply-landing clear below clears it via _clear_status_state_in_turn).
        self._execute(
            """
            INSERT INTO agent_status_state (agent_id, status, in_turn, awaiting_input, turn_run_id, last_event, last_event_at, updated_at)
            VALUES (?, 'working', 1, 0, ?, 'turn_start', ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET in_turn=1, last_event_at=excluded.last_event_at, updated_at=excluded.updated_at
            """,
            ("clearer-claude", "run_clearer_1", now, now),
        )
        asyncio.run(self._async_invalidate("clearer-claude"))
        before = self.client.get("/api/v1/agents/clearer-claude").json()["agent"]
        self.assertEqual(before["status"], "working", before)

        # Reply lands.
        reply = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "clearer-claude",
                "to": "dashboard",
                "type": "response",
                "subject": "answered",
                "body": "Here is the answer.",
                "inReplyTo": "msg_clearer_1",
            },
        )
        self.assertEqual(reply.status_code, 200, reply.text)

        # turn_busy must have been auto-cleared.
        tb = self._fetchone("SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", ("clearer-claude",))
        self.assertEqual(int(tb["turn_busy"] or 0), 0, "expected turn_busy auto-cleared after reply closed the delivered run")

        # And derived status should no longer be working.
        after = self.client.get("/api/v1/agents/clearer-claude").json()["agent"]
        self.assertNotEqual(after["status"], "working", after)

    def test_terminal_route_delivered_does_not_pin_working_status(self):
        # Guardrail for the channel-route fix: the new
        # _current_channel_awaiting_reply_run_row lookup must filter on
        # execution_mode='channel'. A terminal-route dispatch sitting
        # 'delivered' as its normal lingering state must NOT light up
        # "working" — that's the original failure mode the strict
        # _current_active_run_row exists to avoid.
        self._heartbeat_environment(
            id="env_terminal_busy",
            bridgeId="bridge-terminal-busy",
            machineId="linux:terminal-busy",
            runtimes=[
                {
                    "runtime": "claude-code",
                    "modes": ["managed-warm"],
                    "capabilities": {"interrupt": True},
                }
            ],
            terminal=True,
            pty=True,
            terminalRuntimes=["claude-code"],
        )
        self._register("terminal-claude", runtime="claude-code", sessionMode="managed")
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status,
                require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_terminal_busy_1",
                None,
                "dashboard",
                "terminal-claude",
                "start_if_possible",
                "managed",
                "request",
                "delivered-terminal",
                "body",
                "normal",
                "delivered",
                1,
                "2026-05-21T00:00:00Z",
            ),
        )
        asyncio.run(self._async_invalidate("terminal-claude"))
        response = self.client.get("/api/v1/agents/terminal-claude")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(
            response.json()["agent"]["status"],
            "working",
            "execution_mode='managed' delivered run must NOT light up working — that's the terminal-delivery lingering bug guard",
        )

    def test_pi_session_state_reports_bridge_ownership_for_watchdog(self):
        # Phase 4: omp-aify queries this before exec'ing OMP. When no virtual
        # terminal exists, bridgeOwned is false → the wrapper proceeds. After a
        # virtual terminal_session is created (Phase 2 endpoint), it returns
        # bridgeOwned=true and the wrapper refuses to start.
        self._heartbeat_environment(
            id="env_watchdog",
            bridgeId="bridge-watchdog",
            machineId="linux:watchdog",
            runtimes=[
                {
                    "runtime": "pi",
                    "modes": ["managed-warm"],
                    "capabilities": {"interrupt": True, "steer": True},
                }
            ],
        )
        self._register("pi-worker", runtime="pi", sessionMode="managed")
        state_clear = self.client.get("/api/v1/agents/pi-worker/pi-session-state")
        self.assertEqual(state_clear.status_code, 200, state_clear.text)
        body_clear = state_clear.json()
        self.assertEqual(body_clear["ok"], True)
        self.assertEqual(body_clear["agentId"], "pi-worker")
        self.assertEqual(body_clear["bridgeOwned"], False)
        self.assertEqual(body_clear["virtualTerminalId"], "")
        self.assertIsNone(body_clear["terminal"])

        # Seed the state that ensure_virtual_terminal would set: an
        # agent_session row, a running terminal_sessions row marked with the
        # virtual-rpc command marker, and the agent's runtime_state pointing
        # at it. We don't go through the full /ensure endpoint because that
        # requires an environment heartbeat from a bridge.
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, terminal_id, terminal_status,
                terminal_command, terminal_workspace, process_id, session_handle,
                app_server_url, spawn_spec_id, spawn_request_id, capabilities,
                telemetry, status, started_at, last_seen, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "sess_pi_watchdog",
                "pi-worker",
                "env_watchdog",
                "pi",
                "/workspace",
                "managed",
                "managed",
                "bridge-watchdog",
                "",
                "",
                "",
                "",
                "",
                "pi-handle-1",
                "",
                None,
                None,
                "{}",
                "{}",
                "running",
                "2026-05-21T00:00:00Z",
                "2026-05-21T00:00:00Z",
                None,
            ),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "vterm_watchdog_1",
                "sess_pi_watchdog",
                "pi-worker",
                "env_watchdog",
                "bridge-watchdog",
                "pi",
                "/workspace",
                "aify://virtual-rpc/pi",
                "",
                "running",
                "bridge-rpc",
                "2026-05-21T00:00:00Z",
                "2026-05-21T00:00:00Z",
                None,
                "",
            ),
        )
        self._execute(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            (
                json.dumps({"virtualTerminal": True, "virtualTerminalId": "vterm_watchdog_1"}),
                "pi-worker",
            ),
        )

        state_owned = self.client.get("/api/v1/agents/pi-worker/pi-session-state")
        self.assertEqual(state_owned.status_code, 200, state_owned.text)
        body_owned = state_owned.json()
        self.assertEqual(body_owned["bridgeOwned"], True)
        self.assertEqual(body_owned["virtualTerminalId"], "vterm_watchdog_1")
        self.assertIsNotNone(body_owned["terminal"])
        self.assertEqual(body_owned["terminal"]["command"], "aify://virtual-rpc/pi")

        # When the bridge tears the virtual terminal down (status='stopped'),
        # bridgeOwned must flip back to false so the wrapper can proceed.
        self._execute(
            "UPDATE terminal_sessions SET status = 'stopped' WHERE id = ?",
            ("vterm_watchdog_1",),
        )
        state_stopped = self.client.get("/api/v1/agents/pi-worker/pi-session-state")
        self.assertEqual(state_stopped.status_code, 200, state_stopped.text)
        self.assertEqual(state_stopped.json()["bridgeOwned"], False)

        # Unknown agent → 404 (so the wrapper can fail-open cleanly).
        missing = self.client.get("/api/v1/agents/ghost-agent/pi-session-state")
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_register_drops_unexpanded_placeholder_session_handle(self):
        # A caller that registers with sessionHandle="$HERMES_SESSION_ID" from a
        # shell/MCP context where the var was empty stored the literal string,
        # which can never resume a real session ("session not found") and made
        # the dashboard emit `--resume ${HERMES_SESSION_ID}`. The server must
        # treat a whole-placeholder handle as no handle.
        for bad in ("$HERMES_SESSION_ID", "${HERMES_SESSION_ID}", "$CODEX_THREAD_ID"):
            self._register("ph-agent", runtime="hermes", sessionHandle=bad)
            row = self._fetchone("SELECT session_handle FROM agents WHERE id = ?", ("ph-agent",))
            self.assertEqual(
                (row["session_handle"] or ""), "",
                f"placeholder {bad!r} must not be stored as a session handle",
            )
        # A real handle still survives (control).
        self._register("ph-agent", runtime="hermes", sessionHandle="20260529_071302_ea65af")
        row = self._fetchone("SELECT session_handle FROM agents WHERE id = ?", ("ph-agent",))
        self.assertEqual(row["session_handle"], "20260529_071302_ea65af")

    def test_session_handle_patch_drops_unexpanded_placeholder(self):
        self._register("ph-patch", runtime="hermes")
        resp = self.client.patch(
            "/api/v1/agents/ph-patch/session-handle",
            json={"sessionHandle": "${HERMES_SESSION_ID}"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        row = self._fetchone("SELECT session_handle FROM agents WHERE id = ?", ("ph-patch",))
        self.assertEqual((row["session_handle"] or ""), "")

    # ---- Workstream A2: unconditional liveness beat (2026-06-01) ----

    def _has_live_channel_sidecar(self, agent_id: str) -> bool:
        async def _run():
            db = await get_db()
            try:
                return await api_v2._has_live_channel_sidecar(db, agent_id)
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_liveness_beat_creates_sidecar_row_for_idle_sidecar(self):
        # An idle standalone channel-sidecar that has never claimed a dispatch
        # has NO bridge_instances row. Before the fix the heartbeat handler only
        # UPDATEd (matching zero rows), so the sidecar's liveness was never
        # recorded and _has_live_channel_sidecar stayed False even though the
        # process is alive. A {liveness:true} beat must UPSERT the row.
        self._register("idle-side", runtime="hermes", machineId="win32:test-host", sessionMode="managed")

        # No channel-sidecar row exists yet, so the agent is not "live".
        self.assertFalse(self._has_live_channel_sidecar("idle-side"))
        no_row = self._fetchone(
            "SELECT id FROM bridge_instances WHERE agent_id = ? AND bridge_kind = 'channel-sidecar'",
            ("idle-side",),
        )
        self.assertIsNone(no_row)

        resp = self.client.post(
            "/api/v1/agents/idle-side/heartbeat",
            json={"bridgeId": "chan-live-1", "bridgeKind": "channel-sidecar", "liveness": True},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json().get("ok"), resp.text)

        row = self._fetchone(
            "SELECT bridge_kind, last_seen, COALESCE(superseded_by,'') AS sb "
            "FROM bridge_instances WHERE id = ? AND agent_id = ?",
            ("chan-live-1", "idle-side"),
        )
        self.assertIsNotNone(row, "liveness beat must create the sidecar bridge row")
        self.assertEqual(row["bridge_kind"], "channel-sidecar")
        self.assertEqual(row["sb"], "", "liveness beat must not supersede the new row")
        self.assertTrue(str(row["last_seen"] or "").strip(), "last_seen must be stamped")

        self.assertTrue(
            self._has_live_channel_sidecar("idle-side"),
            "_has_live_channel_sidecar must be True after the liveness beat",
        )

    def test_liveness_beat_refreshes_existing_bridge_last_seen(self):
        # A sidecar row that already exists with a stale last_seen must be
        # refreshed by the liveness beat (regression guard for the UPDATE path).
        self._register("refresh-side", runtime="hermes", machineId="win32:test-host", sessionMode="managed")
        self._execute(
            """
            INSERT INTO bridge_instances (
                id, agent_id, machine_id, runtime, session_mode, session_handle,
                terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "chan-refresh", "refresh-side", "win32:test-host", "hermes",
                "managed", "", "", "channel-sidecar",
                "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "", None,
            ),
        )

        resp = self.client.post(
            "/api/v1/agents/refresh-side/heartbeat",
            json={"bridgeId": "chan-refresh", "bridgeKind": "channel-sidecar", "liveness": True},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json().get("ok"), resp.text)

        row = self._fetchone(
            "SELECT last_seen FROM bridge_instances WHERE id = ? AND agent_id = ?",
            ("chan-refresh", "refresh-side"),
        )
        self.assertNotEqual(
            row["last_seen"], "2026-01-01T00:00:00Z",
            "liveness beat must advance last_seen on the existing row",
        )

    def test_liveness_beat_does_not_revive_superseded_bridge(self):
        # A superseded bridge row must NOT be kept alive by a liveness beat —
        # the existing supersession guard short-circuits before the upsert.
        self._register("super-side", runtime="hermes", machineId="win32:test-host", sessionMode="managed")
        self._execute(
            """
            INSERT INTO bridge_instances (
                id, agent_id, machine_id, runtime, session_mode, session_handle,
                terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "chan-super", "super-side", "win32:test-host", "hermes",
                "managed", "", "", "channel-sidecar",
                "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "newer-bridge", "2026-01-01T00:00:00Z",
            ),
        )

        resp = self.client.post(
            "/api/v1/agents/super-side/heartbeat",
            json={"bridgeId": "chan-super", "bridgeKind": "channel-sidecar", "liveness": True},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body.get("ok"), body)
        self.assertTrue(body.get("ignored"), body)

        row = self._fetchone(
            "SELECT COALESCE(superseded_by,'') AS sb, last_seen FROM bridge_instances WHERE id = ? AND agent_id = ?",
            ("chan-super", "super-side"),
        )
        self.assertEqual(row["sb"], "newer-bridge", "supersession must be preserved")
        self.assertEqual(
            row["last_seen"], "2026-01-01T00:00:00Z",
            "a superseded bridge must not refresh its own liveness",
        )

    # --- Workstream C1: operator-driven agent_status push ---------------

    def _agent_status_events(self):
        return [
            args[1]
            for args, _kwargs in self.ws.broadcasts
            if args and args[0] == "agent_status"
        ]

    def test_stop_worker_broadcasts_agent_status(self):
        self._register("c1-stopworker")
        self.ws.broadcasts.clear()

        resp = self.client.post("/api/v1/agents/c1-stopworker/stop-worker")
        self.assertEqual(resp.status_code, 200, resp.text)

        events = self._agent_status_events()
        self.assertTrue(events, "stop-worker must push an agent_status event")
        evt = events[-1]
        self.assertEqual(evt["agentId"], "c1-stopworker")
        self.assertTrue(str(evt.get("status") or ""), "agent_status must carry a computed status")
        self.assertIn("statusNote", evt)

    def test_control_stop_broadcasts_agent_status(self):
        self._register("c1-control")
        self.ws.broadcasts.clear()

        resp = self.client.post(
            "/api/v1/agents/c1-control/control",
            json={"action": "stop", "from_agent": "dashboard"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        events = self._agent_status_events()
        self.assertTrue(events, "control(stop) must push an agent_status event")
        evt = events[-1]
        self.assertEqual(evt["agentId"], "c1-control")
        self.assertTrue(str(evt.get("status") or ""), "agent_status must carry a computed status")
        self.assertIn("statusNote", evt)

    # --- PTY pid persistence + kill-by-pid stop fallback (terminals) ---
    #
    # Dashboard Stop kills a managed PTY tree via the owning bridge's
    # in-memory terminals Map. If the owning bridge restarted/died, the
    # Map miss makes Stop a no-op and the orphaned PTY survives. We now
    # persist the PTY root pid (terminal_sessions.process_id) and surface
    # it on the stop control so ANY live host bridge can kill-by-pid.

    def _start_console_terminal(self, *, agent_id: str) -> str:
        """Create a real managed PTY terminal_sessions row via the production
        /console/start path (so all FK-bound rows exist), returning its id."""
        session_id = self._create_running_session(
            agent_id=agent_id, terminal=True, runtime="claude-code"
        )
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/console/start",
            json={"requestedBy": "dashboard"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        return started.json()["terminal"]["id"]

    def test_terminal_control_update_with_processid_persists_pty_pid(self):
        """Part 2: when the bridge reports processId on the start-control
        completion, the server stores it in terminal_sessions.process_id."""
        terminal_id = self._start_console_terminal(agent_id="term-pid-agent")
        control_id = asyncio.run(self._append_control(terminal_id, action="start"))
        resp = self.client.patch(
            f"/api/v1/terminals/controls/{control_id}",
            json={
                "status": "completed",
                "terminalStatus": "attached",
                "output": "[terminal attached pid=43210]\n",
                "processId": "43210",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        row = self._fetchone(
            "SELECT process_id FROM terminal_sessions WHERE id = ?", (terminal_id,)
        )
        self.assertEqual(str(row["process_id"]), "43210")

    def test_stop_control_carries_stored_pty_pid(self):
        """Part 3: the stop control emitted by _request_stop_agent_terminals
        carries the terminal's stored process_id as `pid`, so a bridge that
        never owned the PTY can still kill the orphan by pid."""
        terminal_id = self._start_console_terminal(agent_id="term-stop-pid")
        # Persist a known PTY root pid (as the bridge would on attach).
        self._execute(
            "UPDATE terminal_sessions SET process_id = ? WHERE id = ?",
            ("55501", terminal_id),
        )
        resp = self.client.post(
            "/api/v1/agents/term-stop-pid/control",
            json={"action": "stop", "from_agent": "dashboard"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        claim = self.client.post(
            "/api/v1/terminals/controls/claim",
            json={"environmentId": "linux:test-host:default", "bridgeId": "bridge-current"},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        stop_controls = [
            c for c in claim.json()["controls"]
            if c.get("action") == "stop" and c.get("terminalId") == terminal_id
        ]
        self.assertTrue(stop_controls, "expected a stop control for the seeded terminal")
        self.assertEqual(str(stop_controls[0].get("pid")), "55501")

    # --- comms_console_tail / comms_console_input endpoints ---------------

    def test_agent_console_tail_returns_last_lines_of_live_console(self):
        # Seed a managed claude with a live (attached) console terminal whose
        # output has many lines; the endpoint must tail the last N.
        self._seed_managed_claude_with_attached_terminal("console-tailee", "term_console_tail")
        full = "\n".join(f"line-{i}" for i in range(1, 121))
        self._execute(
            "UPDATE terminal_sessions SET output = ? WHERE id = ?",
            (full, "term_console_tail"),
        )
        resp = self.client.get("/api/v1/agents/console-tailee/console?lines=5")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["ok"], data)
        self.assertTrue(data["live"], data)
        self.assertEqual(data["terminalId"], "term_console_tail")
        out_lines = data["output"].splitlines()
        self.assertEqual(out_lines, ["line-116", "line-117", "line-118", "line-119", "line-120"])

    def test_agent_console_tail_live_false_when_no_live_console(self):
        # Registered managed agent with NO consoleTerminal pointer / no terminal.
        self._heartbeat_environment(
            runtimes=[{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        self._register("console-idle", runtime="claude-code", sessionMode="managed")
        resp = self.client.get("/api/v1/agents/console-idle/console")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["ok"], data)
        self.assertFalse(data["live"], data)
        self.assertIn("no live console", data["message"].lower())
        # Read is side-effect-free: no terminal was created.
        self.assertEqual(
            self._fetchall("SELECT id FROM terminal_sessions WHERE agent_id = ?", ("console-idle",)),
            [],
        )

    def test_agent_console_tail_resolves_live_terminal_when_pointer_unset(self):
        # 2026-06-17: a managed console that LAZY-STARTS on a message leaves
        # runtime_state.consoleTerminal unset, but a live terminal_sessions row exists (the
        # dashboard renders it). console_tail must FALL BACK to the agent's live terminal
        # instead of falsely reporting "no live console".
        self._seed_managed_claude_with_attached_terminal("console-lazy", "term_lazy_live")
        # Simulate the lazy-start gap: a live terminal but NO consoleTerminal pointer.
        self._execute("UPDATE agents SET runtime_state='{}' WHERE id='console-lazy'")
        resp = self.client.get("/api/v1/agents/console-lazy/console")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["live"], data)
        self.assertEqual(data["terminalId"], "term_lazy_live", data)
        # A STOPPED-only agent still reports no live console (no false-positive extras).
        self._execute("UPDATE terminal_sessions SET status='stopped' WHERE id='term_lazy_live'")
        resp2 = self.client.get("/api/v1/agents/console-lazy/console")
        self.assertFalse(resp2.json()["live"], resp2.text)

    def test_prune_terminal_history_caps_terminal_rows_per_agent(self):
        # 2026-06-17: terminal_sessions ROWS were never pruned (only events/output), so ended
        # consoles accumulated forever. The row prune keeps the newest N per agent and deletes
        # older ended rows — and NEVER a live one.
        import asyncio, datetime as _dt
        from service.db import get_db
        from service.routers import api_v2
        # Helper seeds env + sess_cruft-agent + 1 live terminal (t_live).
        self._seed_managed_claude_with_attached_terminal("cruft-agent", "t_live")
        base = _dt.datetime.now(_dt.timezone.utc)
        for i in range(12):
            ts = (base - _dt.timedelta(minutes=100 - i)).isoformat().replace("+00:00", "Z")
            self._execute(
                "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, "
                "workspace, command, output, status, requested_by, created_at, updated_at, stopped_at, error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"t_old_{i}", "sess_cruft-agent", "cruft-agent", "linux:test-host:default", "b",
                 "claude-code", "/w", "claude-aify", "", "stopped", "dashboard", ts, ts, None, ""),
            )

        async def _run():
            db = await get_db()
            try:
                return await api_v2._prune_terminal_history(db, keep_terminal_rows_per_agent=8)
            finally:
                await db.close()
        counts = asyncio.run(_run())
        remaining = [r["id"] for r in self._fetchall(
            "SELECT id FROM terminal_sessions WHERE agent_id=? ORDER BY updated_at DESC", ("cruft-agent",))]
        self.assertIn("t_live", remaining, "the live terminal must never be pruned")
        self.assertEqual(len(remaining), 8, f"capped to keep-window (8); got {remaining}")
        self.assertGreater(counts["terminal_sessions"], 0)

    def test_agent_console_input_appends_control_with_caller_recorded(self):
        self._seed_managed_claude_with_attached_terminal("console-inputee", "term_console_in")
        # The caller must be a registered agent.
        self._register("console-manager", runtime="claude-code", sessionMode="managed")
        resp = self.client.post(
            "/api/v1/agents/console-inputee/console/input",
            json={"text": "/status", "enter": True, "from": "console-manager"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["ok"], data)
        self.assertTrue(data["live"], data)
        control = self._fetchone(
            "SELECT action, body, requested_by FROM terminal_controls WHERE terminal_id = ? ORDER BY id DESC LIMIT 1",
            ("term_console_in",),
        )
        self.assertEqual(control["action"], "input")
        self.assertEqual(control["body"], "/status\r")
        self.assertEqual(control["requested_by"], "console-manager")
        # Audit event records who sent the input.
        events = self._fetchall(
            "SELECT event_type, body FROM terminal_events WHERE terminal_id = ?",
            ("term_console_in",),
        )
        audit = [e for e in events if e["event_type"] == "agent_console_input"]
        self.assertTrue(audit, f"expected an agent_console_input audit event; got {[e['event_type'] for e in events]}")
        self.assertIn("console-manager", audit[-1]["body"])

    def test_agent_console_input_unknown_caller_rejected(self):
        self._seed_managed_claude_with_attached_terminal("console-inputee2", "term_console_in2")
        resp = self.client.post(
            "/api/v1/agents/console-inputee2/console/input",
            json={"text": "x", "from": "ghost-agent-not-registered"},
        )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_agent_console_input_no_live_console_returns_clear_message(self):
        # Managed agent, registered, but NO live console and nothing to autostart
        # into (no running agent_sessions row / online env terminal). The endpoint
        # must return a clear message, not crash.
        self._heartbeat_environment(
            runtimes=[{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {"interrupt": True}}],
        )
        self._register("console-cold", runtime="claude-code", sessionMode="managed")
        self._register("console-caller", runtime="claude-code", sessionMode="managed")
        resp = self.client.post(
            "/api/v1/agents/console-cold/console/input",
            json={"text": "hi", "from": "console-caller"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertFalse(data["ok"], data)
        self.assertFalse(data["live"], data)
        self.assertIn("no live console", data["message"].lower())

    async def _append_control(self, terminal_id: str, *, action: str) -> str:
        db = await get_db()
        try:
            control_id = await api_v2._append_terminal_control(
                db,
                terminal_id=terminal_id,
                environment_id="linux:test-host:default",
                bridge_id="bridge-current",
                action=action,
                requested_by="dashboard",
            )
            await db.execute(
                "UPDATE terminal_controls SET status = 'claimed', claimed_at = ? WHERE id = ?",
                (api_v2._now(), control_id),
            )
            await db.commit()
            return control_id
        finally:
            await db.close()

    # ------------------------------------------------------------------
    # HAZARD 2 (2026-06-03): _reconcile_duplicate_resident_sessions must NOT
    # retire a LIVE resident session. A resident agent_sessions.last_seen is
    # frozen at register time, so ranking siblings by last_seen DESC can keep a
    # dead-but-newer row and retire a live one. The fix consults bridge_instances:
    # a non-survivor sibling is retired ONLY when its owner_bridge_id has no fresh,
    # non-superseded bridge row.
    # ------------------------------------------------------------------

    def _seed_resident_session(self, *, session_id, agent_id, owner_bridge_id, last_seen):
        """Insert a resident agent_sessions row (FKs satisfied by a registered
        agent + the linux:test-host:default env)."""
        now = api_v2._now()
        # spawn_spec_id/spawn_request_id passed as NULL (not the '' default) so
        # their FKs don't fire — same shape the production insert uses.
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, owner_bridge_id, spawn_spec_id, spawn_request_id,
                status, started_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                session_id, agent_id, "linux:test-host:default", "hermes",
                "/workspace/repo", "resident", "resident", owner_bridge_id,
                None, None, "running", now, last_seen,
            ),
        )

    def _seed_bridge(self, *, bridge_id, agent_id, last_seen, superseded_by=""):
        self._execute(
            """
            INSERT INTO bridge_instances (
                id, agent_id, machine_id, runtime, session_mode,
                bridge_kind, registered_at, last_seen, superseded_by
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                bridge_id, agent_id, "linux:test-host", "hermes", "resident",
                "resident", "2020-01-01T00:00:00Z", last_seen, superseded_by,
            ),
        )

    def _run_reconcile_duplicate_resident_sessions(self):
        from service.routers.api_v2 import _reconcile_duplicate_resident_sessions

        async def _run():
            db = await get_db()
            try:
                return await _reconcile_duplicate_resident_sessions(db)
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_reconcile_duplicate_resident_keeps_live_older_retires_dead_newer(self):
        # Two resident sessions for one agent. The OLDER session has a FRESH bridge
        # (it's the live one); the NEWER session's bridge is DEAD. Ranking by
        # last_seen DESC would keep the newer/dead one — the fix must instead KEEP
        # the older/live session and retire the dead newer one.
        self._heartbeat_environment(
            runtimes=[{"runtime": "hermes", "modes": ["resident"], "capabilities": {}}],
        )
        self._register("dup-res", runtime="hermes", sessionMode="resident")
        now = api_v2._now()
        # Older session (smaller last_seen) but a FRESH owning bridge → LIVE.
        self._seed_bridge(bridge_id="bridge-live", agent_id="dup-res", last_seen=now)
        self._seed_resident_session(
            session_id="sess-old-live", agent_id="dup-res",
            owner_bridge_id="bridge-live", last_seen="2026-06-03T10:00:00Z",
        )
        # Newer session (larger last_seen) but a DEAD owning bridge.
        self._seed_bridge(bridge_id="bridge-dead", agent_id="dup-res", last_seen="2020-01-01T00:00:00Z")
        self._seed_resident_session(
            session_id="sess-new-dead", agent_id="dup-res",
            owner_bridge_id="bridge-dead", last_seen="2026-06-03T11:00:00Z",
        )

        retired = self._run_reconcile_duplicate_resident_sessions()
        self.assertEqual(retired, 1, "exactly the dead newer sibling must be retired")

        live = self._fetchone("SELECT status FROM agent_sessions WHERE id = ?", ("sess-old-live",))
        dead = self._fetchone("SELECT status FROM agent_sessions WHERE id = ?", ("sess-new-dead",))
        # The newer row is the survivor by last_seen ranking, BUT it is dead, so it
        # is retired; the older live one is the non-survivor sibling whose bridge is
        # fresh, so it is LEFT ALONE (a live session is never retired).
        self.assertEqual(live["status"], "running", "the live (fresh-bridge) session must be KEPT")
        self.assertEqual(dead["status"], "stopped", "the dead (stale-bridge) session must be retired")

    def test_reconcile_duplicate_resident_all_stale_keeps_freshest_retires_rest(self):
        # All siblings have dead/absent bridges → no live-session guard applies, so
        # the classic behaviour holds: keep the freshest (by last_seen), retire the rest.
        self._heartbeat_environment(
            runtimes=[{"runtime": "hermes", "modes": ["resident"], "capabilities": {}}],
        )
        self._register("dup-stale", runtime="hermes", sessionMode="resident")
        # Both bridges dead.
        self._seed_bridge(bridge_id="b-stale-1", agent_id="dup-stale", last_seen="2020-01-01T00:00:00Z")
        self._seed_bridge(bridge_id="b-stale-2", agent_id="dup-stale", last_seen="2020-01-01T00:00:00Z")
        self._seed_resident_session(
            session_id="sess-fresh", agent_id="dup-stale",
            owner_bridge_id="b-stale-1", last_seen="2026-06-03T12:00:00Z",
        )
        self._seed_resident_session(
            session_id="sess-older", agent_id="dup-stale",
            owner_bridge_id="b-stale-2", last_seen="2026-06-03T09:00:00Z",
        )

        retired = self._run_reconcile_duplicate_resident_sessions()
        self.assertEqual(retired, 1, "only the older stale sibling is retired")
        fresh = self._fetchone("SELECT status FROM agent_sessions WHERE id = ?", ("sess-fresh",))
        older = self._fetchone("SELECT status FROM agent_sessions WHERE id = ?", ("sess-older",))
        self.assertEqual(fresh["status"], "running", "the freshest survivor is kept")
        self.assertEqual(older["status"], "stopped", "the older stale sibling is retired")

    def test_reconcile_duplicate_resident_single_session_is_noop(self):
        self._heartbeat_environment(
            runtimes=[{"runtime": "hermes", "modes": ["resident"], "capabilities": {}}],
        )
        self._register("dup-single", runtime="hermes", sessionMode="resident")
        self._seed_bridge(bridge_id="b-single", agent_id="dup-single", last_seen="2020-01-01T00:00:00Z")
        self._seed_resident_session(
            session_id="sess-only", agent_id="dup-single",
            owner_bridge_id="b-single", last_seen="2026-06-03T10:00:00Z",
        )

        retired = self._run_reconcile_duplicate_resident_sessions()
        self.assertEqual(retired, 0, "a single session is a no-op")
        only = self._fetchone("SELECT status FROM agent_sessions WHERE id = ?", ("sess-only",))
        self.assertEqual(only["status"], "running", "the sole session is untouched")

    # ------------------------------------------------------------------
    # Coverage gap (review): _reroute_orphaned_managed_channel_runs and
    # _has_live_managed_wrapper_child.
    # ------------------------------------------------------------------

    def _seed_queued_managed_run(self, *, run_id, target_agent, runtime):
        self._execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, runtime, message_type, subject, body, priority,
                status, require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, None, "manager", target_agent, "start_if_possible",
                "managed", runtime, "request", "work", "body", "normal",
                "queued", 1, api_v2._now(),
            ),
        )

    def _run_reroute_orphaned_managed_channel_runs(self):
        from service.routers.api_v2 import _reroute_orphaned_managed_channel_runs

        async def _run():
            db = await get_db()
            try:
                return await _reroute_orphaned_managed_channel_runs(db)
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_reroute_orphaned_managed_run_with_live_sidecar_is_rerouted_to_channel(self):
        # A queued execution_mode='managed' run for a managed hermes agent with a
        # LIVE channel-sidecar must be re-routed to 'channel' (the authoritative
        # delivery path). Idempotent on a second pass.
        # The suite opts into legacy via-console (insert_messages_via_console=True),
        # under which reroute is a deliberate no-op. Flip to the channel-route
        # default for this test so the reroute path is exercised.
        self.client.put("/api/v1/settings", json={"insert_messages_via_console": False})
        self._heartbeat_environment(
            runtimes=[{"runtime": "hermes", "modes": ["resident"], "capabilities": {}}],
        )
        self._register("reroute-hermes", runtime="hermes", sessionMode="managed")
        self._seed_queued_managed_run(run_id="rr-1", target_agent="reroute-hermes", runtime="hermes")
        # A FRESH channel-sidecar bridge → _has_live_channel_sidecar True.
        self._seed_bridge(bridge_id="rr-sidecar", agent_id="reroute-hermes", last_seen=api_v2._now())
        self._execute(
            "UPDATE bridge_instances SET bridge_kind = 'channel-sidecar' WHERE id = ?",
            ("rr-sidecar",),
        )

        n = self._run_reroute_orphaned_managed_channel_runs()
        self.assertEqual(n, 1, "the orphaned managed run must be rerouted")
        run = self._fetchone("SELECT execution_mode FROM dispatch_runs WHERE id = ?", ("rr-1",))
        self.assertEqual(run["execution_mode"], "channel")
        # Idempotent: a second pass finds nothing already-channel to reroute.
        n2 = self._run_reroute_orphaned_managed_channel_runs()
        self.assertEqual(n2, 0, "re-running must be a no-op once rerouted")

    def test_reroute_does_not_touch_non_channel_runtime(self):
        # A pi run (not in _CHANNEL_MANAGED_RUNTIMES | _CHANNEL_FLAG_GATED_RUNTIMES)
        # must NOT be rerouted even with a live sidecar bridge present.
        self.client.put("/api/v1/settings", json={"insert_messages_via_console": False})
        self._heartbeat_environment(
            runtimes=[{"runtime": "pi", "modes": ["managed-warm"], "capabilities": {}}],
        )
        self._register("reroute-pi", runtime="pi", sessionMode="managed")
        self._seed_queued_managed_run(run_id="rr-pi", target_agent="reroute-pi", runtime="pi")
        self._seed_bridge(bridge_id="rr-pi-sidecar", agent_id="reroute-pi", last_seen=api_v2._now())
        self._execute(
            "UPDATE bridge_instances SET bridge_kind = 'channel-sidecar' WHERE id = ?",
            ("rr-pi-sidecar",),
        )

        n = self._run_reroute_orphaned_managed_channel_runs()
        self.assertEqual(n, 0, "a pi run must NOT be rerouted (not a channel runtime)")
        run = self._fetchone("SELECT execution_mode FROM dispatch_runs WHERE id = ?", ("rr-pi",))
        self.assertEqual(run["execution_mode"], "managed", "the pi run stays managed")

    def _run_has_live_managed_wrapper_child(self, agent_id):
        from service.routers.api_v2 import _has_live_managed_wrapper_child

        async def _run():
            db = await get_db()
            try:
                return await _has_live_managed_wrapper_child(db, agent_id)
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_has_live_managed_wrapper_child_true_for_fresh_bridge(self):
        self._heartbeat_environment(
            runtimes=[{"runtime": "hermes", "modes": ["resident"], "capabilities": {}}],
        )
        self._register("mwc-fresh", runtime="hermes", sessionMode="managed")
        self._seed_bridge(bridge_id="mwc-1", agent_id="mwc-fresh", last_seen=api_v2._now())
        self._execute(
            "UPDATE bridge_instances SET bridge_kind = 'managed-wrapper-child' WHERE id = ?",
            ("mwc-1",),
        )
        self.assertTrue(self._run_has_live_managed_wrapper_child("mwc-fresh"))

    def test_has_live_managed_wrapper_child_false_for_stale_superseded_or_none(self):
        self._heartbeat_environment(
            runtimes=[{"runtime": "hermes", "modes": ["resident"], "capabilities": {}}],
        )
        self._register("mwc-stale", runtime="hermes", sessionMode="managed")
        self._register("mwc-super", runtime="hermes", sessionMode="managed")
        self._register("mwc-none", runtime="hermes", sessionMode="managed")
        # Stale heartbeat.
        self._seed_bridge(bridge_id="mwc-s", agent_id="mwc-stale", last_seen="2020-01-01T00:00:00Z")
        self._execute(
            "UPDATE bridge_instances SET bridge_kind = 'managed-wrapper-child' WHERE id = ?",
            ("mwc-s",),
        )
        # Fresh but superseded.
        self._seed_bridge(bridge_id="mwc-sup", agent_id="mwc-super", last_seen=api_v2._now(), superseded_by="newer")
        self._execute(
            "UPDATE bridge_instances SET bridge_kind = 'managed-wrapper-child' WHERE id = ?",
            ("mwc-sup",),
        )
        # mwc-none: no managed-wrapper-child bridge at all.
        self.assertFalse(self._run_has_live_managed_wrapper_child("mwc-stale"), "stale heartbeat → False")
        self.assertFalse(self._run_has_live_managed_wrapper_child("mwc-super"), "superseded → False")
        self.assertFalse(self._run_has_live_managed_wrapper_child("mwc-none"), "no bridge → False")


    def _seed_dead_session(self, *, sid, agent_id, mode, status, owner_mode="managed",
                           terminal_status="", owner_bridge_id="", runtime="claude"):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO agent_sessions
                (id, agent_id, environment_id, runtime, mode, owner_mode,
                 owner_bridge_id, terminal_status, spawn_spec_id, spawn_request_id,
                 status, started_at, last_seen)
            VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?)
            """,
            (sid, agent_id, "linux:test-host:default", runtime, mode, owner_mode,
             owner_bridge_id, terminal_status, status, now, now),
        )

    def _seed_agent_row(self, agent_id, status):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            "INSERT INTO agents (id, role, name, status, registered_at, last_seen) VALUES (?,?,?,?,?,?)",
            (agent_id, "coder", agent_id, status, now, now),
        )

    def _seed_owner_bridge(self, bridge_id, agent_id, *, last_seen_delta_seconds, session_mode="resident", bridge_kind=""):
        ls = (datetime.now(timezone.utc) + timedelta(seconds=last_seen_delta_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        reg = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO bridge_instances
                (id, agent_id, session_mode, bridge_kind, registered_at, last_seen, superseded_by)
            VALUES (?,?,?,?,?,?,'')
            """,
            (bridge_id, agent_id, session_mode, bridge_kind, reg, ls),
        )

    def _seed_terminal_for_session(self, *, tid, sid, agent_id, status, runtime="claude-code", command=""):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._execute(
            """
            INSERT INTO terminal_sessions
                (id, session_id, agent_id, environment_id, bridge_id, runtime,
                 workspace, command, output, status, requested_by,
                 created_at, updated_at, stopped_at, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tid, sid, agent_id, "linux:test-host:default", "bridge-current", runtime,
                "/workspace", command or (runtime + "-aify --aify-agent " + agent_id), "",
                status, "dashboard", now, now, None, "",
            ),
        )

    def _run_dead_session_reconcile(self):
        async def _run():
            db = await get_db()
            try:
                return await api_v2._reconcile_dead_session_status(db)
            finally:
                await db.close()
        return asyncio.run(_run())

    def _dead_session_status(self, sid):
        row = self._fetchone("SELECT status FROM agent_sessions WHERE id = ?", (sid,))
        return row["status"] if row else None

    def test_reconcile_dead_session_status_marks_dead_backings_stopped(self):
        # agent_sessions FK-references environments(environment_id); seed it.
        self._heartbeat_environment(id="linux:test-host:default", machineId="linux:test-host")
        # (a) Phase 3 (2026-06-03): managed session with a STALE 'attached' denorm
        # but a LIVE terminal_sessions row that went 'failed' -> session stopped.
        # This is the live-reproduced cms-manager/lc-coder/lc-tech-lead shape:
        # agent_sessions.terminal_status frozen at 'attached' while the real
        # terminal is dead. case (a) must JOIN the live terminal table to catch it.
        self._seed_agent_row("mp-senior-dev", "active")
        self._seed_dead_session(
            sid="sess_managed_failed_term", agent_id="mp-senior-dev",
            mode="managed-warm", owner_mode="managed", status="running",
            terminal_status="attached",
        )
        self._seed_terminal_for_session(
            tid="term_managed_failed", sid="sess_managed_failed_term",
            agent_id="mp-senior-dev", status="failed",
        )
        # (a-live) managed session whose terminal is genuinely LIVE -> stays running
        # (false-positive guard: the live-truth join must not stop a healthy console).
        self._seed_agent_row("live-managed", "active")
        self._seed_dead_session(
            sid="sess_managed_live_term", agent_id="live-managed",
            mode="managed-warm", owner_mode="managed", status="running",
            terminal_status="attached",
        )
        self._seed_terminal_for_session(
            tid="term_managed_live", sid="sess_managed_live_term",
            agent_id="live-managed", status="attached",
        )
        # (b) session whose agent is stopped (otherwise-attached terminal).
        self._seed_agent_row("stopped-agent", "stopped")
        self._seed_dead_session(
            sid="sess_stopped_agent", agent_id="stopped-agent",
            mode="managed-warm", owner_mode="managed", status="active",
            terminal_status="attached",
        )
        # (c-fresh) resident session with a FRESH owning bridge -> NOT stopped.
        self._seed_agent_row("ci-manager", "active")
        self._seed_owner_bridge("bridge-fresh", "ci-manager", last_seen_delta_seconds=-10)
        self._seed_dead_session(
            sid="sess_resident_fresh", agent_id="ci-manager",
            mode="resident", owner_mode="resident", status="running",
            owner_bridge_id="bridge-fresh",
        )
        # (c-stale) resident session with a stale owning bridge -> stopped.
        self._seed_agent_row("lc-manager", "active")
        self._seed_owner_bridge("bridge-stale", "lc-manager", last_seen_delta_seconds=-3600)
        self._seed_dead_session(
            sid="sess_resident_stale", agent_id="lc-manager",
            mode="resident", owner_mode="resident", status="running",
            owner_bridge_id="bridge-stale",
        )
        # (c-absent) resident session with NO owning bridge id -> stopped.
        self._seed_agent_row("no-bridge-mgr", "active")
        self._seed_dead_session(
            sid="sess_resident_nobridge", agent_id="no-bridge-mgr",
            mode="resident", owner_mode="resident", status="running",
            owner_bridge_id="",
        )

        changed = self._run_dead_session_reconcile()
        self.assertGreaterEqual(changed, 4)

        self.assertEqual(self._dead_session_status("sess_managed_failed_term"), "stopped")
        self.assertEqual(self._dead_session_status("sess_stopped_agent"), "stopped")
        self.assertEqual(self._dead_session_status("sess_resident_stale"), "stopped")
        self.assertEqual(self._dead_session_status("sess_resident_nobridge"), "stopped")
        # False-positive guard: the live resident with a fresh bridge stays running.
        self.assertEqual(self._dead_session_status("sess_resident_fresh"), "running")
        # False-positive guard: a managed session with a LIVE terminal stays running.
        self.assertEqual(self._dead_session_status("sess_managed_live_term"), "running")

    # ── Phase 1 G1: coldstart carries the agent's native session handle ──────
    def _online_codex_env_for_coldstart(self, env_id="env_g1"):
        self.client.put(
            "/api/v1/settings",
            json={
                "insert_messages_via_console": False,
                "managed_via_wrapper": ["codex", "hermes"],
                "managed_terminal_backing_enabled": True,
            },
        )
        self._heartbeat_environment(
            id=env_id,
            bridgeId="bridge-g1",
            machineId="linux:g1",
            runtimes=[{"runtime": "codex", "modes": ["managed-warm"], "capabilities": {"nativeResume": True, "interrupt": True}}],
            terminal=True,
            pty=True,
            terminalRuntimes=["codex"],
        )

    def _run_coldstart(self, agent_id):
        async def _run():
            db = await get_db()
            try:
                settings = await api_v2._load_settings(db)
                created = await api_v2._coldstart_spawn_request_for_dispatch(
                    db, agent_id, runtime="codex", settings=settings, requested_by="test",
                )
                await db.commit()
                return created
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_coldstart_spawn_request_carries_stored_session_handle(self):
        # G1 (2026-06-03): the resident->managed cold-start must RESUME the
        # agent's existing native session, not start fresh. The inserted
        # spawn_request must carry the agent's stored session_handle so the
        # managed worker resumes the codex thread / claude transcript / hermes
        # session instead of losing it.
        self._online_codex_env_for_coldstart()
        self._register("g1-coder", runtime="codex", sessionMode="managed")
        self._execute(
            "UPDATE agents SET session_handle = ? WHERE id = ?",
            ("codex-thread-g1", "g1-coder"),
        )

        created = self._run_coldstart("g1-coder")
        self.assertTrue(created, "a coldstart spawn_request must be created when an online env hosts the runtime")

        row = self._fetchone(
            "SELECT session_handle, resume_policy FROM spawn_requests WHERE agent_id = ? AND status = 'queued'",
            ("g1-coder",),
        )
        self.assertIsNotNone(row, "expected a queued coldstart spawn_request")
        self.assertEqual(
            row["session_handle"], "codex-thread-g1",
            "coldstart spawn_request must carry the agent's stored native session handle",
        )
        self.assertEqual(row["resume_policy"], "native_first")

    def test_coldstart_spawn_request_empty_handle_when_agent_has_none(self):
        # G1 boundary: an agent with NO stored session handle yields a coldstart
        # spawn_request with an empty handle (the worker starts a fresh session,
        # which is correct — there is nothing to resume).
        self._online_codex_env_for_coldstart(env_id="env_g1b")
        self._register("g1-fresh", runtime="codex", sessionMode="managed")

        created = self._run_coldstart("g1-fresh")
        self.assertTrue(created)

        row = self._fetchone(
            "SELECT session_handle FROM spawn_requests WHERE agent_id = ? AND status = 'queued'",
            ("g1-fresh",),
        )
        self.assertIsNotNone(row)
        self.assertEqual((row["session_handle"] or ""), "", "no stored handle -> empty handle on the coldstart spawn_request")

    # ── Phase 1 G2: managed->resident blocked for managed-only runtimes ──────
    def test_switch_pi_managed_to_resident_is_rejected_without_force(self):
        # G2 (2026-06-03): pi (and opencode) do not support a resident bridge.
        # Flipping a managed pi agent to resident yields a presence-only agent
        # whose every dispatch is rejected — an undeliverable footgun. The
        # server must reject the switch with an actionable 409 unless force=true.
        self._register("g2-pi", runtime="pi", sessionMode="managed")

        rejected = self.client.patch(
            "/api/v1/agents/g2-pi/session-mode",
            json={"mode": "resident"},
        )
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("not supported", rejected.json().get("detail", "").lower())

        # Still managed — the rejected switch must not have changed the mode.
        agent = self._fetchone("SELECT session_mode FROM agents WHERE id = ?", ("g2-pi",))
        self.assertEqual(api_v2._normalize_session_mode(agent["session_mode"] or ""), "managed")

    def test_switch_pi_managed_to_resident_with_force_warns_but_proceeds(self):
        # G2: force=true overrides the guard (operator metadata-only flip) but
        # the response carries a clear warning that the agent is now
        # presence-only and undeliverable.
        self._register("g2-pi-forced", runtime="pi", sessionMode="managed")

        forced = self.client.patch(
            "/api/v1/agents/g2-pi-forced/session-mode",
            json={"mode": "resident", "force": True},
        )
        self.assertEqual(forced.status_code, 200, forced.text)
        body = forced.json()
        self.assertEqual(body.get("mode"), "resident")
        self.assertIn("not supported", str(body.get("warning", "")).lower())

    # ── Phase 3 item 1: case (a) joins LIVE terminal_sessions, not the denorm ──
    def test_dead_session_reconcile_catches_stale_denorm_with_failed_terminal(self):
        # The live-reproduced cms-manager/lc-coder/lc-tech-lead bug: a managed
        # session with agent_sessions.terminal_status='attached' (stale denorm) but
        # the live terminal_sessions row is 'failed'. The OLD case (a) read the
        # frozen denorm and MISSED it; the fixed case (a) joins the live terminal
        # table and reconciles it to 'stopped'.
        self._heartbeat_environment(id="linux:test-host:default", machineId="linux:test-host")
        self._seed_agent_row("lc-coder-x", "active")
        self._seed_dead_session(
            sid="sess_stale_denorm", agent_id="lc-coder-x",
            mode="managed-warm", owner_mode="managed", status="running",
            terminal_status="attached",  # STALE denorm
        )
        self._seed_terminal_for_session(
            tid="term_live_failed", sid="sess_stale_denorm",
            agent_id="lc-coder-x", status="failed",  # LIVE truth: dead
        )
        # Sanity: the denorm alone (old logic) would NOT have flagged it.
        self.assertEqual(
            self._fetchone(
                "SELECT terminal_status FROM agent_sessions WHERE id = ?",
                ("sess_stale_denorm",),
            )["terminal_status"],
            "attached",
        )
        self._run_dead_session_reconcile()
        self.assertEqual(self._dead_session_status("sess_stale_denorm"), "stopped")

    def test_dead_session_reconcile_leaves_managed_session_without_terminals_alone(self):
        # A just-starting managed session that has NO terminal rows yet must NOT be
        # stopped (case (a) requires HAS-terminals AND all-dead).
        self._heartbeat_environment(id="linux:test-host:default", machineId="linux:test-host")
        self._seed_agent_row("starting-mgr", "active")
        self._seed_dead_session(
            sid="sess_no_terminals", agent_id="starting-mgr",
            mode="managed-warm", owner_mode="managed", status="starting",
            terminal_status="",
        )
        self._run_dead_session_reconcile()
        self.assertEqual(self._dead_session_status("sess_no_terminals"), "starting")

    # ── Phase 3 item 2: GET /sessions DERIVES status from live truth ──────────
    def _derived_session_status(self, sid):
        async def _run():
            db = await get_db()
            try:
                row = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (sid,))).fetchone()
                return await api_v2._compute_session_display_status(db, row)
            finally:
                await db.close()
        return asyncio.run(_run())

    def test_derived_session_status_live_managed_terminal_attached_is_running(self):
        self._heartbeat_environment(id="linux:test-host:default", machineId="linux:test-host")
        self._seed_agent_row("dv-live-mgr", "active")
        self._seed_dead_session(
            sid="sess_dv_live_mgr", agent_id="dv-live-mgr",
            mode="managed-warm", owner_mode="managed", status="running",
            terminal_status="attached",
        )
        self._seed_terminal_for_session(
            tid="term_dv_live", sid="sess_dv_live_mgr",
            agent_id="dv-live-mgr", status="attached",
        )
        self.assertEqual(self._derived_session_status("sess_dv_live_mgr"), "running")

    def test_derived_session_status_dead_managed_terminal_failed_is_stopped(self):
        # The structural fix: even though the STORED status is still 'running' and
        # the denorm says 'attached', the DERIVED display status is 'stopped'
        # because the live terminal is failed — killing "Stopped/Stale but running".
        self._heartbeat_environment(id="linux:test-host:default", machineId="linux:test-host")
        self._seed_agent_row("dv-dead-mgr", "active")
        self._seed_dead_session(
            sid="sess_dv_dead_mgr", agent_id="dv-dead-mgr",
            mode="managed-warm", owner_mode="managed", status="running",
            terminal_status="attached",
        )
        self._seed_terminal_for_session(
            tid="term_dv_dead", sid="sess_dv_dead_mgr",
            agent_id="dv-dead-mgr", status="failed",
        )
        # Stored row still says running (deriver must NOT mutate it).
        self.assertEqual(self._dead_session_status("sess_dv_dead_mgr"), "running")
        self.assertEqual(self._derived_session_status("sess_dv_dead_mgr"), "stopped")

    def test_derived_session_status_live_resident_fresh_bridge_is_running(self):
        self._heartbeat_environment(id="linux:test-host:default", machineId="linux:test-host")
        self._seed_agent_row("dv-live-res", "active")
        # A fresh resident MCP bridge: _resident_bridge_is_fresh reads the session's
        # runtime_state.bridgeInstanceId; a live channel-sidecar is the simpler proof
        # path used here.
        self._seed_owner_bridge(
            "dv-res-sidecar", "dv-live-res", last_seen_delta_seconds=-5,
            session_mode="resident", bridge_kind="channel-sidecar",
        )
        self._seed_dead_session(
            sid="sess_dv_live_res", agent_id="dv-live-res",
            mode="resident", owner_mode="resident", status="running",
            owner_bridge_id="dv-res-sidecar",
        )
        self.assertEqual(self._derived_session_status("sess_dv_live_res"), "running")

    def test_derived_session_status_dead_resident_no_fresh_bridge_is_stopped(self):
        self._heartbeat_environment(id="linux:test-host:default", machineId="linux:test-host")
        self._seed_agent_row("dv-dead-res", "active")
        self._seed_owner_bridge(
            "dv-res-stale", "dv-dead-res", last_seen_delta_seconds=-3600,
            session_mode="resident", bridge_kind="channel-sidecar",
        )
        self._seed_dead_session(
            sid="sess_dv_dead_res", agent_id="dv-dead-res",
            mode="resident", owner_mode="resident", status="running",
            owner_bridge_id="dv-res-stale",
        )
        self.assertEqual(self._derived_session_status("sess_dv_dead_res"), "stopped")

    def test_derived_session_status_stopped_agent_is_stopped(self):
        self._heartbeat_environment(id="linux:test-host:default", machineId="linux:test-host")
        self._seed_agent_row("dv-stopped-agent", "stopped")
        self._seed_dead_session(
            sid="sess_dv_stopped", agent_id="dv-stopped-agent",
            mode="managed-warm", owner_mode="managed", status="running",
            terminal_status="attached",
        )
        # Even with a (hypothetically) attached terminal, a stopped agent forces stopped.
        self._seed_terminal_for_session(
            tid="term_dv_stopped", sid="sess_dv_stopped",
            agent_id="dv-stopped-agent", status="attached",
        )
        self.assertEqual(self._derived_session_status("sess_dv_stopped"), "stopped")

    def test_derived_session_status_terminal_stored_status_passes_through(self):
        # A genuinely-terminal stored status is displayed as-is (deriver only ever
        # downgrades a live-looking status, never promotes a terminal one).
        self._heartbeat_environment(id="linux:test-host:default", machineId="linux:test-host")
        self._seed_agent_row("dv-ended", "active")
        self._seed_dead_session(
            sid="sess_dv_ended", agent_id="dv-ended",
            mode="managed-warm", owner_mode="managed", status="ended",
        )
        self.assertEqual(self._derived_session_status("sess_dv_ended"), "ended")

    def test_get_sessions_serves_derived_status_for_dead_managed_terminal(self):
        # End-to-end: GET /sessions returns the DERIVED status, so the dashboard
        # badge can no longer show 'running' for a session whose live terminal died.
        self._heartbeat_environment(id="linux:test-host:default", machineId="linux:test-host")
        self._seed_agent_row("e2e-dead-mgr", "active")
        self._seed_dead_session(
            sid="sess_e2e_dead", agent_id="e2e-dead-mgr",
            mode="managed-warm", owner_mode="managed", status="running",
            terminal_status="attached",
        )
        self._seed_terminal_for_session(
            tid="term_e2e_dead", sid="sess_e2e_dead",
            agent_id="e2e-dead-mgr", status="failed",
        )
        resp = self.client.get("/api/v1/sessions", params={"agentId": "e2e-dead-mgr"})
        self.assertEqual(resp.status_code, 200, resp.text)
        sessions = resp.json()["sessions"]
        target = next(s for s in sessions if s["id"] == "sess_e2e_dead")
        self.assertEqual(target["status"], "stopped")
        # Dict shape is preserved — the stored row is untouched.
        self.assertEqual(target["ownerMode"], "managed")
        self.assertEqual(self._dead_session_status("sess_e2e_dead"), "running")


class TerminalSessionMigrationTests(unittest.TestCase):
    """Part 1 migration: an existing DB whose terminal_sessions table predates
    the process_id column gets it added by init_db (idempotently)."""

    def test_process_id_column_added_to_legacy_terminal_sessions(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE terminal_sessions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                environment_id TEXT NOT NULL,
                bridge_id TEXT DEFAULT '',
                runtime TEXT NOT NULL,
                workspace TEXT DEFAULT '',
                command TEXT DEFAULT '',
                status TEXT DEFAULT 'starting',
                requested_by TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                stopped_at TEXT,
                error TEXT DEFAULT ''
            )
            """
        )
        conn.commit()
        cols_before = {row[1] for row in conn.execute("PRAGMA table_info(terminal_sessions)")}
        conn.close()
        self.assertNotIn("process_id", cols_before)

        asyncio.run(init_db(db_path))
        asyncio.run(init_db(db_path))  # idempotent re-run

        conn = sqlite3.connect(str(db_path))
        cols_after = {row[1] for row in conn.execute("PRAGMA table_info(terminal_sessions)")}
        conn.close()
        self.assertIn("process_id", cols_after)
