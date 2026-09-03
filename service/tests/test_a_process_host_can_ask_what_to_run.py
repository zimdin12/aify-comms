"""`GET /terminals/{id}/launch` -- the seam aify-env becomes the process host through.

WHY IT EXISTS. Until now the only tier that could start a managed worker was the aify-comms
environment BRIDGE, because starting one meant composing the launch and that composition lived on
the host. The bridge is being removed. The operator's reasoning for it is exact: aify-comms is a
container service, so it cannot hold the agents -- they would be in the container's environment
rather than the host's.

So this endpoint states the split. The service says WHAT to run: the program, its arguments (both
already on the terminal row since Phase 8), and the aify-owned variables the worker needs. The host
adds only what the service cannot know -- its own base environment, and CODEX_HOME, which names a
directory that must be CREATED on the machine that runs the process.

WHAT THESE PIN:
  - a host is never handed a blank launch and left to report success on it, which is the silence
    this tier keeps being bitten by;
  - `argv` travels, because that is the form a host can execute -- the first delegated spawn failed
    for exactly this, the loop read argv to find a session handle and then dropped it;
  - no base environment travels, ever. A process environment on the wire carries whatever the
    sender happened to hold, including its secrets.
"""

from __future__ import annotations

import json

from service.tests._base import FastApiTestCase


