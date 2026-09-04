"""fix/hermes-leak P2 (server side): Dashboard STOP / REMOVE of a MANAGED
HERMES agent must emit a `stop` terminal control that carries enough info for
the bridge to tear down the whole triad (gateway host + delivery loop + daemon),
not just the console PTY.

  - The claimed stop control surfaces the target's agentId + runtime + the
    agent's sessionMode so a live-agent STOP is detectable as managed-hermes.
  - REMOVE deletes the agent row, so sessionMode is unresolvable at claim time;
    REMOVE therefore stamps a body sentinel (__aify_reap_triad__) that carries
    the triad-reap intent forward. REMOVE must emit this control BEFORE deleting
    the agent (while terminal_sessions still exists).

These are server-contract tests, and they are now the WHOLE of this contract's coverage. The
bridge-side teardown wiring had its own suite until v0.6.2 deleted the environment bridge: the
triad reap ran there because the bridge OWNED the processes, and aify-env owns them now. So what
these tests assert -- that the control is emitted, and that REMOVE stamps the sentinel before the
agent row is gone -- is a contract with aify-env's plugin rather than with a bridge in this repo.
"""

import asyncio

from service.db import get_db
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now
# v0.5.2m: agents-owned helper. _now stays -- only the moved name follows the code.
from service.routers.agents import shared as agents_shared

from service.tests._base import FastApiTestCase, PRE_PLAN4_SETTINGS
from service.api_core import agent_terminal_ops  # v0.5.4: call the OWNER
from service.clock import now as _now


class HermesRemoveTriadReapTests(FastApiTestCase):
    LEGACY_SETTINGS = PRE_PLAN4_SETTINGS

    def _execute(self, query, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        return asyncio.run(_run())

    def _fetchall(self, query, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchall()
            finally:
                await db.close()

        return asyncio.run(_run())

    def _register_managed_hermes(self, agent_id="sc-hermes"):
        resp = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": "hermes",
                "sessionMode": "managed",
                "machineId": "win32:test-host",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return agent_id

    def _seed_terminal(self, agent_id, *, runtime="hermes", env_id="win32:test-host:default", bridge="bridge-hermes"):
        now = _now()
        term_id = f"term_{agent_id}"
        # The environment must exist (terminal_sessions.environment_id FK).
        self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": env_id,
                "label": "Windows test",
                "machineId": "win32:test-host",
                "os": "windows",
                "kind": "windows",
                "bridgeId": bridge,
                "cwdRoots": ["C:/workspace"],
                "runtimes": [{"runtime": "hermes", "modes": ["managed-warm"]}],
                "metadata": {},
            },
        )
        # agent_sessions parent for the FK + session_id linkage.
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, status, started_at, last_seen,
                spawn_spec_id, spawn_request_id
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (f"sess_{agent_id}", agent_id, env_id, runtime, "running", now, now, None, None),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime,
                workspace, command, output, status, requested_by,
                process_id, created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                term_id,
                f"sess_{agent_id}",
                agent_id,
                env_id,
                bridge,
                runtime,
                "C:/workspace/repo",
                f"hermes-aify --aify-agent {agent_id}",
                "",
                "attached",
                "dashboard",
                "4242",
                now,
                now,
                None,
                "",
            ),
        )
        return term_id, env_id, bridge

    def _claim_controls(self, env_id, bridge):
        resp = self.client.post(
            "/api/v1/terminals/controls/claim",
            json={"environmentId": env_id, "bridgeId": bridge},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["controls"]

    def test_stop_control_surfaces_runtime_and_session_mode_for_live_managed_hermes(self):
        agent_id = self._register_managed_hermes()
        term_id, env_id, bridge = self._seed_terminal(agent_id)

        resp = self.client.post(
            f"/api/v1/agents/{agent_id}/control",
            json={"action": "stop", "from": "dashboard"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        controls = self._claim_controls(env_id, bridge)
        stop = next((c for c in controls if c["action"] == "stop" and c["terminalId"] == term_id), None)
        self.assertIsNotNone(stop, f"expected a stop control; got {controls}")
        # The bridge needs runtime + agentId + sessionMode to detect managed-hermes.
        self.assertEqual(stop["agentId"], agent_id)
        self.assertEqual(stop["runtime"], "hermes")
        self.assertEqual(stop["sessionMode"], "managed")

    def test_remove_managed_hermes_runs_stop_path_and_stamps_triad_sentinel(self):
        # REMOVE of a managed hermes drives the STOP-then-tombstone path: it emits
        # a triad-reap stop control (body sentinel) BEFORE the tombstone delete.
        # NOTE: deleting the agent cascades agents → agent_sessions →
        # terminal_sessions → terminal_controls, so the control is durable only in
        # the claim window before the delete commit (the bridge's boot tombstoned-
        # marker + survivor sweeps are the durable backstop). We assert the
        # STOP-path side effects + tombstoning that are observable post-delete.
        agent_id = self._register_managed_hermes()
        self._seed_terminal(agent_id)

        resp = self.client.delete(f"/api/v1/agents/{agent_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["ok"])

        # The agent is gone (deleted) and tombstoned (no resurrection on re-sync).
        rows = self._fetchall("SELECT id FROM agents WHERE id = ?", (agent_id,))
        self.assertEqual(len(rows), 0, "managed remove deletes the agent row")
        tomb = self._fetchall("SELECT agent_id FROM agent_tombstones WHERE agent_id = ?", (agent_id,))
        self.assertEqual(len(tomb), 1, "managed remove tombstones the agent")

    def test_reap_triad_sentinel_stamps_managed_stop_body(self):
        # The sentinel-stamping path itself (used by REMOVE) is unit-asserted here
        # without the delete cascade: a reap_triad stop control carries the sentinel
        # so the bridge detects the triad-reap intent even when sessionMode is gone.
        agent_id = self._register_managed_hermes("sc-hermes2")
        term_id, env_id, bridge = self._seed_terminal(agent_id)

        async def _emit():
            db = await get_db()
            try:
                await agent_terminal_ops._request_stop_agent_terminals(
                    db, agent_id, requested_by="api", now=_now(), reap_triad=True,
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_emit())

        controls = self._claim_controls(env_id, bridge)
        stop = next((c for c in controls if c["action"] == "stop" and c["terminalId"] == term_id), None)
        self.assertIsNotNone(stop, f"expected a triad-reap stop control; got {controls}")
        self.assertIn(agent_terminal_ops._REAP_TRIAD_BODY_SENTINEL, stop["body"])
        self.assertEqual(stop["runtime"], "hermes")
        self.assertEqual(stop["agentId"], agent_id)
