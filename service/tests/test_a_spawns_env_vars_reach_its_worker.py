"""A spawn's `envVars` reach the worker it launches.

THE DEFECT, found 2026-09-14. `POST /spawn-requests` stored `envVars` in `spawn_specs.env_vars` and the
listing returned them, but `GET /terminals/{id}/launch` -- the only place a worker's environment is
composed, and whose `env` aify-env merges over its own -- never read them. Every caller that set one got
a 200 and a worker without it.

These drive the REAL spawn route for the write and the REAL launch route for the read, joined the way
the control plane joins them (terminal -> session -> spawn spec), so the stored shape and the read
shape cannot drift apart unnoticed.
"""

from __future__ import annotations

import asyncio
import json

from service.api_core.spawn_env import (
    MAX_SPAWN_ENV_VALUE_BYTES,
    MAX_SPAWN_ENV_VARS,
    spawn_env_overlay,
    spawn_env_problems,
)
from service.tests._base import FastApiTestCase


class ASpawnsEnvVarsReachItsWorkerTests(FastApiTestCase):
    DB_NAME = "aify-test-spawn-env-vars.db"

    ENV = "linux:spawn-env-host:default"

    def setUp(self):
        super().setUp()
        heartbeat = self._client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "machineId": "linux:spawn-env-host", "os": "linux", "kind": "linux",
            "bridgeId": "bridge-spawn-env", "cwdRoots": ["/work"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)

    def _spawn(self, agent_id="env-worker", **extra):
        body = {"agentId": agent_id, "environmentId": self.ENV, "runtime": "claude-code",
                "role": "coder", "workspace": "/work", "createdBy": "tester"}
        body.update(extra)
        return self._client.post("/api/v1/spawn-requests", json=body)

    def _terminal_for_spec(self, agent_id, spec_id, terminal_id="term-env"):
        """The agent, session and terminal the control plane would create, pointing at the spec.

        The agent row first: the claim report creates it in production, and `agent_sessions.agent_id`
        is a foreign key, so a session seeded without one fails naming no column."""
        from service.db import get_db

        registered = self._client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": "managed",
            "machineId": "linux:spawn-env-host", "bridgeId": "bridge-spawn-env",
        })
        self.assertEqual(registered.status_code, 200, registered.text)

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "owner_mode, terminal_id, terminal_status, started_at, last_seen, "
                    "spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"sess-{terminal_id}", agent_id, self.ENV, "claude-code", "running", "console",
                     terminal_id, "attached", "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z",
                     spec_id, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, argv, workspace, status, output, "
                    "error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (terminal_id, agent_id, f"sess-{terminal_id}", self.ENV, "claude-code",
                     "bridge-spawn-env", "claude-aify", json.dumps(["claude-aify"]), "/work",
                     "attached", "", "", "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())
        return terminal_id

    def _launch_env(self, terminal_id):
        response = self._client.get(f"/api/v1/terminals/{terminal_id}/launch")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["launch"]["env"]

    def _spec_count(self):
        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                return (await (await db.execute("SELECT COUNT(*) FROM spawn_specs")).fetchone())[0]
            finally:
                await db.close()

        return asyncio.run(go())

    def test_THE_DEFECT_a_spawns_variables_reach_the_worker_launch(self):
        created = self._spawn(envVars={"DASHBOARD_PROJECT": "aify-dashboard", "FEATURE_FLAG": "1"})
        self.assertEqual(created.status_code, 200, created.text)
        spec = created.json()["spawnRequest"]["spawnSpec"]
        self.assertEqual(spec["envVars"], {"DASHBOARD_PROJECT": "aify-dashboard", "FEATURE_FLAG": "1"})

        env = self._launch_env(self._terminal_for_spec("env-worker", spec["id"]))
        self.assertEqual(env.get("DASHBOARD_PROJECT"), "aify-dashboard")
        self.assertEqual(env.get("FEATURE_FLAG"), "1")
        # The aify variables are still there beside them: added to, not replaced.
        self.assertEqual(env["AIFY_AGENT_ID"], "env-worker")

    def test_a_spawn_cannot_rename_the_agent_it_launches(self):
        """Order, not a denylist: the spawn's variables go down first and the launch's own identity on
        top, so a caller can add to a worker's environment but not make it somebody else."""
        created = self._spawn(agent_id="real-agent", envVars={
            "AIFY_AGENT_ID": "impostor", "AIFY_SESSION_MODE": "resident", "AIFY_ENVIRONMENT_BRIDGE": "1",
            "STILL_ARRIVES": "yes",
        })
        self.assertEqual(created.status_code, 200, created.text)
        spec_id = created.json()["spawnRequest"]["spawnSpec"]["id"]
        env = self._launch_env(self._terminal_for_spec("real-agent", spec_id))
        self.assertEqual(env["AIFY_AGENT_ID"], "real-agent")
        self.assertEqual(env["AIFY_SESSION_MODE"], "managed")
        self.assertEqual(env["AIFY_ENVIRONMENT_BRIDGE"], "0")
        self.assertEqual(env["STILL_ARRIVES"], "yes")

    def test_a_session_with_no_spawn_spec_launches_with_no_extra_variables(self):
        created = self._spawn(agent_id="with-vars", envVars={"ONLY_FOR_THIS_ONE": "x"})
        self.assertEqual(created.status_code, 200, created.text)
        env = self._launch_env(self._terminal_for_spec("with-vars", None, terminal_id="term-nospec"))
        self.assertNotIn("ONLY_FOR_THIS_ONE", env)

    def test_invalid_envVars_are_refused_and_nothing_is_stored(self):
        before = self._spec_count()
        cases = {
            "a bad name": {"1STARTS_WITH_DIGIT": "x"},
            "a dash": {"HAS-DASH": "x"},
            "a number value": {"PORT": 5},
            "a null value": {"EMPTY": None},
            "a NUL byte": {"NUL": "a\x00b"},
            "an oversized value": {"BIG": "x" * (MAX_SPAWN_ENV_VALUE_BYTES + 1)},
            "too many": {f"V{i}": "x" for i in range(MAX_SPAWN_ENV_VARS + 1)},
        }
        for label, env_vars in cases.items():
            with self.subTest(label):
                response = self._spawn(agent_id="refused-agent", envVars=env_vars)
                self.assertEqual(response.status_code, 400, f"{label}: {response.text}")
                self.assertIn("envVars", response.text)
        self.assertEqual(self._spec_count(), before, "a refused spawn still wrote a spec")

    def test_the_limits_themselves_are_accepted(self):
        at_limit = {f"V{i}": "x" for i in range(MAX_SPAWN_ENV_VARS)}
        at_limit["V0"] = "x" * MAX_SPAWN_ENV_VALUE_BYTES
        self.assertEqual(spawn_env_problems(at_limit), [])
        self.assertEqual(self._spawn(agent_id="at-limit", envVars=at_limit).status_code, 200)


class SpawnEnvOverlayTests(FastApiTestCase):
    DB_NAME = "aify-test-spawn-env-overlay.db"

    def test_absent_is_valid_and_empty(self):
        self.assertEqual(spawn_env_problems(None), [])
        self.assertEqual(spawn_env_overlay(None), {})
        self.assertEqual(spawn_env_overlay({}), {})

    def test_a_stored_value_with_any_problem_contributes_nothing(self):
        """Rows written before validation existed could hold anything. Launching with the valid half
        would start a worker with a configuration nobody asked for."""
        self.assertEqual(spawn_env_overlay({"GOOD": "1", "bad-name": "2"}), {})
        self.assertEqual(spawn_env_overlay(["NOT", "A", "DICT"]), {})
        self.assertEqual(spawn_env_overlay({"GOOD": "1"}), {"GOOD": "1"})
