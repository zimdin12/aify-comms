"""Plan 6 C1-C2 (2026-05-26) — `PATCH /api/v1/agents/{id}/session-mode`.

Operator-driven resident/managed mode flip. Today the wrapper auto-detects
via `[ -t 0 ]`; this endpoint lets the operator override the agent's
`session_mode` from the dashboard regardless of how the wrapper was
launched.

Edge cases covered:
- mode validation (must be 'resident' or 'managed')
- 404 when agent is unknown
- no-op (changed=False) when the requested mode matches the current
- 409 when an active dispatch run is in flight (unless force=true)
- 409 when hermes is being flipped managed -> resident without a
  gatewayUrl (unless force=true)
- audit log row in `dispatch_events` keyed by agent_id with type
  `mode_switch_<old>_to_<new>`
- state-transition side effects (C2): managed -> resident releases the
  managed PTY if any; resident -> managed eager-spawn is invoked.
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


class AgentSessionModeSwitchTests(FastApiTestCase):
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

    def _register_agent(self, *, agent_id: str, runtime: str, session_mode: str, runtime_config: dict | None = None) -> None:
        rc = dict(runtime_config or {})
        if runtime == "codex" and "appServerUrl" not in rc:
            rc["appServerUrl"] = "ws://127.0.0.1:1234"
        if runtime == "hermes" and "gatewayUrl" in (runtime_config or {}):
            rc["gatewayUrl"] = (runtime_config or {})["gatewayUrl"]
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": runtime,
                "sessionMode": session_mode,
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["managed-run", "resume", "interrupt"],
                "runtimeConfig": rc,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _read_agent_mode(self, agent_id: str) -> str:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT session_mode FROM agents WHERE id = ?", (agent_id,)).fetchone()
        finally:
            conn.close()
        return str(row["session_mode"] or "") if row else ""

    def _read_agent_row(self, agent_id: str) -> sqlite3.Row | None:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        finally:
            conn.close()

    def _seed_active_run(self, agent_id: str) -> str:
        """Insert a synthetic 'running' dispatch_runs row to simulate an in-flight run."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """
                INSERT INTO dispatch_runs (
                    id, target_agent, from_agent, subject, body, message_type, status,
                    dispatch_mode, execution_mode, runtime, requested_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "run_test_active",
                    agent_id,
                    "dashboard",
                    "test-active",
                    "in flight",
                    "request",
                    "running",
                    "managed",
                    "managed",
                    "codex",
                    "2026-05-26T00:00:00Z",
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return "run_test_active"

    def _read_dispatch_events_for_agent(self, agent_id: str) -> list[sqlite3.Row]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(
                "SELECT event_type, body FROM dispatch_events WHERE body LIKE ? ORDER BY id",
                (f"%{agent_id}%",),
            ).fetchall())
        finally:
            conn.close()

    # ─── C1 ────────────────────────────────────────────────────────────────

    def test_switch_resident_to_managed_without_backing_succeeds_with_warning(self):
        # RELAXED 2026-06-11: no live managed session/backing used to 409; since lazy
        # auto-start the agent is simply `available` and cold-starts on the next send —
        # blocking the flip stranded resident agents on offline machines.
        self._heartbeat_environment("codex")
        self._register_agent(agent_id="codex-noback", runtime="codex", session_mode="resident")
        # The old PC's sessions are long dead — mark every session row ended (the user case).
        import sqlite3
        con = sqlite3.connect(str(self._db_path))
        con.execute("UPDATE agent_sessions SET status='ended' WHERE agent_id='codex-noback'")
        con.commit(); con.close()
        res = self.client.patch(
            "/api/v1/agents/codex-noback/session-mode",
            json={"mode": "managed"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("mode"), "managed")
        self.assertIn("cold-start", str(body.get("warning") or ""))
        self.assertEqual(self._read_agent_mode("codex-noback"), "managed")

    def test_switch_resident_to_managed_success(self):
        self._heartbeat_environment("codex")
        self._register_agent(agent_id="codex-r1", runtime="codex", session_mode="resident")
        self._seed_managed_terminal("codex-r1", runtime="codex")
        res = self.client.patch(
            "/api/v1/agents/codex-r1/session-mode",
            json={"mode": "managed"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("mode"), "managed")
        self.assertEqual(body.get("previousMode"), "resident")
        self.assertTrue(body.get("changed"))
        self.assertEqual(self._read_agent_mode("codex-r1"), "managed")
        agent = self._read_agent_row("codex-r1")
        self.assertEqual(agent["launch_mode"], "managed")
        self.assertIn("managed-run", agent["capabilities"])

    def test_switch_invalid_mode_returns_400(self):
        self._heartbeat_environment("codex")
        self._register_agent(agent_id="codex-r2", runtime="codex", session_mode="resident")
        res = self.client.patch(
            "/api/v1/agents/codex-r2/session-mode",
            json={"mode": "frobnicate"},
        )
        self.assertEqual(res.status_code, 400, res.text)

    def test_switch_unknown_agent_returns_404(self):
        res = self.client.patch(
            "/api/v1/agents/nonexistent/session-mode",
            json={"mode": "managed"},
        )
        self.assertEqual(res.status_code, 404, res.text)

    def test_switch_to_same_mode_is_noop(self):
        self._heartbeat_environment("codex")
        self._register_agent(agent_id="codex-r3", runtime="codex", session_mode="resident")
        res = self.client.patch(
            "/api/v1/agents/codex-r3/session-mode",
            json={"mode": "resident"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertFalse(body.get("changed"))
        self.assertEqual(body.get("mode"), "resident")

    def test_switch_blocked_by_active_run(self):
        self._heartbeat_environment("codex")
        self._register_agent(agent_id="codex-busy", runtime="codex", session_mode="resident")
        self._seed_active_run("codex-busy")
        res = self.client.patch(
            "/api/v1/agents/codex-busy/session-mode",
            json={"mode": "managed"},
        )
        self.assertEqual(res.status_code, 409, res.text)
        self.assertIn("active", (res.json().get("detail") or "").lower())

    def test_switch_force_bypasses_active_run(self):
        self._heartbeat_environment("codex")
        self._register_agent(agent_id="codex-force", runtime="codex", session_mode="resident")
        self._seed_active_run("codex-force")
        res = self.client.patch(
            "/api/v1/agents/codex-force/session-mode",
            json={"mode": "managed", "force": True},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._read_agent_mode("codex-force"), "managed")
        self.assertEqual(self._read_agent_row("codex-force")["launch_mode"], "managed")

    def test_switch_hermes_managed_to_resident_without_gateway_succeeds(self):
        # api_server model: resident hermes resumes its pinned session via
        # --resume and needs no gatewayUrl. The old tui_gateway-era 409 guard was
        # removed, so this switch must succeed WITHOUT force=true.
        self._heartbeat_environment("hermes")
        # Register hermes agent WITHOUT gatewayUrl, in managed mode.
        self._register_agent(
            agent_id="hermes-no-gw",
            runtime="hermes",
            session_mode="managed",
            runtime_config={},
        )
        res = self.client.patch(
            "/api/v1/agents/hermes-no-gw/session-mode",
            json={"mode": "resident"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._read_agent_mode("hermes-no-gw"), "resident")
        self.assertIn("--resume", res.json().get("resumeCommand") or "")

    def test_switch_hermes_managed_to_resident_with_force_succeeds(self):
        self._heartbeat_environment("hermes")
        self._register_agent(
            agent_id="hermes-force",
            runtime="hermes",
            session_mode="managed",
            runtime_config={},
        )
        res = self.client.patch(
            "/api/v1/agents/hermes-force/session-mode",
            json={"mode": "resident", "force": True},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._read_agent_mode("hermes-force"), "resident")

    def test_switch_hermes_to_registered_resident_candidate_uses_gateway(self):
        self._heartbeat_environment("hermes")
        self._register_agent(
            agent_id="hermes-candidate",
            runtime="hermes",
            session_mode="managed",
            runtime_config={},
        )
        registered = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "hermes-candidate",
                "role": "coder",
                "runtime": "hermes",
                "sessionMode": "resident",
                "sessionHandle": "resident-hermes-session",
                "machineId": "linux:test-host",
                "bridgeId": "resident-bridge",
                "capabilities": ["resident-run", "resume", "interrupt"],
                "runtimeConfig": {"gatewayUrl": "ws://127.0.0.1:9999/api/ws?token=test"},
            },
        )
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertEqual(registered.json().get("ownershipTransition"), "manual_switch_required")

        res = self.client.patch(
            "/api/v1/agents/hermes-candidate/session-mode",
            json={"mode": "resident"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        agent = self._read_agent_row("hermes-candidate")
        self.assertEqual(agent["session_mode"], "resident")
        self.assertEqual(agent["session_handle"], "resident-hermes-session")
        self.assertIn("gatewayUrl", agent["runtime_config"])
        self.assertIn("resident-run", agent["capabilities"])

    def test_switch_appends_dispatch_event(self):
        """C1 audit log: dispatch_events row referencing agent id + transition type."""
        self._heartbeat_environment("codex")
        self._register_agent(agent_id="codex-audit", runtime="codex", session_mode="resident")
        self._seed_managed_terminal("codex-audit", runtime="codex")
        res = self.client.patch(
            "/api/v1/agents/codex-audit/session-mode",
            json={"mode": "managed"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        events = self._read_dispatch_events_for_agent("codex-audit")
        types = [e["event_type"] for e in events]
        self.assertIn("mode_switch_resident_to_managed", types, f"got events: {[dict(e) for e in events]}")

    # ─── C2 — state-transition side effects ───────────────────────────────

    def _seed_managed_terminal(self, agent_id: str, *, runtime: str = "codex") -> tuple[str, str]:
        """Seed a 'running' terminal_sessions row + matching agent_sessions row
        wired up the way `_active_terminal_for_agent` expects. Returns
        (terminal_id, session_id)."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            session_id = f"session_{agent_id}"
            terminal_id = f"term_{agent_id}"
            now = "2099-01-01T00:00:00Z"
            env_row = conn.execute("SELECT id, bridge_id FROM environments LIMIT 1").fetchone()
            env_id = env_row["id"] if env_row else "linux:test-host:default"
            bridge_id = env_row["bridge_id"] if env_row else "bridge-current"
            # agent_sessions row first.
            conn.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, status, workspace,
                    started_at, last_seen, terminal_id, terminal_status, owner_mode
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id, agent_id, env_id, runtime, "running",
                    "/workspace", now, now, terminal_id, "running", "managed",
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
                    terminal_id, session_id, agent_id, env_id, bridge_id,
                    runtime, "/workspace", f"{runtime}-aify", "running",
                    "dashboard", now, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return terminal_id, session_id

    def _read_terminal_status(self, terminal_id: str) -> str:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT status FROM terminal_sessions WHERE id = ?", (terminal_id,)).fetchone()
        finally:
            conn.close()
        return str(row["status"] or "") if row else ""

    def test_switch_managed_to_resident_releases_managed_terminal(self):
        """C2: when an agent has a live managed PTY and the operator flips
        to resident, the PTY's status flips to 'stopping' so the bridge
        side reconciles the close cleanly."""
        self._heartbeat_environment("codex")
        self._register_agent(agent_id="codex-pty", runtime="codex", session_mode="managed")
        terminal_id, _session_id = self._seed_managed_terminal("codex-pty", runtime="codex")
        # Touch the environment so its last-seen stays current (>30s tolerance).
        self._heartbeat_environment("codex")
        # Sanity: terminal currently active.
        self.assertEqual(self._read_terminal_status(terminal_id), "running")
        res = self.client.patch(
            "/api/v1/agents/codex-pty/session-mode",
            json={"mode": "resident"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._read_terminal_status(terminal_id), "stopping",
                         "Plan 6 C2: managed -> resident must release the managed PTY")
        body = res.json()
        self.assertEqual(body.get("sideEffects", {}).get("stoppedTerminalId"), terminal_id)
        agent = self._read_agent_row("codex-pty")
        self.assertEqual(agent["launch_mode"], "detached")
        self.assertIn("resident-run", agent["capabilities"])

    def test_switch_resident_to_managed_without_backing_reports_missing_backing(self):
        """resident -> managed without force: under the lazy-autostart governance
        (DECISIONS.md 2026-05-31, Phase 2) an `available` managed agent with no
        live worker is NOT hard-blocked — switching to managed succeeds (200) and
        changes metadata, while the best-effort eager-PTY side effect reports the
        absent backing in `sideEffects.error` (Plan 6 C2: "side-effect failures
        don't roll back the mode change — they surface in response.sideEffects").

        This is the same observable contract as the force=true sibling
        (`test_switch_resident_to_managed_force_reports_missing_backing`); the
        only thing force gates here is the in-flight-run check, not a
        "requires existing managed backing" guard. The next dispatch lazily
        cold-starts a spawn_request rather than the operator pre-provisioning a
        managed PTY.
        """
        self._heartbeat_environment("codex")
        self._register_agent(agent_id="codex-noenv", runtime="codex", session_mode="resident")
        res = self.client.patch(
            "/api/v1/agents/codex-noenv/session-mode",
            json={"mode": "managed"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body.get("mode"), "managed")
        self.assertEqual(body.get("previousMode"), "resident")
        self.assertTrue(body.get("changed"))
        self.assertEqual(self._read_agent_mode("codex-noenv"), "managed")
        # 2026-06-03: resident->managed for a wrapper-backed runtime (codex/hermes)
        # now COLDSTARTS a managed-warm spawn_request at switch time (the lazy
        # next-dispatch autostart became an at-switch coldstart), so the side effect
        # reports managedSpawnRequested rather than a missing-backing error.
        self.assertTrue((body.get("sideEffects") or {}).get("managedSpawnRequested"))

    # ─── 2026-06-12 — the sc-manager "sent but never received" strand ─────────

    def _register_resident_candidate(self, agent_id: str) -> None:
        """Re-register an existing managed agent as a LIVE resident session (the
        operator's launch-terminal-first flow): creates the resident bridge row and
        records manualResidentCandidate for the later mode switch."""
        res = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": "claude-code",
                "sessionMode": "resident",
                "sessionHandle": "resident-claude-session",
                "machineId": "linux:test-host",
                "bridgeId": "resident-bridge-live",
                "capabilities": ["resident-run", "resume", "interrupt"],
            },
        )
        self.assertEqual(res.status_code, 200, res.text)

    def _read_driver_state(self, agent_id: str) -> str:
        row = self._read_agent_row(agent_id)
        return str(row["driver_state"] or "") if row else ""

    def test_switch_to_resident_with_live_candidate_keeps_driving(self):
        """Switching to resident while ADOPTING a live resident bridge must leave
        driver_state='driving' — the old unconditional 'idle' made the server tell the
        resident session's OWN channel sidecar to release, silently killing delivery."""
        self._heartbeat_environment("claude-code")
        self._register_agent(agent_id="claude-flip", runtime="claude-code", session_mode="managed")
        self._register_resident_candidate("claude-flip")
        res = self.client.patch(
            "/api/v1/agents/claude-flip/session-mode",
            json={"mode": "resident"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._read_agent_mode("claude-flip"), "resident")
        self.assertEqual(self._read_driver_state("claude-flip"), "driving",
                         "adopting a live resident candidate must keep it the active driver")
        # The resident's own sidecar heartbeat must NOT be told to release.
        hb = self.client.post(
            "/api/v1/agents/claude-flip/heartbeat",
            json={"bridgeId": "channel-test-claude-flip", "bridgeKind": "channel-sidecar", "liveness": True},
        )
        self.assertEqual(hb.status_code, 200, hb.text)
        self.assertFalse(hb.json().get("release"), "live resident driver's sidecar must keep driving")

    def test_sidecar_heartbeat_adopts_live_resident_driver(self):
        """Self-heal: resident agent with driver_state drifted to 'idle' but a FRESH
        resident bridge beating — the sidecar heartbeat must adopt 'driving' instead of
        releasing (a release here permanently severed delivery pre-fix)."""
        self._heartbeat_environment("claude-code")
        self._register_agent(agent_id="claude-heal", runtime="claude-code", session_mode="managed")
        self._register_resident_candidate("claude-heal")
        conn = sqlite3.connect(str(self._db_path))
        try:
            # The resident registration just wrote a fresh ISO last_seen on the
            # resident bridge row; only the driver_state drift is simulated here.
            conn.execute("UPDATE agents SET session_mode='resident', driver_state='idle' WHERE id='claude-heal'")
            conn.commit()
        finally:
            conn.close()
        hb = self.client.post(
            "/api/v1/agents/claude-heal/heartbeat",
            json={"bridgeId": "channel-test-claude-heal", "bridgeKind": "channel-sidecar", "liveness": True},
        )
        self.assertEqual(hb.status_code, 200, hb.text)
        self.assertFalse(hb.json().get("release"), "fresh resident bridge → adopt driving, not release")
        self.assertEqual(self._read_driver_state("claude-heal"), "driving")

    def test_sidecar_heartbeat_still_releases_without_live_resident(self):
        """The release path stays intact for the case it was built for: a displaced
        managed sidecar with NO live resident session behind the agent."""
        self._heartbeat_environment("claude-code")
        self._register_agent(agent_id="claude-displaced", runtime="claude-code", session_mode="managed")
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("UPDATE agents SET session_mode='resident', driver_state='idle' WHERE id='claude-displaced'")
            conn.commit()
        finally:
            conn.close()
        hb = self.client.post(
            "/api/v1/agents/claude-displaced/heartbeat",
            json={"bridgeId": "channel-test-claude-displaced", "bridgeKind": "channel-sidecar", "liveness": True},
        )
        self.assertEqual(hb.status_code, 200, hb.text)
        self.assertTrue(hb.json().get("release"), "no live resident bridge → the displaced sidecar still releases")

    def test_switch_resident_to_managed_force_reports_missing_backing(self):
        self._heartbeat_environment("codex")
        self._register_agent(agent_id="codex-force-noenv", runtime="codex", session_mode="resident")
        res = self.client.patch(
            "/api/v1/agents/codex-force-noenv/session-mode",
            json={"mode": "managed", "force": True},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body.get("mode"), "managed")
        self.assertTrue((body.get("sideEffects") or {}).get("managedSpawnRequested"))


if __name__ == "__main__":
    unittest.main()
