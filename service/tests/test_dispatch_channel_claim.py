"""Plan 5 (2026-05-25) — symmetric channel-claim for wrapper-backed managed
codex/hermes/pi dispatches.

Pins the server-side claim contract: when a bridge polls /dispatch/claim with
executionModes=['channel'] for a managed codex/hermes/pi agent, the queued
execution_mode='channel' run (set by api_v2.py:1047 for wrapper-backed runtimes)
must be returned. Pre-Plan-5, _CHANNEL_MANAGED_RUNTIMES contained only
'claude-code', so codex/hermes/pi bridges were rejected and runs sat queued
forever (observed 2026-05-25 — graph-senior-dev: dispatch_runs row showed
execution_mode='channel' status='queued' claim_bridge_id='' even with a live
bridge polling).
"""

import asyncio
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
    """Plan 5 Task B2 — _CHANNEL_MANAGED_RUNTIMES widened to include
    codex/hermes/pi so their wrapper-backed managed dispatches can be claimed
    by the main bridge (mirrors Plan 5 Task B1 on the bridge side)."""

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

        # Plan 4 defaults are on (managed_via_wrapper=[codex,hermes,pi]).
        # Confirm via a no-op PUT so the test is explicit about expectation.
        self.client.put(
            "/api/v1/settings",
            json={"managed_via_wrapper": ["codex", "hermes", "pi"]},
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

    def _assert_channel_claim_succeeds(self, *, agent_id: str, run_id: str, runtime: str) -> None:
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
        self.assertIsNotNone(
            body.get("run"),
            f"Plan 5: expected channel-claim to return queued run for {runtime}; got: {body}",
        )
        self.assertEqual(body["run"]["id"], run_id)
        self.assertEqual(
            body["run"].get("executionMode") or body["run"].get("execution_mode"),
            "channel",
            f"claimed run for {runtime} should be channel-mode; got: {body['run']}",
        )

    def test_codex_managed_wrapper_backed_claims_channel(self):
        self._heartbeat_environment("codex")
        self._register_managed_agent(agent_id="codex-managed", runtime="codex")
        run_id = self._dispatch_to("codex-managed")
        self._assert_channel_claim_succeeds(
            agent_id="codex-managed", run_id=run_id, runtime="codex"
        )

    def test_hermes_managed_wrapper_backed_claims_channel(self):
        self._heartbeat_environment("hermes")
        self._register_managed_agent(agent_id="hermes-managed", runtime="hermes")
        run_id = self._dispatch_to("hermes-managed")
        self._assert_channel_claim_succeeds(
            agent_id="hermes-managed", run_id=run_id, runtime="hermes"
        )

    def test_pi_managed_wrapper_backed_claims_channel(self):
        self._heartbeat_environment("pi")
        self._register_managed_agent(agent_id="pi-managed", runtime="pi")
        run_id = self._dispatch_to("pi-managed")
        self._assert_channel_claim_succeeds(
            agent_id="pi-managed", run_id=run_id, runtime="pi"
        )


if __name__ == "__main__":
    unittest.main()