class AProcessHostCanAskWhatToRunTests(FastApiTestCase):
    DB_NAME = "aify-test-terminal-launch.db"

    ENV = "linux:launch-host:default"

    def _register(self, agent_id="sc-lead", runtime="claude-code", role="coder", **extra):
        heartbeat = self._client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "machineId": "linux:launch-host", "os": "linux", "kind": "linux",
            "bridgeId": "bridge-launch", "cwdRoots": ["/work"],
            "runtimes": [{"runtime": runtime, "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        body = {
            "agentId": agent_id, "role": role, "runtime": runtime,
            "sessionMode": "managed", "machineId": "linux:launch-host", "bridgeId": "bridge-launch",
        }
        body.update(extra)
        registered = self._client.post("/api/v1/agents", json=body)
        self.assertEqual(registered.status_code, 200, registered.text)
        return agent_id

    def _terminal(self, agent_id="sc-lead", *, argv=None, terminal_id="term-1"):
        """Seeded directly, as this suite's siblings do: a terminal row is created by the control
        plane in the course of a spawn, and reproducing that whole path would test the spawn rather
        than this endpoint."""
        import asyncio
        import json as _json

        from service.db import get_db

        launch_argv = ["claude-aify", "--aify-agent", agent_id] if argv is None else argv

        async def go():
            db = await get_db()
            try:
                # THE SESSION ROW FIRST: `terminal_sessions.session_id` is a FOREIGN KEY, and
                # omitting it fails with "FOREIGN KEY constraint failed" naming no column at all.
                # `spawn_spec_id` and `spawn_request_id` are named and NULLed for the same reason --
                # both are nullable foreign keys whose column DEFAULT is '', which no spawn row has.
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "owner_mode, terminal_id, terminal_status, started_at, last_seen, "
                    "spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("sess-launch", agent_id, self.ENV, "claude-code", "running", "console",
                     terminal_id, "attached", "2026-09-03T00:00:00Z", "2026-09-03T00:00:00Z",
                     None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, argv, workspace, status, output, "
                    "error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (terminal_id, agent_id, "sess-launch", self.ENV, "claude-code", "bridge-launch",
                     f"claude-aify --aify-agent {agent_id}", _json.dumps(launch_argv), "/work",
                     "attached", "", "", "2026-09-03T00:00:00Z", "2026-09-03T00:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())
        return terminal_id

    def _launch(self, terminal_id):
        response = self._client.get(f"/api/v1/terminals/{terminal_id}/launch")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["launch"]

    def test_the_launch_carries_the_program_AND_its_argv(self):
        """ARGV IS THE FORM A HOST CAN EXECUTE. The first delegated spawn failed for exactly this
        -- the control loop read `argv` to find a session handle and then dropped it, so the host
        received an empty list and threw. A command string alone would make every host split it,
        which is the quoting bug this design avoids."""
        self._register()
        launch = self._launch(self._terminal())
        self.assertEqual(launch["argv"], ["claude-aify", "--aify-agent", "sc-lead"])
        self.assertTrue(launch["command"], "the string form must still travel for a human to read")
        self.assertEqual(launch["cwd"], "/work")

    def test_the_launch_carries_the_aify_variables_the_worker_needs(self):
        self._register(role="tester")
        launch = self._launch(self._terminal())
        env = launch["env"]
        self.assertEqual(env["AIFY_AGENT_ID"], "sc-lead")
        self.assertEqual(env["AIFY_SESSION_MODE"], "managed")
        self.assertEqual(env["AIFY_ENVIRONMENT_BRIDGE"], "0")
        self.assertEqual(env["AIFY_TERMINAL_ID"], launch["terminalId"])

    def test_THE_SPAWNS_ROLE_REACHES_THE_WORKER(self):
        """The bug this closes was silent and expensive: a worker with no AIFY_AGENT_ROLE read its
        own default, self-registered as `coder`, and re-register is a full state refresh -- so
        asking for a tester produced a coder with nothing reporting a problem."""
        self._register(role="tester")
        self.assertEqual(self._launch(self._terminal())["env"]["AIFY_AGENT_ROLE"], "tester")

    def test_NO_BASE_ENVIRONMENT_TRAVELS(self):
        """A process environment on the wire carries whatever the sender happened to hold. This is
        an OVERLAY, and it must stay small by construction -- a large one means a base env leaked."""
        self._register()
        env = self._launch(self._terminal())["env"]
        self.assertNotIn("PATH", env)
        self.assertNotIn("CODEX_HOME", env, "the host creates that directory; the service cannot")
        self.assertLess(len(env), 40, f"the overlay grew to {len(env)} keys, which looks like a leak")

    def test_an_unknown_terminal_is_404_rather_than_a_blank_launch(self):
        """A host given a blank command starts nothing and reports success. That is the exact
        silence this tier keeps being bitten by, so the refusal has to be loud."""
        self.assertEqual(self._client.get("/api/v1/terminals/nope/launch").status_code, 404)

    def test_a_terminal_with_NO_argv_still_answers_and_says_so_with_an_empty_list(self):
        """An operator-supplied command has no argv: we did not build it, and splitting it would be
        the parse this avoids. An empty list is a real answer meaning "not ours to run", and a host
        must be able to tell that from a missing field."""
        self._register()
        terminal_id = self._terminal(argv=[])
        launch = self._launch(terminal_id)
        self.assertEqual(launch["argv"], [])
        self.assertTrue(launch["command"])

    def test_a_terminal_CANNOT_outlive_its_agent_here_and_that_is_the_schema_saying_so(self):
        """The endpoint tolerates a missing agent -- every field the agent contributes is an
        override, and `managed_launch_env` is unit-tested with `agent={}`. What this records is that
        the state is UNREACHABLE through the database rather than merely unhandled:
        `agent_sessions.agent_id` and `terminal_sessions.session_id` are foreign keys, the latter
        `ON DELETE CASCADE`, so removing an agent takes its terminals with it.

        Written as a test rather than a comment because the tolerance above would otherwise look
        like dead code to the next reader, and deleting it would be reasonable right up until a
        schema change made the state reachable again."""
        with self.assertRaises(Exception):
            self._terminal(agent_id="ghost-agent", terminal_id="term-ghost")

    def test_the_session_handle_reaches_the_runtimes_OWN_variable(self):
        """`AIFY_SESSION_HANDLE` is ours; `CLAUDE_SESSION_ID` is what the runtime actually reads.
        Sending only the first resumes nothing, and a resume that silently starts fresh is how an
        agent loses its whole conversation while every status reads healthy.

        IT COMES FROM THE AGENT ROW. `terminal_sessions` has no session-handle column -- checked
        against the schema, not assumed -- so a composer reading only the terminal would send an
        empty handle for every managed agent and nothing would report a problem."""
        self._register(sessionHandle="sess-abc")
        env = self._launch(self._terminal())["env"]
        self.assertEqual(env["AIFY_SESSION_HANDLE"], "sess-abc")
        self.assertEqual(env["CLAUDE_SESSION_ID"], "sess-abc")

    def test_the_response_is_JSON_SERIALISABLE_end_to_end(self):
        """CONTROL. Every assertion above reads a parsed body, so a value the encoder cannot handle
        would fail as a 500 here rather than as a confusing shape there."""
        self._register()
        json.dumps(self._launch(self._terminal()))
