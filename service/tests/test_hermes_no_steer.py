"""Hermes advertises native non-interrupting ``session.steer`` support."""
import json
import unittest

from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now
from service.runtimes import adapter_for
from service.api_core import capabilities  # v0.5.4: call the OWNER


def _row(*, runtime, session_mode, capabilities, runtime_config=None):
    return {
        "capabilities": json.dumps(capabilities),
        "runtime": runtime,
        "session_mode": session_mode,
        "session_handle": "h",
        "runtime_config": json.dumps(runtime_config or {}),
        "id": "agent-x",
    }


class HermesSteerTests(unittest.TestCase):
    def test_runtime_adapter_declares_managed_hermes_steerable(self):
        self.assertTrue(adapter_for("hermes").supports_steering)

    def test_managed_hermes_keeps_steer(self):
        caps = capabilities._row_capabilities(_row(
            runtime="hermes", session_mode="managed",
            capabilities=["managed-run", "resume", "interrupt", "spawn"],
            runtime_config={"channelEnabled": True},
        ))
        for cap in ("managed-run", "resume", "interrupt", "steer", "spawn"):
            self.assertIn(cap, caps)

    def test_managed_acp_fallback_does_not_advertise_steer(self):
        caps = capabilities._row_capabilities(_row(
            runtime="hermes", session_mode="managed",
            capabilities=["managed-run", "resume", "interrupt", "steer", "spawn"],
            runtime_config={},
        ))
        self.assertNotIn("steer", caps)

    def test_resident_hermes_keeps_steer_with_live_gateway(self):
        caps = capabilities._row_capabilities(_row(
            runtime="hermes", session_mode="resident",
            capabilities=["resident-run", "resume", "interrupt"],
            runtime_config={"gatewayUrl": "ws://127.0.0.1:9000/api/ws"},
        ))
        for cap in ("resident-run", "resume", "interrupt", "steer"):
            self.assertIn(cap, caps)

    def test_managed_claude_keeps_steer(self):
        caps = capabilities._row_capabilities(_row(
            runtime="claude-code", session_mode="managed",
            capabilities=["managed-run", "resume", "interrupt", "steer", "spawn"],
        ))
        self.assertIn("steer", caps, "managed claude must keep steer (it queues injects safely)")

if __name__ == "__main__":
    unittest.main()
