"""fix/resident-hermes-status (2026-06-02) — a resident hermes that cannot
actually be woken must NOT compute `available`.

Operator-reported inconsistency (live agent ci-senior-dev, a resident hermes
registered from a REMOTE machine): the dashboard showed status `available` but a
RED status dot, wake path "Hermes missing handle", because the status label and
the dot are driven by DIFFERENT fields.

Confirmed root cause (server side): `_compute_live_status_cache` derived
`available` for a resident agent whose wake-mode is a `*-missing-handle` mode —
i.e. no usable wake handle (resident hermes with no usable `gatewayUrl`). The
resident-bridge-stale gate at the top of the compute is itself gated on
`"resident-run" in _row_capabilities(...)`, and `_row_capabilities` STRIPS
`resident-run` for a resident hermes with no gatewayUrl — so the stale gate
never even ran, and the agent fell through to `available`. Meanwhile the
dashboard dot derives `unreachable`/red from the non-live-wake wake-mode. Label
and dot disagree.

Fix: a resident agent in a `*-missing-handle` wake-mode (no usable wake handle)
must compute `stale`, never `available`/`online`. `stale` is what the dashboard
dot already renders for an unwakeable resident, so the label and the dot agree
(single source of truth = the live-state engine). A genuinely-live resident
hermes (fresh bridge + usable gatewayUrl → wake-mode `hermes-live`) is
unaffected and still computes `available`/`online`.
"""

import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.tests._base import FastApiTestCase


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ResidentHermesMissingHandleStatusTests(FastApiTestCase):
    MACHINE = "linux:laputa"
    GATEWAY_URL = "ws://127.0.0.1:44403/api/ws?token=secrettoken"

    # ---- helpers ----
    def _register_resident_hermes(self, agent_id: str, *, runtime_config: dict) -> None:
        res = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": "hermes",
                "sessionMode": "resident",
                "machineId": self.MACHINE,
                "bridgeId": f"bridge-{agent_id}",
                "capabilities": ["resident-run", "resume", "interrupt", "steer"],
                "sessionHandle": "20260529_071302_ea65af",
                "runtimeConfig": runtime_config,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)

    def _agent(self, agent_id: str) -> dict:
        res = self.client.get(f"/api/v1/agents/{agent_id}")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["agent"]

    def _age_resident_bridge(self, agent_id: str, *, minutes: int) -> None:
        stale = _iso(datetime.now(timezone.utc) - timedelta(minutes=minutes))
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "UPDATE bridge_instances SET last_seen = ? WHERE agent_id = ?",
                (stale, agent_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- the bug ----
    def test_resident_hermes_missing_handle_is_not_available(self):
        # The operator's exact case: resident hermes, FRESH bridge (just
        # registered), but NO usable gatewayUrl → wake-mode hermes-missing-handle.
        # It must NOT read `available` (which would disagree with the red dot).
        self._register_resident_hermes("hermes-nohandle", runtime_config={})
        info = self._agent("hermes-nohandle")
        self.assertEqual(
            info["wakeMode"], "hermes-missing-handle",
            f"precondition: no gatewayUrl → missing-handle wake-mode; got {info['wakeMode']!r}",
        )
        self.assertNotIn(
            info["status"], {"available", "online", "ready", "idle"},
            f"a resident hermes that cannot be woken (missing handle) must not read available/online; got {info['status']!r}",
        )

    def test_resident_hermes_missing_handle_is_offline(self):
        # Proof-based (2026-06-18): an unwakeable resident (no live bridge / no handle) is
        # OFFLINE — 'stale' was a time-decay artifact and is gone.
        self._register_resident_hermes("hermes-nohandle2", runtime_config={})
        info = self._agent("hermes-nohandle2")
        self.assertEqual(
            info["status"], "offline",
            f"missing-handle resident hermes should compute offline; got {info['status']!r}",
        )

    def test_dot_and_label_status_agree_for_missing_handle(self):
        # Single-source-of-truth check: the dashboard dot derives from statusRaw
        # for the stale/offline/stopped family (it reads the SAME engine status as
        # the label). With the fix the engine status is `stale`, which the dot
        # renders as the muted/offline dot — consistent, no available+red split.
        self._register_resident_hermes("hermes-nohandle3", runtime_config={})
        info = self._agent("hermes-nohandle3")
        raw = str(info.get("statusRaw") or info.get("status") or "").lower()
        self.assertIn(
            raw, {"stale", "offline", "stopped"},
            f"dot-driving statusRaw must be in the unreachable family so it agrees with the label; got {raw!r}",
        )

    # ---- regression: a genuinely-live resident hermes is unchanged ----
    def test_resident_hermes_with_gateway_url_is_live_bound(self):
        # Fresh bridge + usable gatewayUrl → wake-mode hermes-live. This is a
        # wakeable resident and must NOT be downgraded to stale by the fix.
        self._register_resident_hermes(
            "hermes-live", runtime_config={"gatewayUrl": self.GATEWAY_URL}
        )
        info = self._agent("hermes-live")
        self.assertEqual(
            info["wakeMode"], "hermes-live",
            f"precondition: gatewayUrl present → hermes-live; got {info['wakeMode']!r}",
        )
        self.assertNotEqual(
            info["status"], "stale",
            "a fresh-bridge resident hermes WITH a usable gatewayUrl must not be marked stale",
        )
        self.assertIn(
            info["status"], {"available", "online", "ready", "idle"},
            f"a live-bound resident hermes should be available/online; got {info['status']!r}",
        )

    def test_resident_hermes_with_gateway_url_but_silent_bridge_is_offline(self):
        # Proof-based (2026-06-18): a live-bound resident hermes whose bridge heartbeat has
        # gone silent is OFFLINE (the heartbeat going away is the proof it's gone; no 'stale').
        self._register_resident_hermes(
            "hermes-livestale", runtime_config={"gatewayUrl": self.GATEWAY_URL}
        )
        self._age_resident_bridge("hermes-livestale", minutes=20)
        info = self._agent("hermes-livestale")
        self.assertEqual(
            info["status"], "offline",
            f"a live-bound resident hermes with a silent bridge must be offline; got {info['status']!r}",
        )


if __name__ == "__main__":
    unittest.main()
