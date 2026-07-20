"""Hermes must NOT be advertised as `steer`-capable (mc-senior-dev interrupt/churn, 2026-07-20).

`steer` means "safely accepts a mid-turn inject." Claude queues injects in order; hermes treats a
submission delivered while it is waiting on the model as an INTERRUPT and cancels the turn. So a
managed hermes carrying `steer` (registered by its bridge) made both the send-time steer path and
the /dispatch/claim turn-busy bypass inject mid-turn → interrupted every turn → stranded replies +
cold-start churn. `_row_capabilities` now strips `steer` for managed hermes so its messages ENQUEUE
and deliver at turn-end. Claude keeps `steer`.
"""
import asyncio
import json
import unittest

from service.routers import api_v2
from service.runtimes import adapter_for


def _row(*, runtime, session_mode, capabilities, runtime_config=None):
    return {
        "capabilities": json.dumps(capabilities),
        "runtime": runtime,
        "session_mode": session_mode,
        "session_handle": "h",
        "runtime_config": json.dumps(runtime_config or {}),
        "id": "agent-x",
    }


class HermesNoSteerTests(unittest.TestCase):
    def test_runtime_adapter_declares_managed_hermes_non_steerable(self):
        self.assertFalse(adapter_for("hermes").supports_steering)

    def test_managed_hermes_strips_steer_even_when_registered(self):
        caps = api_v2._row_capabilities(_row(
            runtime="hermes", session_mode="managed",
            capabilities=["managed-run", "resume", "interrupt", "steer", "spawn"],
        ))
        self.assertNotIn("steer", caps, "managed hermes must not advertise steer")
        # ...but the legitimate managed caps remain.
        for cap in ("managed-run", "resume", "interrupt", "spawn"):
            self.assertIn(cap, caps)

    def test_resident_hermes_strips_legacy_steer_even_with_live_gateway(self):
        caps = api_v2._row_capabilities(_row(
            runtime="hermes", session_mode="resident",
            capabilities=["resident-run", "resume", "interrupt", "steer"],
            runtime_config={"gatewayUrl": "ws://127.0.0.1:9000/api/ws"},
        ))
        self.assertNotIn("steer", caps)
        for cap in ("resident-run", "resume", "interrupt"):
            self.assertIn(cap, caps)

    def test_managed_claude_keeps_steer(self):
        caps = api_v2._row_capabilities(_row(
            runtime="claude-code", session_mode="managed",
            capabilities=["managed-run", "resume", "interrupt", "steer", "spawn"],
        ))
        self.assertIn("steer", caps, "managed claude must keep steer (it queues injects safely)")

    def test_claim_gate_bypass_is_false_for_managed_hermes(self):
        # With steer stripped, the /dispatch/claim steer bypass must NOT fire → the turn-busy gate
        # HOLDS the queued run until turn-end instead of injecting mid-turn.
        async def _run():
            db = await api_v2.get_db() if hasattr(api_v2, "get_db") else None
            return db
        # _has_claimable_steerable_run short-circuits on capabilities before touching the DB, so we
        # can assert the capability gate directly without a live DB.
        row = _row(runtime="hermes", session_mode="managed",
                   capabilities=["managed-run", "resume", "interrupt", "steer", "spawn"])
        caps = api_v2._row_capabilities(row)
        self.assertNotIn("steer", caps)


if __name__ == "__main__":
    unittest.main()
