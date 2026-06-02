"""Task 4.1 (2026-05-30) — managed/resident driver FSM + mutual-exclusion guard.

Design: docs/superpowers/specs/2026-05-30-runtime-symmetry-and-session-governance-design.md
("FSM — mode + driver", "Mutual exclusion (the collision guard)").

Hard invariant: AT MOST ONE driver per session at a time. A second driver
attaching in the OTHER mode is REJECTED with an actionable error containing the
resume command. Same-mode supersession (a managed restart) stays allowed.

Cases:
(a) managed -> resident switch marks the agent resident, signals RELEASE (the
    managed sidecar reads it and stops claiming), and the switch response
    carries the takeover `resumeCommand`.
(b) a cross-mode attach to a session currently `driving` is REJECTED with the
    actionable error (contains the resume command).
(c) same-mode supersession (managed restart) is still allowed.
(d) resident -> managed flips back.
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

from service.tests._base import FastApiTestCase


class SessionModeFsmTests(FastApiTestCase):
    pass

    # ── helpers ──────────────────────────────────────────────────────────────

    def _heartbeat_environment(self, runtime: str) -> None:
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
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
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _register(self, *, agent_id, runtime, session_mode, bridge_id="bridge-1",
                  session_handle="sess-abc", runtime_config=None, machine_id="linux:test-host"):
        rc = dict(runtime_config or {})
        if runtime == "hermes" and "gatewayUrl" not in rc:
            rc["gatewayUrl"] = "ws://127.0.0.1:9999/api/ws?token=test"
        return self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": runtime,
                "sessionMode": session_mode,
                "sessionHandle": session_handle,
                "machineId": machine_id,
                "bridgeId": bridge_id,
                "launchMode": "managed" if session_mode == "managed" else "detached",
                "capabilities": ["managed-run", "resident-run", "resume", "interrupt"],
                "runtimeConfig": rc,
            },
        )

    def _read_agent(self, agent_id):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        finally:
            conn.close()

    def _set_driving(self, agent_id):
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("UPDATE agents SET driver_state = 'driving' WHERE id = ?", (agent_id,))
            conn.commit()
        finally:
            conn.close()

    def _register_hermes_no_gateway(self, *, agent_id, session_mode="managed", bridge_id="bridge-1"):
        """Register a hermes agent with an EXPLICITLY empty runtimeConfig (no
        gatewayUrl) — mirrors the api_server model where hermes-aify no longer
        exports AIFY_HERMES_GATEWAY_URL."""
        return self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": "hermes",
                "sessionMode": session_mode,
                "sessionHandle": f"aify-{agent_id}",
                "machineId": "linux:test-host",
                "bridgeId": bridge_id,
                "launchMode": "managed" if session_mode == "managed" else "detached",
                "capabilities": ["managed-run", "resident-run", "resume", "interrupt"],
                "runtimeConfig": {},
            },
        )

    def _insert_active_run(self, agent_id, run_id="run-active"):
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """
                INSERT INTO dispatch_runs (
                    id, from_agent, target_agent, message_type, subject, body,
                    status, requested_at, claimed_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (run_id, "lead", agent_id, "request", "work", "body",
                 "running", "2026-05-30T00:00:00Z", "2026-05-30T00:00:00Z"),
            )
            conn.commit()
        finally:
            conn.close()

    # ── (a) managed -> resident: marks resident, releases, surfaces resume ────

    def test_managed_to_resident_marks_release_and_surfaces_resume_command(self):
        self._heartbeat_environment("claude-code")
        self._register(agent_id="claude-1", runtime="claude-code", session_mode="managed")
        self._set_driving("claude-1")
        res = self.client.patch(
            "/api/v1/agents/claude-1/session-mode",
            json={"mode": "resident", "force": True},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body.get("mode"), "resident")
        self.assertTrue(body.get("changed"))
        # Switch response carries the takeover resume command.
        self.assertIn("resumeCommand", body)
        self.assertIn("--resume", body["resumeCommand"])
        # Prior managed driver is released (idle) so the sidecar stops.
        self.assertEqual(self._read_agent("claude-1")["driver_state"], "idle")

    # ── release signal in the managed sidecar's claim response ────────────────

    def test_managed_claim_after_switch_to_resident_returns_release(self):
        self._heartbeat_environment("claude-code")
        self._register(agent_id="claude-rel", runtime="claude-code", session_mode="managed")
        # Operator flips it to resident.
        self.client.patch(
            "/api/v1/agents/claude-rel/session-mode",
            json={"mode": "resident", "force": True},
        )
        # The managed sidecar's next claim must be told to release.
        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "claude-rel",
                "machineId": "linux:test-host",
                "bridgeId": "sidecar-1",
                "bridgeKind": "channel-sidecar",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        cb = claim.json()
        self.assertTrue(cb.get("release"), f"expected release signal, got {cb}")
        self.assertIsNone(cb.get("run"))

    def test_managed_heartbeat_after_switch_to_resident_returns_release(self):
        self._heartbeat_environment("hermes")
        self._register(agent_id="hermes-rel", runtime="hermes", session_mode="managed")
        self.client.patch(
            "/api/v1/agents/hermes-rel/session-mode",
            json={"mode": "resident", "force": True},
        )
        hb = self.client.post(
            "/api/v1/agents/hermes-rel/heartbeat",
            json={"bridgeId": "sidecar-1", "turnBusy": False, "turnRuntime": "hermes",
                  "bridgeKind": "channel-sidecar"},
        )
        self.assertEqual(hb.status_code, 200, hb.text)
        self.assertTrue(hb.json().get("release"), f"expected release, got {hb.json()}")

    # ── release MUST NOT fire for a natively-resident agent's own delivery ─────
    # Regression (operator-reported 2026-05-31, sc-manager): a NATIVELY resident
    # claude/hermes agent's channel sidecar (claude-channel.js / hermes-channel.js)
    # IS its sole delivery path and claims with bridgeKind="channel-sidecar". The
    # Task-4.1 release fired on the blunt `session_mode != managed` condition, so
    # every resident agent's delivery sidecar was told to release -> queued runs
    # never claimed, delivery silently stalled. The release must only fire for a
    # DISPLACED managed driver (driver_state != 'driving'), never for the live
    # resident driver (driver_state == 'driving').

    def test_native_resident_channel_sidecar_claim_does_not_release(self):
        self._heartbeat_environment("claude-code")
        # Registered resident WITH a bridge -> driver_state='driving' (live driver).
        reg = self._register(agent_id="claude-native", runtime="claude-code",
                             session_mode="resident", bridge_id="resident-sidecar")
        self.assertEqual(reg.status_code, 200, reg.text)
        self.assertEqual(self._read_agent("claude-native")["driver_state"], "driving")
        claim = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "claude-native",
                "machineId": "linux:test-host",
                "bridgeId": "channel-linux:test-host",
                "bridgeKind": "channel-sidecar",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertFalse(
            claim.json().get("release"),
            f"a driving resident's delivery sidecar must NOT be released, got {claim.json()}",
        )

    def test_native_resident_channel_sidecar_heartbeat_does_not_release(self):
        self._heartbeat_environment("claude-code")
        reg = self._register(agent_id="claude-native-hb", runtime="claude-code",
                             session_mode="resident", bridge_id="resident-sidecar-hb")
        self.assertEqual(reg.status_code, 200, reg.text)
        self.assertEqual(self._read_agent("claude-native-hb")["driver_state"], "driving")
        hb = self.client.post(
            "/api/v1/agents/claude-native-hb/heartbeat",
            json={"bridgeId": "channel-linux:test-host", "turnBusy": False,
                  "turnRuntime": "claude-code", "bridgeKind": "channel-sidecar"},
        )
        self.assertEqual(hb.status_code, 200, hb.text)
        self.assertFalse(
            hb.json().get("release"),
            f"a driving resident's delivery sidecar must NOT be released, got {hb.json()}",
        )

    # ── (b) cross-mode attach to a driving session is REJECTED ────────────────

    def test_managed_attach_to_driving_resident_session_is_rejected(self):
        """A managed registration against a DRIVING resident session is the
        genuinely-unhandled collision (it would otherwise silently overwrite the
        live resident driver) -> hard 409 with the actionable resume command."""
        self._heartbeat_environment("claude-code")
        # Resident agent actively driving.
        self._register(agent_id="claude-x", runtime="claude-code", session_mode="resident")
        self._set_driving("claude-x")
        res = self._register(
            agent_id="claude-x", runtime="claude-code", session_mode="managed",
            bridge_id="managed-bridge",
        )
        self.assertEqual(res.status_code, 409, res.text)
        detail = (res.json().get("detail") or "")
        self.assertIn("resident", detail.lower())
        self.assertIn("managed", detail.lower())
        # actionable: contains the resume command
        self.assertIn("--resume", detail)

    def test_resident_attach_to_driving_managed_parks_candidate_with_resume(self):
        """A resident registration against a DRIVING managed session is handled
        gracefully (NOT a hard error): parked as a manual-switch candidate that
        never drives, with the actionable resume command surfaced. This still
        enforces the one-driver invariant — the resident does not take over until
        the operator switches mode."""
        self._heartbeat_environment("claude-code")
        self._register(agent_id="claude-park", runtime="claude-code", session_mode="managed")
        self._set_driving("claude-park")
        res = self._register(
            agent_id="claude-park", runtime="claude-code", session_mode="resident",
            bridge_id="resident-bridge",
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body.get("ownershipTransition"), "manual_switch_required")
        self.assertIn("--resume", body.get("resumeCommand") or "")
        # The agent stays managed — the resident did NOT take over the session.
        self.assertEqual(self._read_agent("claude-park")["session_mode"], "managed")

    def test_cross_mode_attach_when_idle_is_not_rejected(self):
        """The guard only fires when the session is actively `driving`.

        A managed agent registered WITHOUT a live driver bridge stays
        driver_state='idle', so a cross-mode attach is not a collision.
        """
        self._heartbeat_environment("claude-code")
        # Register managed metadata-only (no bridge) -> driver_state stays idle.
        self._register(agent_id="claude-idle", runtime="claude-code", session_mode="managed",
                       bridge_id="")
        self.assertEqual(self._read_agent("claude-idle")["driver_state"], "idle")
        res = self._register(
            agent_id="claude-idle", runtime="claude-code", session_mode="resident",
            bridge_id="resident-bridge",
        )
        self.assertNotEqual(res.status_code, 409, res.text)

    # ── (c) same-mode supersession (managed restart) is still allowed ─────────

    def test_same_mode_supersession_is_allowed(self):
        self._heartbeat_environment("claude-code")
        self._register(agent_id="claude-s", runtime="claude-code", session_mode="managed",
                       bridge_id="bridge-old")
        self._set_driving("claude-s")
        # Same-mode (managed) re-register with a NEW bridge = a managed restart.
        res = self._register(agent_id="claude-s", runtime="claude-code", session_mode="managed",
                             bridge_id="bridge-new")
        self.assertEqual(res.status_code, 200, res.text)

    # ── (d) resident -> managed flips back ────────────────────────────────────

    def test_resident_to_managed_flips_back(self):
        self._heartbeat_environment("claude-code")
        self._register(agent_id="claude-flip", runtime="claude-code", session_mode="managed")
        self.client.patch(
            "/api/v1/agents/claude-flip/session-mode",
            json={"mode": "resident", "force": True},
        )
        self.assertEqual(self._read_agent("claude-flip")["session_mode"], "resident")
        res = self.client.patch(
            "/api/v1/agents/claude-flip/session-mode",
            json={"mode": "managed", "force": True},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json().get("mode"), "managed")
        self.assertEqual(self._read_agent("claude-flip")["session_mode"], "managed")

    # ── api_server model: hermes managed -> resident needs no gatewayUrl ──────

    def test_hermes_managed_to_resident_without_gateway_url_no_force_ok(self):
        """api_server model: resident hermes resumes its pinned session via
        --resume, so a managed hermes agent with NO runtimeConfig.gatewayUrl can
        switch to resident WITHOUT force=true (the old tui_gateway-era 409 guard
        is gone). The switch must succeed and surface a --resume command."""
        self._heartbeat_environment("hermes")
        reg = self._register_hermes_no_gateway(agent_id="hermes-ng")
        self.assertEqual(reg.status_code, 200, reg.text)
        res = self.client.patch(
            "/api/v1/agents/hermes-ng/session-mode",
            json={"mode": "resident"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body.get("mode"), "resident")
        self.assertTrue(body.get("changed"))
        self.assertIn("--resume", body.get("resumeCommand") or "")
        self.assertEqual(self._read_agent("hermes-ng")["session_mode"], "resident")

    def test_hermes_managed_to_resident_active_run_still_blocks_without_force(self):
        """Regression: the active-run guard is KEPT — an in-flight run blocks the
        switch with 409 unless force=true (independent of the dropped gateway
        guard)."""
        self._heartbeat_environment("hermes")
        self.assertEqual(
            self._register_hermes_no_gateway(agent_id="hermes-busy").status_code, 200)
        self._insert_active_run("hermes-busy")
        res = self.client.patch(
            "/api/v1/agents/hermes-busy/session-mode",
            json={"mode": "resident"},
        )
        self.assertEqual(res.status_code, 409, res.text)
        self.assertIn("active dispatch run", (res.json().get("detail") or "").lower())
        # force=true overrides the active-run guard.
        forced = self.client.patch(
            "/api/v1/agents/hermes-busy/session-mode",
            json={"mode": "resident", "force": True},
        )
        self.assertEqual(forced.status_code, 200, forced.text)
        self.assertEqual(forced.json().get("mode"), "resident")


if __name__ == "__main__":
    unittest.main()
