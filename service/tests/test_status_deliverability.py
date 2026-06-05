"""Task 1.6 (2026-05-30, Runtime Symmetry & Session Governance) — truthful
status for managed hermes.

`_compute_agent_status` / `_compute_live_status_cache` must report `online`
(deliverable, idle) for a managed hermes agent ONLY when the agent is actually
deliverable: channel-enabled (the hermes-aify wrapper exported
AIFY_CHANNELS_ENABLED=1) AND a LIVE channel sidecar (hermes-channel.js) is
heartbeating. With no/stale sidecar heartbeat the agent must report
`available` — never a falsely positive `online`/`ready`.

This MIRRORS claude's existing `has_live_worker` gate. For claude the live
signal is the claude-aify wrapper PTY terminal_session (the sidecar runs INSIDE
that PTY). The standalone hermes sidecar owns no PTY, so its liveness signal is
its own channel-sidecar bridge heartbeat (kept fresh by the /dispatch/claim
poll loop, which updates bridge_instances.last_seen) — the runtime-agnostic
equivalent. See the shared deliverability branch in _compute_live_status_cache.

claude + resident behavior is unchanged (regression guards below); codex/pi
(no channel flag) are unaffected.
"""

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.db import init_db
from service.routers.api_v2 import router


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


from service.tests._base import FastApiTestCase


