"""A defined agent starts from its definition, at one revision, or not at all (P0 C6, C7; the service half).

Every start the service makes writes its spawn request through `insert_spawn_request` (held by
test_every_start_goes_through_the_guarded_insert.py): the cold start (the agent-level start, a message,
a channel post, the queued-run backstop, a spec-less restart), the restart or recreate of a session
with a spec, defined or not, and the direct spawn. Each witness drives a real route and then reads the
spawn request and its spec, since the host is told what to start by those rows, not by the HTTP answer. The host's check at the process-start
boundary is aify-env's (P4); what it checks against is the launch's `definition`, witnessed here.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import service.api_core.dispatch_start as dispatch_start
from service.api_core.definition_start import CHANGED_WHILE_STARTING, start_binding
from service.db import get_db
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, B, invalid, snapshot_digest, valid

RUNTIMES = [{"runtime": runtime, "available": True} for runtime in ("claude-code", "codex", "hermes")]


class ADefinedAgentStartsFromItsDefinition(FastApiTestCase):
    DB_NAME = "aify-test-definition-start.db"

    def setUp(self):
        super().setUp()
        for host in (A, B):
            self.heartbeat(host)

    def heartbeat(self, host):
        beat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32",
            "bridgeId": host["bridge"], "cwdRoots": ["/work"], "runtimes": RUNTIMES, "metadata": {}})
        self.assertEqual(beat.status_code, 200, beat.text)

    def push(self, store, revision, entries, host=A):
        response = self.client.put(f"/api/v1/environments/{host['env']}/agent-definitions", json={
            "bridgeId": host["bridge"], "machineId": host["machine"], "storeId": store, "revision": revision,
            "snapshotDigest": snapshot_digest(entries), "entries": entries})
        self.assertEqual(response.status_code, 200, response.text)

    def start(self, agent_id):
        return self.client.post(f"/api/v1/agents/{agent_id}/control", json={"action": "start", "from_agent": "dashboard"})

    def rows(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, params)]
        finally:
            conn.close()

    def execute(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def spawns(self, agent_id):
        return self.rows(
            "SELECT r.id, r.environment_id, r.role, r.name, r.runtime, r.workspace, r.session_handle, "
            "r.resume_policy, r.start_intent, r.definition_store_id AS store, "
            "r.definition_incarnation AS incarnation, r.definition_revision AS revision, s.id AS spec_id, "
            "s.model, s.standing_instructions, s.env_vars, s.metadata FROM spawn_requests r "
            "JOIN spawn_specs s ON s.id = r.spawn_spec_id WHERE r.agent_id = ? ORDER BY r.created_at, r.rowid",
            (agent_id,))

    def register_undefined(self, agent_id):
        self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder", "runtime": "claude-code",
                                                  "sessionMode": "managed"}).raise_for_status()

    def test_a_newly_defined_id_starts_with_no_session_from_its_definition(self):
        self.push("s1", 1, [valid("coder", role="reviewer", name="Code Rev", harness="codex", model="gpt-x",
                                  effort="high", instructions="be brief", env={"K": "v"}, workspace="/work/coder")])
        self.assertEqual(self.rows("SELECT id FROM agent_sessions WHERE agent_id = 'coder'"), [],
                         "control: it has never run")
        started = self.start("coder")
        self.assertEqual((started.status_code, started.json().get("spawnRequested")), (200, True), started.text)
        [spawn] = self.spawns("coder")
        self.assertEqual(
            {k: spawn[k] for k in ("environment_id", "role", "name", "runtime", "workspace", "store", "incarnation",
                                   "revision", "model", "standing_instructions")},
            {"environment_id": A["env"], "role": "reviewer", "name": "Code Rev", "runtime": "codex",
             "workspace": "/work/coder", "store": "s1", "incarnation": 1, "revision": 1, "model": "gpt-x",
             "standing_instructions": "be brief"})
        self.assertEqual(json.loads(spawn["env_vars"]), {"K": "v"})
        self.assertEqual(json.loads(spawn["metadata"]), {"runtimeConfig": {"effort": "high"}})

    def test_an_undefined_agents_start_records_no_definition(self):
        self.register_undefined("plain")
        self.assertEqual(self.start("plain").status_code, 200)
        [spawn] = self.spawns("plain")
        self.assertEqual((spawn["store"], spawn["incarnation"], spawn["revision"], spawn["role"]), ("", 0, 0, "coder"))

    def test_a_start_is_built_from_the_current_revision(self):
        self.push("s1", 1, [valid("coder", model="m1")])
        self.push("s1", 2, [valid("coder", revision=2, model="m2")])
        self.assertEqual(self.start("coder").status_code, 200)
        [spawn] = self.spawns("coder")
        self.assertEqual((spawn["revision"], spawn["model"]), (2, "m2"))

    def test_a_changed_harness_takes_effect_at_the_next_start(self):
        """C5: the effective runtime column describes the running process and is not rewritten by a push;
        the next start, here from a message, which passes that column, runs the definition's harness."""
        self.push("s1", 1, [valid("coder")])
        self.push("s1", 2, [valid("coder", revision=2, harness="codex")])
        [row] = self.rows("SELECT runtime FROM agents WHERE id = 'coder'")
        self.assertEqual(row["runtime"], "claude-code", "control: the effective runtime is unchanged")
        self.assertTrue(self.cold_start("coder", [], runtime=row["runtime"]))
        self.assertEqual(self.spawns("coder")[0]["runtime"], "codex")

    def test_an_earlier_session_on_another_machine_does_not_move_a_defined_agent(self):
        self.push("s1", 1, [valid("coder")])
        self.execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, status, started_at, "
                     "last_seen) VALUES ('sess-b', 'coder', ?, 'claude-code', '/work', 'stopped', 'now', 'now')",
                     (B["env"],))
        self.assertEqual(self.start("coder").status_code, 200)
        self.assertEqual(self.spawns("coder")[0]["environment_id"], A["env"])

    def test_a_defined_agent_starts_on_the_machine_that_defines_it(self):
        self.push("s1", 1, [valid("coder")])
        # B is the freshest environment that can run claude-code; A is still online. Set rather than
        # heartbeated, since two heartbeats in one second tie.
        aged = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30))
        self.execute("UPDATE environments SET last_seen = ? WHERE id = ?", (aged, A["env"]))
        self.register_undefined("plain")
        self.assertEqual(self.start("plain").status_code, 200)
        self.assertEqual(self.spawns("plain")[0]["environment_id"], B["env"],
                         "control: an undefined agent takes the freshest environment")
        self.assertEqual(self.start("coder").status_code, 200)
        self.assertEqual(self.spawns("coder")[0]["environment_id"], A["env"])

    def test_a_withdrawn_agent_is_not_started_restarted_or_spawned(self):
        self.push("s1", 1, [valid("coder")])
        self.execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, started_at, last_seen) "
                     "VALUES ('sess-coder', 'coder', ?, 'claude-code', 'stopped', 'now', 'now')", (A["env"],))
        self.push("s1", 2, [])
        why = 'Agent "coder" was withdrawn in aify-env on win32:host-a; define it again to start it'
        started = self.start("coder")
        self.assertEqual((started.status_code, started.json()["detail"]), (409, why))
        restarted = self.client.post("/api/v1/sessions/sess-coder/control", json={"action": "restart", "from_agent": "dashboard"})
        self.assertEqual((restarted.status_code, restarted.json()["detail"]), (409, why))
        spawned = self.client.post("/api/v1/spawn-requests", json={
            "agentId": "coder", "environmentId": A["env"], "runtime": "claude-code", "workspace": "/work"})
        self.assertEqual((spawned.status_code, spawned.json()["detail"]), (409, why))
        reasons: list[str] = []
        self.assertFalse(self.cold_start("coder", reasons), "a message does not start it either")
        self.assertEqual(reasons, [f"{dispatch_start.COLDSTART_REFUSED_PREFIX}{why}"])
        self.assertEqual(self.spawns("coder"), [])
        self.push("s1", 3, [valid("coder", revision=3)])
        self.assertEqual(self.start("coder").status_code, 200, "control: defined again, it starts")

    def test_a_resident_definition_is_not_started_and_starts_once_managed_again(self):
        self.push("s1", 1, [valid("coder", mode="resident")])
        refused = self.start("coder")
        self.assertEqual((refused.status_code, refused.json()["detail"]), (409,
                         'Agent "coder" is defined in aify-env on win32:host-a as resident; '
                         'start it through its launcher on that host'))
        self.push("s1", 2, [valid("coder", revision=2, mode="managed")])
        self.assertEqual(self.rows("SELECT session_mode FROM agents WHERE id = 'coder'")[0]["session_mode"],
                         "resident", "control: the effective mode is still resident")
        started = self.start("coder")
        self.assertEqual((started.status_code, started.json().get("spawnRequested")), (200, True), started.text)

    def test_a_managed_worker_is_not_started_beside_a_live_resident(self):
        self.push("s1", 1, [valid("coder", mode="resident")])
        self.push("s1", 2, [valid("coder", revision=2)])
        self.execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, started_at, last_seen) "
                     "VALUES ('sess-res', 'coder', ?, 'claude-code', 'resident', 'running', 'now', 'now')", (A["env"],))
        reasons: list[str] = []
        self.assertFalse(self.cold_start("coder", reasons))
        self.assertIn("a managed worker beside it would be a twin", reasons[0])
        self.execute("UPDATE agent_sessions SET status = 'stopped' WHERE id = 'sess-res'")
        self.assertTrue(self.cold_start("coder", []), "control: once the resident has stopped, it starts")

    def test_a_definition_its_host_cannot_use_is_not_started(self):
        self.push("s1", 1, [valid("coder", available=False)])
        refused = self.start("coder")
        self.assertEqual(refused.json()["detail"], 'Agent "coder" is defined on win32:host-a, which cannot run it: '
                                                   'harness-not-installed')
        self.push("s1", 2, [invalid("coder", "agent.model: type")])
        refused = self.start("coder")
        self.assertEqual((refused.status_code, refused.json()["detail"]), (409,
                         'Agent "coder" has an invalid definition on win32:host-a (agent.model: type); fix it there to start it'))
        self.assertEqual(self.spawns("coder"), [])

    def restartable(self, model, handle="h1", agent_id="coder", session_id="sess-1", environment=A):
        """A worker that ran from a start of `agent_id`: its spawn request running, its session on
        `environment`. A defined agent is pushed first."""
        if agent_id == "coder":
            self.push("s1", 1, [valid("coder", model=model)])
        self.assertEqual(self.start(agent_id).status_code, 200)
        first = self.spawns(agent_id)[-1]
        self.execute("UPDATE spawn_requests SET status = 'running' WHERE id = ?", (first["id"],))
        self.execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, spawn_spec_id, "
                     "spawn_request_id, session_handle, started_at, last_seen) VALUES (?, ?, ?, "
                     "'claude-code', 'managed-warm', 'running', ?, ?, ?, 'now', 'now')",
                     (session_id, agent_id, environment["env"], first["spec_id"], first["id"], handle))
        return first

    def test_a_restart_with_its_machine_offline_says_so_and_does_not_move(self):
        self.restartable("m1")
        self.execute("UPDATE environments SET last_seen = '2000-01-01T00:00:00Z' WHERE id = ?", (A["env"],))
        restarted = self.client.post("/api/v1/sessions/sess-1/control", json={"action": "restart", "from_agent": "dashboard"})
        self.assertEqual((restarted.status_code, restarted.json()["detail"]), (409,
                         'Agent "coder" is defined on win32:host-a, and no online environment there can start '
                         '"claude-code"; start aify-env on that host'))
        self.assertEqual(len(self.spawns("coder")), 1, "B, online on another machine, is not used")

    def test_an_undefined_restart_overtaken_by_a_definition_queues_nothing(self):
        """Review of 8de83233, N6: the undefined restart from a session's old spec read "never defined",
        and a push or a withdrawal committing right after that read must not be answered with a start
        from the old spec, nor a recreate forgetting the agent's native session."""
        import service.api_core.session_restart as session_restart

        real = session_restart.start_binding
        self.addCleanup(setattr, session_restart, "start_binding", real)
        for action in ("restart", "recreate"):
            for arm in ("defined", "withdrawn"):
                with self.subTest(action=action, arm=arm):
                    agent_id, session_id = f"p-{action}-{arm}", f"sess-{action}-{arm}"
                    self.register_undefined(agent_id)
                    self.restartable("", agent_id=agent_id, session_id=session_id)
                    self.execute("UPDATE agents SET session_handle = 'h1' WHERE id = ?", (agent_id,))

                    async def read_then_overtaken(db, read_id, arm=arm):
                        binding = await real(db, read_id)
                        if arm == "defined":
                            self.execute(
                                "INSERT INTO agent_definitions (agent_id, machine_id, store_id, incarnation, revision, "
                                "definition_digest, body, available, updated_at) VALUES (?, ?, 's1', 1, 1, 'd', ?, 1, 'now')",
                                (read_id, A["machine"], json.dumps(valid(read_id)["definition"])))
                        else:
                            self.execute("UPDATE agents SET definition_state = 'withdrawn' WHERE id = ?", (read_id,))
                        return binding

                    session_restart.start_binding = read_then_overtaken
                    answered = self.client.post(f"/api/v1/sessions/{session_id}/control",
                                                json={"action": action, "from_agent": "dashboard"})
                    session_restart.start_binding = real
                    self.assertEqual((answered.status_code, answered.json().get("detail")),
                                     (409, f'Agent "{agent_id}": {CHANGED_WHILE_STARTING}'))
                    self.assertEqual(len(self.spawns(agent_id)), 1, "no start from the old spec")
                    self.assertEqual(self.rows("SELECT session_handle FROM agents WHERE id = ?", (agent_id,)),
                                     [{"session_handle": "h1"}], "the agent still names its native session")

    def test_an_undefined_restart_from_its_old_spec_and_one_defined_first(self):
        """The controls for N6, both orders serialised: undefined throughout, it restarts from its old spec
        unbound; defined before the restart, it restarts from the definition."""
        self.register_undefined("plain")
        self.restartable("", agent_id="plain", session_id="sess-plain")
        self.execute("UPDATE spawn_specs SET model = 'old-model' WHERE agent_id = 'plain'")
        plain = self.client.post("/api/v1/sessions/sess-plain/control", json={"action": "restart", "from_agent": "dashboard"})
        self.assertEqual(plain.status_code, 200, plain.text)
        latest = self.spawns("plain")[-1]
        self.assertEqual((latest["store"], latest["revision"], latest["model"]), ("", 0, "old-model"))
        self.register_undefined("later")
        self.restartable("", agent_id="later", session_id="sess-later")
        self.push("s1", 1, [valid("later", model="new-model")])
        defined = self.client.post("/api/v1/sessions/sess-later/control", json={"action": "restart", "from_agent": "dashboard"})
        self.assertEqual(defined.status_code, 200, defined.text)
        latest = self.spawns("later")[-1]
        self.assertEqual((latest["store"], latest["revision"], latest["model"]), ("s1", 1, "new-model"))

    def test_a_restart_runs_on_the_machine_that_defines_it(self):
        self.restartable("m1", environment=B)
        restarted = self.client.post("/api/v1/sessions/sess-1/control", json={"action": "restart", "from_agent": "dashboard"})
        self.assertEqual(restarted.status_code, 200, restarted.text)
        self.assertEqual(self.spawns("coder")[-1]["environment_id"], A["env"])

    def test_a_restart_is_built_from_the_definition_not_the_sessions_spec(self):
        self.restartable("m1")
        self.push("s1", 2, [valid("coder", revision=2, model="m2")])
        restarted = self.client.post("/api/v1/sessions/sess-1/control", json={"action": "restart", "from_agent": "dashboard"})
        self.assertEqual(restarted.status_code, 200, restarted.text)
        latest = self.spawns("coder")[-1]
        self.assertEqual({k: latest[k] for k in ("revision", "model", "environment_id", "session_handle", "resume_policy",
                                                 "start_intent")},
                         {"revision": 2, "model": "m2", "environment_id": A["env"], "session_handle": "h1",
                          "resume_policy": "native_first", "start_intent": "replace"})

    def test_a_recreate_is_built_from_the_definition_and_forgets_the_native_session(self):
        self.restartable("m1")
        self.execute("UPDATE agents SET session_handle = 'h1' WHERE id = 'coder'")
        recreated = self.client.post("/api/v1/sessions/sess-1/control", json={"action": "recreate", "from_agent": "dashboard"})
        self.assertEqual(recreated.status_code, 200, recreated.text)
        latest = self.spawns("coder")[-1]
        self.assertEqual((latest["revision"], latest["session_handle"], latest["resume_policy"]), (1, "", "fresh_context"))
        self.assertEqual(self.rows("SELECT session_handle FROM agents WHERE id = 'coder'")[0]["session_handle"], "")

    def test_the_launch_carries_the_definition_it_was_built_from(self):
        first = self.restartable("m1")
        self.register_undefined("plain")
        self.restartable("", agent_id="plain", session_id="sess-plain")
        for terminal, agent_id, session in (("term-bound", "coder", "sess-1"), ("term-plain", "plain", "sess-plain")):
            self.execute("INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, runtime, bridge_id, "
                         "command, status, output, error, cols, rows, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, 'claude-code', ?, 'claude-aify', 'attached', '', '', 80, 24, 'now', 'now')",
                         (terminal, agent_id, session, A["env"], A["bridge"]))
        bound = self.client.get("/api/v1/terminals/term-bound/launch").json()["launch"]["definition"]
        self.assertEqual(bound, {"storeId": "s1", "incarnation": 1, "revision": first["revision"]})
        plain = self.client.get("/api/v1/terminals/term-plain/launch").json()["launch"]
        self.assertIsNone(plain["definition"], "an undefined agent's start carries none, and its host does no check")

    def cold_start(self, agent_id, reasons, runtime="claude-code"):
        async def run():
            db = await get_db()
            try:
                started = await dispatch_start._coldstart_spawn_request_for_dispatch(
                    db, agent_id, runtime=runtime, settings={}, requested_by="test", warnings=reasons)
                await db.commit()
                return started
            finally:
                await db.close()
        return asyncio.run(run())

    def test_a_definition_changed_while_starting_queues_nothing(self):
        """The write asks again: a binding read before a push landed inserts no request and leaves no spec."""
        self.push("s1", 1, [valid("coder")])

        async def read():
            db = await get_db()
            try:
                return await start_binding(db, "coder")
            finally:
                await db.close()

        stale = asyncio.run(read())
        self.push("s1", 2, [valid("coder", revision=2)])
        real = dispatch_start.start_binding

        async def read_before_the_push(db, agent_id):
            return stale

        dispatch_start.start_binding = read_before_the_push
        self.addCleanup(setattr, dispatch_start, "start_binding", real)
        reasons: list[str] = []
        self.assertFalse(self.cold_start("coder", reasons))
        self.assertEqual(reasons, [f"{dispatch_start.COLDSTART_REFUSED_PREFIX}{CHANGED_WHILE_STARTING}"])
        self.assertEqual(self.rows("SELECT id FROM spawn_requests WHERE agent_id = 'coder'"), [])
        self.assertEqual(self.rows("SELECT id FROM spawn_specs WHERE agent_id = 'coder'"), [])

        async def read_before_it_was_defined(db, agent_id):
            return None

        dispatch_start.start_binding = read_before_it_was_defined
        self.assertFalse(self.cold_start("coder", []), "an undefined reading of a defined agent inserts nothing")
        dispatch_start.start_binding = real
        self.assertTrue(self.cold_start("coder", []), "control: read now, it starts")

    def test_a_restart_from_a_definition_that_changed_meanwhile_queues_nothing(self):
        import service.api_core.session_restart as session_restart

        first = self.restartable("m1")

        async def read():
            db = await get_db()
            try:
                return await start_binding(db, "coder")
            finally:
                await db.close()

        stale = asyncio.run(read())
        self.push("s1", 2, [valid("coder", revision=2, model="m2")])
        real = session_restart.start_binding

        async def read_before_the_push(db, agent_id):
            return stale

        session_restart.start_binding = read_before_the_push
        self.addCleanup(setattr, session_restart, "start_binding", real)
        restarted = self.client.post("/api/v1/sessions/sess-1/control", json={"action": "restart", "from_agent": "dashboard"})
        self.assertEqual((restarted.status_code, restarted.json()["detail"]), (409, f'Agent "coder": {CHANGED_WHILE_STARTING}'))
        self.assertEqual([spawn["id"] for spawn in self.spawns("coder")], [first["id"]])
