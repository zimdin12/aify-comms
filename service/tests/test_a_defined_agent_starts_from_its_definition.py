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
from service.api_core.definition_start import CHANGED_WHILE_STARTING, start_binding, undefined_refusal
from service.api_core.managed_env import _select_online_environment_for_runtime
from service.db import get_db
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define, undefine
from service.tests.test_agent_definition_push import A, B, invalid, snapshot_digest, valid

RUNTIMES = [{"runtime": runtime, "available": True} for runtime in ("claude-code", "codex", "hermes")]


class ColdStart:
    """A cold start's answer in the shape the start route gave: 200 when it queued a spawn, else 409 and why."""
    def __init__(self, started, reasons):
        self.status_code = 200 if started else 409
        self.text = " ".join(reasons)
        self._body = {"spawnRequested": True} if started else {
            "detail": reasons[0].removeprefix(dispatch_start.COLDSTART_REFUSED_PREFIX) if reasons else ""}

    def json(self):
        return self._body


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
        """The start that builds a spawn from the definition: a message's cold start. Since D9c the
        dashboard's start of a DEFINED agent queues a lifecycle request instead
        (test_legacy_routes_delegate_defined_agents.py), and the host's launch is built from the same
        definition (test_lifecycle_launch.py)."""
        reasons: list[str] = []
        return ColdStart(self.cold_start(agent_id, reasons), reasons)

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

    def started_before_d8(self, agent_id):
        """An agent started before D8 that no host defines, as the live fleet still has: since an undefined
        agent is no longer started, it is started defined (on B, so A's snapshot is left alone), then its
        definition goes and its spawn request records none, as an undefined start's did."""
        define(self, agent_id, environment_id=B["env"], machine_id=B["machine"], bridge_id=B["bridge"],
               workspace="/work")
        self.assertEqual(self.start(agent_id).status_code, 200)
        undefine(self, agent_id)
        self.execute("UPDATE spawn_requests SET definition_store_id = '', definition_incarnation = 0, "
                     "definition_revision = 0 WHERE agent_id = ?", (agent_id,))

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

    def test_an_undefined_agent_is_not_started_and_says_how_to_define_it(self):
        """D8: an agent no host defines is never started; before D8 it started recording no definition."""
        self.register_undefined("plain")
        refused = self.start("plain")
        self.assertEqual((refused.status_code, refused.json()["detail"]), (409, undefined_refusal("plain")))
        self.assertEqual(self.spawns("plain"), [])
        self.assertEqual(self.rows("SELECT id FROM spawn_specs WHERE agent_id = 'plain'"), [], "nor a spec")

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

        async def freshest():
            db = await get_db()
            try:
                return await _select_online_environment_for_runtime(db, "claude-code")
            finally:
                await db.close()

        self.assertEqual(asyncio.run(freshest())["id"], B["env"],
                         "control: with no machine to keep to, the freshest environment is B")
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
        else:
            self.started_before_d8(agent_id)
        first = self.spawns(agent_id)[-1]
        self.execute("UPDATE spawn_requests SET status = 'running' WHERE id = ?", (first["id"],))
        self.execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, spawn_spec_id, "
                     "spawn_request_id, session_handle, started_at, last_seen) VALUES (?, ?, ?, "
                     "'claude-code', 'managed-warm', 'running', ?, ?, ?, 'now', 'now')",
                     (session_id, agent_id, environment["env"], first["spec_id"], first["id"], handle))
        return first

    def test_an_undefined_restart_is_refused_and_not_built_from_its_old_spec(self):
        """N6 left no window: since D8 the route reads nothing after its own check, so an undefined agent is
        refused, where before D8 it restarted from its old spec unbound. Defined before the restart, it goes to the lifecycle queue instead
        (test_legacy_routes_delegate_defined_agents.py)."""
        self.register_undefined("plain")
        self.restartable("", agent_id="plain", session_id="sess-plain")
        self.execute("UPDATE spawn_specs SET model = 'old-model' WHERE agent_id = 'plain'")
        for action in ("restart", "recreate"):
            with self.subTest(action):
                plain = self.client.post("/api/v1/sessions/sess-plain/control",
                                         json={"action": action, "from_agent": "dashboard"})
                self.assertEqual((plain.status_code, plain.json().get("detail")), (409, undefined_refusal("plain")))
                self.assertEqual(len(self.spawns("plain")), 1, "no start from the old spec")

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

    def test_a_re_registered_model_does_not_reach_a_defined_agents_launch(self):
        """External review of 0.8.4: re-registering rewrote the record's model and effort without the operator
        key, and a new terminal on the running session launched with them, not the definition's."""
        self.push("s1", 1, [valid("coder", model="m1", effort="high")])
        self.assertEqual(self.start("coder").status_code, 200)
        self.register_undefined("plain")
        self.started_before_d8("plain")
        for agent_id, session, terminal in (("coder", "sess-1", "term-bound"), ("plain", "sess-plain", "term-plain")):
            spawn = self.spawns(agent_id)[-1]
            self.execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, spawn_spec_id, "
                         "spawn_request_id, session_handle, started_at, last_seen) VALUES (?, ?, ?, "
                         "'claude-code', 'managed-warm', 'running', ?, ?, 'h1', 'now', 'now')",
                         (session, agent_id, A["env"], spawn["spec_id"], spawn["id"]))
            self.execute("INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, runtime, bridge_id, "
                         "command, status, output, error, cols, rows, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, 'claude-code', ?, 'claude-aify', 'attached', '', '', 80, 24, 'now', 'now')",
                         (terminal, agent_id, session, A["env"], A["bridge"]))
            self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": "managed",
                "model": "rogue", "runtimeConfig": {"effort": "low"}}).raise_for_status()
        self.assertEqual([r["model"] for r in self.rows("SELECT model FROM agents WHERE id IN ('coder', 'plain') ORDER BY id")],
                         ["rogue", "rogue"], "control: the re-register did rewrite both records")
        env = lambda terminal: self.client.get(f"/api/v1/terminals/{terminal}/launch").json()["launch"]["env"]
        bound = env("term-bound")
        self.assertEqual((bound["AIFY_MANAGED_MODEL"], bound["AIFY_MANAGED_EFFORT"]), ("m1", "high"),
                         "a defined agent's launch runs its definition's model and effort")
        plain = env("term-plain")
        self.assertEqual((plain["AIFY_MANAGED_MODEL"], plain["AIFY_MANAGED_EFFORT"]), ("rogue", "low"),
                         "control: an undefined agent's launch still runs its record")

    def bound_session_with_a_rogue_record(self, spec_link):
        """`coder` defined (m1/high) and started, its session linked to its request, its own spec link set to
        `spec_link`, then its record rewritten to rogue/low by a re-register."""
        self.push("s1", 1, [valid("coder", model="m1", effort="high")])
        self.assertEqual(self.start("coder").status_code, 200)
        spawn = self.spawns("coder")[-1]
        self.execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, spawn_spec_id, "
                     "spawn_request_id, session_handle, started_at, last_seen) VALUES ('sess-1', 'coder', ?, "
                     "'claude-code', 'managed-warm', 'running', ?, ?, 'h1', 'now', 'now')",
                     (A["env"], spec_link(spawn), spawn["id"]))
        self.execute("INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, runtime, bridge_id, command, "
                     "status, output, error, cols, rows, created_at, updated_at) VALUES ('term-1', 'coder', 'sess-1', ?, "
                     "'claude-code', ?, 'claude-aify', 'attached', '', '', 80, 24, 'now', 'now')", (A["env"], A["bridge"]))
        self.client.post("/api/v1/agents", json={
            "agentId": "coder", "role": "coder", "runtime": "claude-code", "sessionMode": "managed",
            "model": "rogue", "runtimeConfig": {"effort": "low"}}).raise_for_status()
        return spawn

    def test_a_bound_launch_takes_its_spec_from_its_request_not_the_sessions_own_link(self):
        """Review of 272df43f: with the session's nullable spec link empty, the launch fell back to the record,
        which any key holder can rewrite. The request's spec link is NOT NULL and is where the binding is read."""
        for label, link in (("no link", lambda spawn: None), ("empty link", lambda spawn: "")):
            with self.subTest(label):
                self.setUp()
                self.bound_session_with_a_rogue_record(link)
                launch = self.client.get("/api/v1/terminals/term-1/launch")
                self.assertEqual(launch.status_code, 200, launch.text)
                env = launch.json()["launch"]["env"]
                self.assertEqual((env["AIFY_MANAGED_MODEL"], env["AIFY_MANAGED_EFFORT"]), ("m1", "high"))

    def test_a_bound_launch_whose_spec_cannot_be_read_is_refused(self):
        spawn = self.bound_session_with_a_rogue_record(lambda spawn: spawn["spec_id"])
        self.execute("UPDATE spawn_requests SET spawn_spec_id = 'no-such-spec' WHERE id = ?", (spawn["id"],))
        launch = self.client.get("/api/v1/terminals/term-1/launch")
        self.assertEqual(launch.status_code, 409, launch.text)
        self.assertIn("'s start was built from a definition, and its spawn spec cannot be read; start the agent again",
                      launch.json()["detail"])
        self.assertNotIn("rogue", launch.text)

    def test_a_definition_that_leaves_model_and_effort_to_the_harness_is_not_filled_from_the_record(self):
        """An empty model or effort leaves the harness to choose (spec_columns). The record's
        runtimeConfig.model and legacy `thinking` are what the launch's readers fall back to, so they must not
        fill the gap either."""
        self.push("s1", 1, [valid("coder")])
        self.assertEqual(self.start("coder").status_code, 200)
        spawn = self.spawns("coder")[-1]
        self.execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode, status, spawn_spec_id, "
                     "spawn_request_id, session_handle, started_at, last_seen) VALUES ('sess-1', 'coder', ?, "
                     "'claude-code', 'managed-warm', 'running', ?, ?, 'h1', 'now', 'now')", (A["env"], spawn["spec_id"], spawn["id"]))
        self.execute("INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, runtime, bridge_id, command, "
                     "status, output, error, cols, rows, created_at, updated_at) VALUES ('term-1', 'coder', 'sess-1', ?, "
                     "'claude-code', ?, 'claude-aify', 'attached', '', '', 80, 24, 'now', 'now')", (A["env"], A["bridge"]))
        self.execute("UPDATE agents SET model = '', runtime_config = ? WHERE id = 'coder'",
                     (json.dumps({"model": "rogue", "thinking": "low", "usageSource": "kept"}),))
        env = self.client.get("/api/v1/terminals/term-1/launch").json()["launch"]["env"]
        self.assertEqual((env.get("AIFY_MANAGED_MODEL", ""), env.get("AIFY_MANAGED_EFFORT", "")), ("", ""))

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