class StatusDeliverabilityTests(FastApiTestCase):
    # hermes is NOT in managed_via_wrapper here — the standalone channel
    # sidecar path (Task 1.5b) is the one under test, gated purely on the
    # channelEnabled flag, exactly like claude.
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex"]}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
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
        res = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(res.status_code, 200, res.text)

    def _register_managed(self, *, agent_id: str, runtime: str, channel_enabled: bool) -> None:
        runtime_config = {}
        if channel_enabled:
            runtime_config["channelEnabled"] = True
        if runtime == "codex":
            runtime_config["appServerUrl"] = "ws://127.0.0.1:1234"
        res = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": runtime,
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["resume", "interrupt"],
                "runtimeConfig": runtime_config,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)

    def _stamp_channel_sidecar_bridge(self, agent_id: str, *, fresh: bool) -> None:
        """Insert a channel-sidecar bridge_instances row for the agent (the
        standalone hermes-channel.js / claude-channel.js process). `fresh`
        controls last_seen — a fresh row models a LIVE sidecar; an aged row
        models a dead/stale sidecar."""
        now = datetime.now(timezone.utc)
        last_seen = _iso(now if fresh else now - timedelta(minutes=20))
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO bridge_instances (
                    id, agent_id, machine_id, runtime, session_mode, session_handle,
                    terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "hermes-channel-linux:test-host",
                    agent_id,
                    "linux:test-host",
                    "hermes",
                    "managed",
                    "",
                    "",
                    "channel-sidecar",
                    _iso(now - timedelta(minutes=1)),
                    last_seen,
                    "",
                    None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _insert_claude_wrapper_pty(self, agent_id: str) -> None:
        """A live claude-aify wrapper PTY terminal_session — claude's existing
        has_live_worker signal (the sidecar runs inside this PTY)."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            session_id = f"sess-{agent_id}"
            terminal_id = f"term-{agent_id}"
            now = _iso(datetime.now(timezone.utc))
            conn.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                    terminal_id, terminal_status, status, started_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id, agent_id, "linux:test-host:default", "claude-code",
                    "/workspace", "managed-warm", "managed", terminal_id, "running",
                    "running", now, now,
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
                    terminal_id, session_id, agent_id, "linux:test-host:default",
                    "bridge-current", "claude-code", "/workspace",
                    f"claude-aify --aify-agent {agent_id}", "running", "dashboard", now, now,
                ),
            )
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()

    def _status(self, agent_id: str) -> str:
        res = self.client.get(f"/api/v1/agents/{agent_id}")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["agent"]["status"]

    def _insert_hermes_console_pty(self, agent_id: str) -> None:
        """A live hermes console PTY terminal_session — the visible-TUI console
        the delivery loop fronts. WS3 (2026-06-02): managed hermes is in
        _CHANNEL_SIDECAR_DELIVERY_RUNTIMES, so `online` requires BOTH this live
        console AND a live channel-sidecar claimer (visible-TUI HARD requirement).
        Mirrors _insert_claude_wrapper_pty for hermes."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            session_id = f"sess-{agent_id}"
            terminal_id = f"term-{agent_id}"
            now = _iso(datetime.now(timezone.utc))
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                    terminal_id, terminal_status, status, started_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id, agent_id, "linux:test-host:default", "hermes",
                    "/workspace", "managed-warm", "managed", terminal_id, "attached",
                    "running", now, now,
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
                    terminal_id, session_id, agent_id, "linux:test-host:default",
                    "bridge-current", "hermes", "/workspace",
                    f"hermes-aify --aify-agent {agent_id}", "attached", "dashboard", now, now,
                ),
            )
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()

    def _insert_managed_session(self, agent_id: str, runtime: str) -> None:
        """A managed agent_sessions row so the claim gate's managed-environment
        carve-out applies (mirrors a real warm managed hermes session). Without
        it, /dispatch/claim rejects the sidecar bridge as bridge_not_current."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            now = _iso(datetime.now(timezone.utc))
            conn.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                    terminal_id, terminal_status, status, started_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"sess-{agent_id}", agent_id, "linux:test-host:default", runtime,
                    "/workspace", "managed-warm", "managed", "", "",
                    "running", now, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _channel_sidecar_claim(self, agent_id: str, *, bridge_id: str) -> dict:
        """Idle channel-sidecar poll: a /dispatch/claim with no queued run,
        declaring bridgeKind='channel-sidecar' (Task 1.5b flag)."""
        res = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": agent_id,
                "bridgeId": bridge_id,
                "machineId": "linux:test-host",
                "bridgeKind": "channel-sidecar",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def _channel_sidecar_bridge_row(self, agent_id: str) -> tuple | None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute(
                """
                SELECT id, bridge_kind, last_seen, COALESCE(superseded_by, '')
                FROM bridge_instances
                WHERE agent_id = ? AND bridge_kind = 'channel-sidecar'
                """,
                (agent_id,),
            ).fetchone()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # hermes — the new behavior
    # ------------------------------------------------------------------
    def test_managed_hermes_channel_enabled_with_live_sidecar_is_online(self):
        # WS3 (2026-06-02): UPDATED — hermes joined _CHANNEL_SIDECAR_DELIVERY_
        # RUNTIMES, so `online` now requires BOTH a live console PTY (visible-TUI
        # HARD requirement) AND a live channel-sidecar claimer, matching claude's
        # both-required gate. The prior sidecar-ALONE→online behavior was the
        # superseded "online but no visible console" path (a headless orphan).
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-live", runtime="hermes", channel_enabled=True)
        self._insert_hermes_console_pty("hermes-live")
        self._stamp_channel_sidecar_bridge("hermes-live", fresh=True)
        self.assertIn(
            self._status("hermes-live"),
            {"online", "ready"},
            "channel-enabled managed hermes WITH a live console PTY AND a live sidecar heartbeat must be deliverable (online/ready)",
        )

    def test_managed_hermes_channel_enabled_without_sidecar_is_available_not_online(self):
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-nosidecar", runtime="hermes", channel_enabled=True)
        # No channel-sidecar bridge row at all → not deliverable.
        status = self._status("hermes-nosidecar")
        self.assertNotIn(
            status, {"online", "ready"},
            f"channel-enabled hermes with NO live sidecar must not be falsely online; got {status!r}",
        )
        self.assertEqual(status, "available", f"expected available; got {status!r}")

    def test_managed_hermes_channel_enabled_with_stale_sidecar_is_available(self):
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-stale", runtime="hermes", channel_enabled=True)
        self._stamp_channel_sidecar_bridge("hermes-stale", fresh=False)
        status = self._status("hermes-stale")
        self.assertNotIn(
            status, {"online", "ready"},
            f"a STALE sidecar heartbeat must not keep hermes online; got {status!r}",
        )
        self.assertEqual(status, "available", f"expected available; got {status!r}")

    # ------------------------------------------------------------------
    # Task 1.6b — the idle claim poll IS the liveness heartbeat
    # ------------------------------------------------------------------
    def test_idle_channel_sidecar_claim_upserts_bridge_row_and_makes_hermes_online(self):
        """An idle hermes sidecar polls /dispatch/claim continuously even with
        NO queued run. That poll must upsert the channel-sidecar bridge row so
        _has_live_channel_sidecar is true and status computes online — without
        the poll the agent is correctly `available`."""
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-idle", runtime="hermes", channel_enabled=True)
        self._insert_managed_session("hermes-idle", "hermes")
        # WS3 (2026-06-02): `online` now requires a live console PTY too (visible-
        # TUI HARD requirement). The delivery loop fronts this console; this test
        # is about the SIDECAR-CLAIM-AS-HEARTBEAT mechanism, so seed the console so
        # the both-required gate's console half is satisfied and the test isolates
        # the sidecar-liveness behavior under test.
        self._insert_hermes_console_pty("hermes-idle")

        # Before any claim: no sidecar row exists. A fresh console whose sidecar has never
        # registered is BOOTING (2026-06-05) → it reads `online` (worker starting). The idle
        # claim below upserts a LIVE channel-sidecar bridge row — the mechanism under test —
        # which is what keeps it `online` for REAL (deliverable), not merely booting.
        self.assertIsNone(self._channel_sidecar_bridge_row("hermes-idle"))
        self.assertIn(
            self._status("hermes-idle"), {"online", "ready"},
            "a fresh console with no sidecar yet is booting → online",
        )

        # Idle claim poll (no queued run) declaring channel-sidecar.
        body = self._channel_sidecar_claim("hermes-idle", bridge_id="hermes-channel-linux:test-host")
        self.assertIsNone(body.get("run"), f"no queued run should be claimed; got {body}")

        # The poll itself upserted a fresh channel-sidecar bridge row.
        row = self._channel_sidecar_bridge_row("hermes-idle")
        self.assertIsNotNone(row, "idle channel-sidecar claim must upsert a channel-sidecar bridge row")
        self.assertEqual(row[1], "channel-sidecar")
        self.assertEqual(row[3], "", "the upserted row must not be superseded")

        # Now the agent is deliverable.
        self.assertIn(
            self._status("hermes-idle"), {"online", "ready"},
            "after an idle channel-sidecar claim poll the agent must be online (live sidecar heartbeat)",
        )

    def test_repeated_idle_channel_sidecar_claims_are_idempotent(self):
        """Each poll just refreshes last_seen — no duplicate rows, no
        supersession churn."""
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-poller", runtime="hermes", channel_enabled=True)
        self._insert_managed_session("hermes-poller", "hermes")
        # WS3 (2026-06-02): seed the live console PTY so the both-required gate's
        # console half is satisfied — this test isolates sidecar-poll idempotency.
        self._insert_hermes_console_pty("hermes-poller")
        for _ in range(3):
            self._channel_sidecar_claim("hermes-poller", bridge_id="hermes-channel-linux:test-host")
        conn = sqlite3.connect(str(self._db_path))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM bridge_instances WHERE agent_id = ? AND bridge_kind = 'channel-sidecar'",
                ("hermes-poller",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1, "repeated idle polls must not create duplicate channel-sidecar rows")
        self.assertIn(self._status("hermes-poller"), {"online", "ready"})

    def test_idle_non_sidecar_claim_does_not_upsert_channel_sidecar_row(self):
        """A claim that does NOT declare bridgeKind='channel-sidecar' (e.g. the
        environment bridge or a wrapper-child poll) must not synthesize a
        channel-sidecar liveness row."""
        self._heartbeat_environment("hermes")
        self._register_managed(agent_id="hermes-envpoll", runtime="hermes", channel_enabled=True)
        res = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "hermes-envpoll",
                "bridgeId": "bridge-current",
                "machineId": "linux:test-host",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIsNone(
            self._channel_sidecar_bridge_row("hermes-envpoll"),
            "a non-sidecar claim must not create a channel-sidecar bridge row",
        )
        self.assertEqual(self._status("hermes-envpoll"), "available")

    # ------------------------------------------------------------------
    # claude — regression guard (unchanged)
    # ------------------------------------------------------------------
    def test_managed_claude_with_live_pty_and_live_sidecar_is_online(self):
        # status-F1 (2026-05-31): a HEALTHY managed claude has BOTH a live wrapper
        # PTY (renders) and a live channel-sidecar (claude-channel.js, the actual
        # claimer). With both present it is deliverable → online.
        self._heartbeat_environment("claude-code")
        self._register_managed(agent_id="claude-live", runtime="claude-code", channel_enabled=True)
        self._insert_claude_wrapper_pty("claude-live")
        self._stamp_channel_sidecar_bridge("claude-live", fresh=True)
        self.assertEqual(
            self._status("claude-live"), "online",
            "managed claude with a live PTY AND a live sidecar must be online",
        )

    def test_managed_claude_live_pty_dead_sidecar_is_available_not_online(self):
        # status-F1 regression (operator-reported 2026-05-31, sc-claude): the
        # claude-aify wrapper PTY only RENDERS; claude-channel.js is the sole
        # claimer. A live "Console" PTY with a DEAD/superseded sidecar delivers
        # NOTHING — runs sit queued — so it must report `available`, not a
        # falsely-positive `online`. Previously the PTY alone made it online.
        self._heartbeat_environment("claude-code")
        self._register_managed(agent_id="claude-deadcar", runtime="claude-code", channel_enabled=True)
        self._insert_claude_wrapper_pty("claude-deadcar")          # live PTY (renders)
        self._stamp_channel_sidecar_bridge("claude-deadcar", fresh=False)  # stale sidecar (~20m)
        # DEAF, not booting (2026-06-05): age the console so its sidecar registered FOR THIS
        # console (~20m ago) then died — a genuinely deaf worker that must stay `available`. (A
        # fresh console whose sidecar has NEVER registered is BOOTING and reads `online`; the
        # discriminator is whether a sidecar was last-seen AFTER the console started.)
        _con = sqlite3.connect(str(self._db_path))
        _con.execute(
            "UPDATE terminal_sessions SET created_at = ? WHERE agent_id = ?",
            (_iso(datetime.now(timezone.utc) - timedelta(minutes=40)), "claude-deadcar"),
        )
        _con.commit()
        _con.close()
        status = self._status("claude-deadcar")
        self.assertNotIn(
            status, {"online", "ready"},
            f"a live PTY with a dead sidecar must NOT be falsely online; got {status!r}",
        )
        self.assertEqual(status, "available", f"expected available; got {status!r}")

    def test_managed_console_booting_no_sidecar_yet_is_online(self):
        # BOOT vs DEAF (2026-06-05, operator-chosen): a managed claude whose console JUST came
        # up but whose channel-sidecar has NOT registered yet is BOOTING — it reads `online` so
        # the operator doesn't miss the terminal. DISPLAY-ONLY: has_live_worker stays False so a
        # send still queues until the sidecar claims. Contrast the dead-sidecar test above (a
        # sidecar that registered for this console then DIED stays `available`).
        self._heartbeat_environment("claude-code")
        self._register_managed(agent_id="claude-booting", runtime="claude-code", channel_enabled=True)
        self._insert_claude_wrapper_pty("claude-booting")  # fresh live console, NO sidecar yet
        self.assertEqual(
            self._status("claude-booting"), "online",
            "a fresh console whose sidecar hasn't registered yet is booting → online",
        )

    def test_managed_console_after_restart_old_sidecar_is_booting_online(self):
        # Cross-restart (the mp-manager 'restarted, missed the terminal' case): a relaunched
        # console with only a STALE sidecar from a PRIOR session (last_seen BEFORE the new
        # console started) is BOOTING for the new console → `online` (its own sidecar hasn't
        # registered yet). This is why the discriminator is relational (sidecar-since-console),
        # not 'a sidecar row exists' — an old row persists across restarts.
        self._heartbeat_environment("claude-code")
        self._register_managed(agent_id="claude-relaunch", runtime="claude-code", channel_enabled=True)
        self._stamp_channel_sidecar_bridge("claude-relaunch", fresh=False)  # old sidecar (prior session)
        self._insert_claude_wrapper_pty("claude-relaunch")  # NEW console created now (after the old sidecar)
        self.assertEqual(
            self._status("claude-relaunch"), "online",
            "a new console after restart with only a pre-console stale sidecar → booting → online",
        )

    def test_managed_claude_without_live_worker_is_available(self):
        self._heartbeat_environment("claude-code")
        self._register_managed(agent_id="claude-dead", runtime="claude-code", channel_enabled=True)
        # No wrapper PTY, no sidecar → claude degrades to available.
        self.assertEqual(
            self._status("claude-dead"), "available",
            "managed claude with no live worker must be available (unchanged)",
        )

    # ------------------------------------------------------------------
    # codex / pi — cheap unchanged guard
    # ------------------------------------------------------------------
    def test_managed_codex_unaffected_by_channel_deliverability_gate(self):
        """codex is not a channel-flag runtime; the hermes deliverability gate
        must not change its status. Managed codex with no live worker is
        available (its existing taxonomy), and a channel-sidecar row must not
        make it online (codex is not sidecar-channel)."""
        self._heartbeat_environment("codex")
        self._register_managed(agent_id="codex-x", runtime="codex", channel_enabled=False)
        status = self._status("codex-x")
        self.assertEqual(status, "available", f"managed codex no-worker should be available; got {status!r}")


class ManagedEnvBridgeGateTests(FastApiTestCase):
    """FIX B (2026-06-02): a MANAGED agent whose OWNING ENVIRONMENT bridge is
    offline/stale must compute as `offline` — regardless of any surviving
    delivery-loop heartbeat/lease/sidecar — because only the env bridge can
    spawn/host its worker. The operator killed the `aify-comms` env bridge and the
    managed agents STILL showed `available`/`online` because detached loops kept
    heartbeating; this gate closes that hole. Resident agents (whose liveness is
    their resident bridge, not the env bridge) must NOT be forced offline by a down
    env bridge.
    """

    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex"]}

    ENV_ID = "linux:test-host:default"

    def _heartbeat_environment(self, runtime: str) -> None:
        payload = {
            "id": self.ENV_ID,
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
        res = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(res.status_code, 200, res.text)

    def _age_environment(self, *, minutes: int) -> None:
        """Push the environment's last_seen far enough into the past that
        _environment_effective_status computes `offline` (env bridge is dead).
        This models the operator killing the env bridge."""
        stale = _iso(datetime.now(timezone.utc) - timedelta(minutes=minutes))
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("UPDATE environments SET last_seen = ? WHERE id = ?", (stale, self.ENV_ID))
            conn.commit()
        finally:
            conn.close()

    def _register_managed_hermes(self, agent_id: str) -> None:
        res = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": "hermes",
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["resume", "interrupt"],
                "runtimeConfig": {"channelEnabled": True, "environmentId": self.ENV_ID},
            },
        )
        self.assertEqual(res.status_code, 200, res.text)

    def _register_resident_claude(self, agent_id: str) -> None:
        res = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": "claude-code",
                "sessionMode": "resident",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["resident-run", "resume", "interrupt"],
                "runtimeConfig": {"environmentId": self.ENV_ID},
            },
        )
        self.assertEqual(res.status_code, 200, res.text)

    def _bind_session_and_workers(self, agent_id: str, runtime: str) -> None:
        """Seed a live managed session + console PTY + fresh channel-sidecar row so
        the agent would otherwise compute `online` (the surviving-detached-loop
        scenario). environment_id is bound on the session row."""
        now = _iso(datetime.now(timezone.utc))
        session_id = f"sess-{agent_id}"
        terminal_id = f"term-{agent_id}"
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                    terminal_id, terminal_status, status, started_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id, agent_id, self.ENV_ID, runtime, "/workspace",
                    "managed-warm", "managed", terminal_id, "attached", "running", now, now,
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
                    terminal_id, session_id, agent_id, self.ENV_ID, "bridge-current",
                    runtime, "/workspace", f"hermes-aify --aify-agent {agent_id}",
                    "attached", "dashboard", now, now,
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO bridge_instances (
                    id, agent_id, machine_id, runtime, session_mode, session_handle,
                    terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "hermes-channel-linux:test-host", agent_id, "linux:test-host",
                    "hermes", "managed", "", "", "channel-sidecar",
                    _iso(datetime.now(timezone.utc) - timedelta(minutes=1)), now, "", None,
                ),
            )
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()

    def _heartbeat_agent(self, agent_id: str) -> None:
        """A surviving detached loop keeps the AGENT heartbeat fresh even though
        the env bridge is dead — the exact condition that kept it `available`."""
        res = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": agent_id,
                "bridgeId": "hermes-channel-linux:test-host",
                "machineId": "linux:test-host",
                "bridgeKind": "channel-sidecar",
                "executionModes": ["channel", "resident"],
            },
        )
        self.assertEqual(res.status_code, 200, res.text)

    def _status(self, agent_id: str) -> str:
        res = self.client.get(f"/api/v1/agents/{agent_id}")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["agent"]["status"]

    # ---- the new gate ----
    def test_managed_with_live_env_bridge_is_online(self):
        # Control: a managed agent with a LIVE env bridge + live workers is online.
        self._heartbeat_environment("hermes")
        self._register_managed_hermes("env-live-hermes")
        self._bind_session_and_workers("env-live-hermes", "hermes")
        self.assertIn(
            self._status("env-live-hermes"), {"online", "ready"},
            "managed agent with a live env bridge + live workers must be online",
        )

    def test_managed_with_dead_env_bridge_is_offline_despite_live_sidecar(self):
        # FIX B: kill the env bridge (stale last_seen). Even though the surviving
        # delivery loop keeps a fresh sidecar + console + agent heartbeat (would
        # otherwise be `online`), the agent must compute `offline` because only the
        # env bridge can host its worker.
        self._heartbeat_environment("hermes")
        self._register_managed_hermes("env-dead-hermes")
        self._bind_session_and_workers("env-dead-hermes", "hermes")
        # Sanity: live env → online.
        self.assertIn(self._status("env-dead-hermes"), {"online", "ready"})
        # Operator kills the env bridge; a detached loop keeps heartbeating.
        self._age_environment(minutes=20)
        self._heartbeat_agent("env-dead-hermes")
        status = self._status("env-dead-hermes")
        self.assertEqual(
            status, "offline",
            f"a managed agent whose owning env bridge is offline must compute offline "
            f"even with a fresh sidecar/lease; got {status!r}",
        )

    def test_managed_dead_env_offline_via_stored_binding_no_session_row(self):
        # FIX B core hole: the worker died so there is NO live session row binding
        # environment_id and runtime_state has none either — only the STORED binding
        # (runtime_config.environmentId / machine_id+runtime) identifies the owning
        # env. A surviving detached sidecar keeps heartbeating. The OLD env-offline
        # branch never fired (environment_id resolved empty) so the agent wrongly
        # stayed `available`; the new resolver must force `offline`.
        self._heartbeat_environment("hermes")
        self._register_managed_hermes("env-dead-nosession")
        # Fresh channel-sidecar bridge only — NO session row, NO console PTY.
        now = _iso(datetime.now(timezone.utc))
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO bridge_instances (
                    id, agent_id, machine_id, runtime, session_mode, session_handle,
                    terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "hermes-channel-linux:test-host", "env-dead-nosession", "linux:test-host",
                    "hermes", "managed", "", "", "channel-sidecar",
                    _iso(datetime.now(timezone.utc) - timedelta(minutes=1)), now, "", None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        # With the env still live this is `available` (no live session/console).
        self.assertEqual(self._status("env-dead-nosession"), "available")
        # Kill the env bridge.
        self._age_environment(minutes=20)
        status = self._status("env-dead-nosession")
        self.assertEqual(
            status, "offline",
            f"a managed agent whose owning env bridge is offline must compute offline "
            f"via its stored binding even with no session row; got {status!r}",
        )

    def test_resident_not_forced_offline_by_dead_env_bridge(self):
        # Regression: a resident agent's liveness is its resident bridge, NOT the
        # env bridge. A down env bridge must NOT force a resident agent offline.
        self._heartbeat_environment("claude-code")
        self._register_resident_claude("resident-claude")
        # Resident heartbeat keeps the resident bridge fresh.
        res = self.client.post(
            "/api/v1/dispatch/claim",
            json={
                "agentId": "resident-claude",
                "bridgeId": "bridge-current",
                "machineId": "linux:test-host",
                "executionModes": ["resident"],
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        self._age_environment(minutes=20)
        status = self._status("resident-claude")
        self.assertNotEqual(
            status, "offline",
            f"a resident agent must NOT be forced offline by a down env bridge; got {status!r}",
        )


if __name__ == "__main__":
    unittest.main()
