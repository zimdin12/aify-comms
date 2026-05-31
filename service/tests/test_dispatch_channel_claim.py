"""Wrapper-backed managed channel-claim contract.

Codex/Hermes managed-wrapper dispatches are persisted as execution_mode='channel',
but the environment bridge must not claim them. The claimant must be the
*-aify wrapper PTY's child bridge, registered as bridge_kind='managed-wrapper-child',
because only that child has the live app-server/gateway for the visible console.
"""

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import init_db
from service.routers.api_v2 import router


class _DummyWS:
    async def broadcast(self, *_args, **_kwargs):
        return None

    async def notify_agent(self, *_args, **_kwargs):
        return None


class ChannelClaimWrapperBackedTests(unittest.TestCase):
    """Wrapper-backed Codex/Hermes channel claims are reserved for child bridges."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test.db"
        asyncio.run(init_db(self._db_path))

        app = FastAPI()
        app.state.ws_manager = _DummyWS()
        app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        app.state.testing = True
        app.include_router(router, prefix="/api/v1")
        self.client = TestClient(app)

        # Plan 4 defaults are on (managed_via_wrapper=[codex,hermes]).
        # Confirm via a no-op PUT so the test is explicit about expectation.
        self.client.put(
            "/api/v1/settings",
            json={"managed_via_wrapper": ["codex", "hermes"]},
        )

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def _heartbeat_environment(self, runtime: str) -> None:
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
                    "runtime": runtime,
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                }
            ],
            "metadata": {},
        }
        response = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(response.status_code, 200, response.text)

    def _register_managed_agent(self, *, agent_id: str, runtime: str) -> None:
        # Native-managed capability + managed session_mode + a plausible
        # runtimeConfig the controller can dispatch into. The exact config
        # shape doesn't matter for the claim gate — only that the runtime
        # makes it past _check_capabilities_for_managed_dispatch.
        runtime_config = {}
        if runtime == "codex":
            runtime_config["appServerUrl"] = "ws://127.0.0.1:1234"
        elif runtime == "hermes":
            runtime_config["gatewayUrl"] = "ws://127.0.0.1:9119/api/ws?token=t"
        # pi: no special runtime_config required for the claim gate.
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": runtime,
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["native-managed-run", "managed-run", "resume", "interrupt"],
                "runtimeConfig": runtime_config,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        conn = sqlite3.connect(str(self._db_path))
        try:
            session_id = f"session-{agent_id}"
            terminal_id = f"term-{agent_id}"
            conn.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                    terminal_id, terminal_status, status, started_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    agent_id,
                    "linux:test-host:default",
                    runtime,
                    "/workspace",
                    "managed-warm",
                    "managed",
                    terminal_id,
                    "running",
                    "running",
                    "2099-01-01T00:00:00Z",
                    "2099-01-01T00:00:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO terminal_sessions (
                    id, session_id, agent_id, environment_id, bridge_id, runtime,
                    workspace, command, status, requested_by, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    terminal_id,
                    session_id,
                    agent_id,
                    "linux:test-host:default",
                    "bridge-current",
                    runtime,
                    "/workspace",
                    f"{runtime}-aify --aify-agent {agent_id}",
                    "running",
                    "dashboard",
                    "2099-01-01T00:00:00Z",
                    "2099-01-01T00:00:00Z",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _dispatch_to(self, agent_id: str) -> str:
        # Use /messages/send because it passes `settings` into
        # _agent_execution_mode (api_v2.py:3642), which is the helper that
        # detects wrapper-backed managed runtimes and returns
        # execution_mode='channel' (api_v2.py:1047). This mirrors how the
        # operator's actual comms_send call lands at the server.
        response = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "to": agent_id,
                "type": "request",
                "subject": "channel-claim",
                "body": "hello via channel",
                "trigger": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        runs = body.get("dispatchRuns") or []
        self.assertTrue(runs, f"expected a dispatch run for {agent_id}; got: {body}")
        return runs[0]["runId"]

    def _register_wrapper_child_bridge(self, *, agent_id: str, runtime: str, bridge_id: str, terminal_id: str | None = None) -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": runtime,
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": bridge_id,
                "capabilities": ["native-managed-run", "managed-run", "resume", "interrupt"],
                "terminalId": terminal_id or f"term-{agent_id}",
                "managedWrapperChild": True,
                "autoRegister": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _assert_environment_claim_blocked(self, *, agent_id: str, runtime: str) -> None:
        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": agent_id,
                "bridgeId": "bridge-current",
                "machineId": "linux:test-host",
                "executionModes": ["channel"],
            },
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        body = claim.json()
        self.assertIsNone(body.get("run"), f"environment bridge must not claim wrapper-backed {runtime}; got: {body}")
        self.assertEqual((body.get("blockedBy") or {}).get("reason"), "managed_wrapper_child_required")

    def _assert_wrapper_child_channel_claim_succeeds(self, *, agent_id: str, run_id: str, runtime: str) -> None:
        bridge_id = f"{agent_id}-wrapper-child"
        self._register_wrapper_child_bridge(agent_id=agent_id, runtime=runtime, bridge_id=bridge_id)
        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": agent_id,
                "bridgeId": bridge_id,
                "machineId": "linux:test-host",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        body = claim.json()
        self.assertIsNotNone(
            body.get("run"),
            f"expected wrapper-child channel claim to return queued run for {runtime}; got: {body}",
        )
        self.assertEqual(body["run"]["id"], run_id)
        self.assertEqual(
            body["run"].get("executionMode") or body["run"].get("execution_mode"),
            "channel",
            f"claimed run for {runtime} should be channel-mode; got: {body['run']}",
        )

    def _assert_wrapper_child_channel_claim_blocked(self, *, agent_id: str, runtime: str, bridge_id: str, reason: str) -> None:
        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": agent_id,
                "bridgeId": bridge_id,
                "machineId": "linux:test-host",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        body = claim.json()
        self.assertIsNone(body.get("run"), f"stale wrapper-child must not claim {runtime}; got: {body}")
        self.assertEqual((body.get("blockedBy") or {}).get("reason"), reason, body)

    def test_codex_managed_wrapper_backed_claims_channel(self):
        self._heartbeat_environment("codex")
        self._register_managed_agent(agent_id="codex-managed", runtime="codex")
        run_id = self._dispatch_to("codex-managed")
        self._assert_environment_claim_blocked(agent_id="codex-managed", runtime="codex")
        self._assert_wrapper_child_channel_claim_succeeds(
            agent_id="codex-managed", run_id=run_id, runtime="codex"
        )

    def test_hermes_managed_wrapper_backed_claims_channel(self):
        self._heartbeat_environment("hermes")
        self._register_managed_agent(agent_id="hermes-managed", runtime="hermes")
        run_id = self._dispatch_to("hermes-managed")
        self._assert_environment_claim_blocked(agent_id="hermes-managed", runtime="hermes")
        self._assert_wrapper_child_channel_claim_succeeds(
            agent_id="hermes-managed", run_id=run_id, runtime="hermes"
        )

    def test_hermes_wrapper_child_cannot_claim_while_console_is_still_resuming(self):
        self._heartbeat_environment("hermes")
        self._register_managed_agent(agent_id="hermes-resuming", runtime="hermes")
        self._dispatch_to("hermes-resuming")
        self._register_wrapper_child_bridge(
            agent_id="hermes-resuming",
            runtime="hermes",
            bridge_id="hermes-resuming-wrapper-child",
        )
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """
                UPDATE terminal_sessions
                SET output = ?
                WHERE id = ?
                """,
                ("Hermes Agent\nresuming... | gpt-5.5 | voice off", "term-hermes-resuming"),
            )
            conn.commit()
        finally:
            conn.close()

        self._assert_wrapper_child_channel_claim_blocked(
            agent_id="hermes-resuming",
            runtime="hermes",
            bridge_id="hermes-resuming-wrapper-child",
            reason="managed_wrapper_terminal_not_ready",
        )

    def test_wrapper_backed_send_without_managed_session_coldstarts_backing(self):
        # Phase 2 (2026-05-31): a wrapper-backed managed codex agent with no
        # live managed session is now LAZY-AUTOSTARTED on send when an ONLINE
        # env advertises codex — the send cold-starts a spawn_request that a
        # bridge claims, instead of hard-rejecting. The operator explicitly
        # wants `available` agents to auto-start on first message. The
        # no-ORPHAN invariant still holds: any queued dispatch_run must be
        # backed by a claimable spawn_request (a bridge will spawn the wrapper
        # and claim it), never left to sit forever with nothing to claim it.
        self._heartbeat_environment("codex")
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "codex-no-backing",
                "role": "coder",
                "runtime": "codex",
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["native-managed-run", "managed-run", "resume", "interrupt"],
                "runtimeConfig": {"appServerUrl": "ws://127.0.0.1:1234"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        sent = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "to": "codex-no-backing",
                "type": "request",
                "subject": "auto-start",
                "body": "this should cold-start a backing spawn request",
                "trigger": True,
            },
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        body = sent.json()
        # Not hard-rejected — the available agent is auto-started.
        self.assertNotEqual(
            body.get("error"),
            "Message was not sent because one or more recipients cannot start live work now.",
            body,
        )
        conn = sqlite3.connect(str(self._db_path))
        try:
            queued_runs = conn.execute(
                "SELECT COUNT(*) FROM dispatch_runs WHERE target_agent = ? AND status = 'queued'",
                ("codex-no-backing",),
            ).fetchone()[0]
            spawn_reqs = conn.execute(
                "SELECT COUNT(*) FROM spawn_requests WHERE agent_id = ? AND status IN ('queued','claimed')",
                ("codex-no-backing",),
            ).fetchone()[0]
        finally:
            conn.close()
        # Cold-start created exactly one claimable spawn_request as the backing.
        self.assertEqual(spawn_reqs, 1, body)
        # No-orphan invariant: a queued run must be backed by a spawn_request.
        if queued_runs:
            self.assertGreaterEqual(
                spawn_reqs, 1, "queued run must be backed by a spawn_request, not orphaned"
            )

    def test_standalone_hermes_channel_sidecar_claims_channel(self):
        """Task 1.5b: the NEW standalone per-agent hermes sidecar
        (mcp/stdio/hermes-channel.js) is NOT a managed-wrapper-child and owns
        no PTY terminal — it drives the agent's pinned api_server daemon
        session directly. It declares bridgeKind='channel-sidecar' on its claim
        and must be accepted on the SAME basis claude's standalone channel
        sidecar is (claude bypasses the wrapper-child requirement by runtime;
        hermes bypasses it by declaring channel-sidecar). Before the fix the
        claim was rejected with managed_wrapper_child_required and delivery
        silently never happened."""
        self._heartbeat_environment("hermes")
        self._register_managed_agent(agent_id="hermes-sidecar", runtime="hermes")
        run_id = self._dispatch_to("hermes-sidecar")
        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "hermes-sidecar",
                # Standalone sidecar bridge id (mirror of claude's
                # channel-<machine>), never registered as a wrapper child and
                # with no terminal binding.
                "bridgeId": "hermes-channel-linux:test-host",
                "machineId": "linux:test-host",
                "bridgeKind": "channel-sidecar",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        body = claim.json()
        self.assertIsNotNone(
            body.get("run"),
            f"standalone hermes channel sidecar must claim its queued channel run; got: {body}",
        )
        self.assertEqual(body["run"]["id"], run_id)
        self.assertEqual(
            body["run"].get("executionMode") or body["run"].get("execution_mode"),
            "channel",
        )

    def test_standalone_claude_channel_sidecar_claims_channel(self):
        """Regression: claude's standalone channel sidecar claim is unchanged —
        it is accepted whether or not it declares bridgeKind (claude bypasses
        the wrapper-child gate by runtime, not by bridgeKind)."""
        self._heartbeat_environment("claude-code")
        # claude managed routes to channel unconditionally (channelEnabled).
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "claude-sidecar",
                "role": "coder",
                "runtime": "claude-code",
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["resume", "interrupt"],
                "runtimeConfig": {"channelEnabled": True},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        run_id = self._dispatch_to("claude-sidecar")
        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "claude-sidecar",
                "bridgeId": "channel-linux:test-host",
                "machineId": "linux:test-host",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        body = claim.json()
        self.assertIsNotNone(body.get("run"), f"claude channel claim must still work; got: {body}")
        self.assertEqual(body["run"]["id"], run_id)

    def test_superseded_channel_sidecar_self_heals_on_claim(self):
        """Regression (operator-reported 2026-05-31, sc-claude): a managed
        claude/hermes channel sidecar and the visible TUI's managed-wrapper-child
        bridge legitimately COEXIST (complementary pair). During managed-PTY
        churn the sidecar's bridge row briefly goes stale and the wrapper-child
        registration superseded it (the 5-min-stale clause overrode the
        complementary-pair carve-out). Once superseded, _bridge_claim_block_reason
        permanently BLOCKED the still-live sidecar's claims (the block precedes
        the heartbeat upsert, so it could never un-supersede itself) -> queued
        channel runs were never delivered and the agent sat idle forever.

        A live channel-sidecar poll is proof of life: it must self-heal
        (un-supersede its own row) and resume claiming."""
        self._heartbeat_environment("claude-code")
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "claude-heal",
                "role": "coder",
                "runtime": "claude-code",
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["resume", "interrupt"],
                "runtimeConfig": {"channelEnabled": True},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        sidecar_bridge = "channel-linux:test-host"
        # First claim creates the channel-sidecar bridge row.
        run_id1 = self._dispatch_to("claude-heal")
        claim1 = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "claude-heal",
                "bridgeId": sidecar_bridge,
                "machineId": "linux:test-host",
                "bridgeKind": "channel-sidecar",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(claim1.status_code, 200, claim1.text)
        self.assertEqual(claim1.json()["run"]["id"], run_id1)
        # Simulate the wrapper-child registration superseding the sidecar row.
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "UPDATE bridge_instances SET superseded_by = 'some-wrapper-child' "
                "WHERE id = ? AND agent_id = ?",
                (sidecar_bridge, "claude-heal"),
            )
            conn.commit()
        finally:
            conn.close()
        # A new run is queued; the still-live sidecar must self-heal and claim it.
        run_id2 = self._dispatch_to("claude-heal")
        claim2 = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "claude-heal",
                "bridgeId": sidecar_bridge,
                "machineId": "linux:test-host",
                "bridgeKind": "channel-sidecar",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(claim2.status_code, 200, claim2.text)
        body = claim2.json()
        self.assertIsNone(
            body.get("blockedBy"),
            f"a live channel sidecar must self-heal a superseded row, not stay blocked; got: {body}",
        )
        self.assertIsNotNone(body.get("run"), f"self-healed sidecar must claim the queued run; got: {body}")
        self.assertEqual(body["run"]["id"], run_id2)
        # Row is un-superseded again.
        conn = sqlite3.connect(str(self._db_path))
        try:
            row = conn.execute(
                "SELECT superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
                (sidecar_bridge, "claude-heal"),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual((row[0] or ""), "", "channel sidecar row should be un-superseded after a live claim")

    def test_old_wrapper_child_for_different_terminal_cannot_claim_channel_run(self):
        self._heartbeat_environment("hermes")
        self._register_managed_agent(agent_id="hermes-multi-wrapper", runtime="hermes")
        run_id = self._dispatch_to("hermes-multi-wrapper")
        self._register_wrapper_child_bridge(
            agent_id="hermes-multi-wrapper",
            runtime="hermes",
            bridge_id="old-wrapper-child",
            terminal_id="term-old-hidden",
        )
        self._assert_wrapper_child_channel_claim_blocked(
            agent_id="hermes-multi-wrapper",
            runtime="hermes",
            bridge_id="old-wrapper-child",
            reason="managed_wrapper_terminal_mismatch",
        )
        self._assert_wrapper_child_channel_claim_succeeds(
            agent_id="hermes-multi-wrapper",
            run_id=run_id,
            runtime="hermes",
        )

if __name__ == "__main__":
    unittest.main()
