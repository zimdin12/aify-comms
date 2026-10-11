"""A defined agent's descriptive fields are its definition's, on every path that wrote them (P0 C5).

Each witness pairs the defined agent with an UNDEFINED control on the same path, because C5 changes
nothing for an agent no host defines, and a guard that also froze undefined agents would pass every
defined-agent assertion here. The live controls show the guard is not a freeze: a defined agent's
heartbeat, handle, lease and usage source still land.
"""
from __future__ import annotations

import json
import sqlite3
import threading

from service.tests._base import FastApiTestCase
from service.tests.defined_agents import cold_start, undefine
from service.tests.test_agent_definition_push import A, snapshot_digest, valid

LINUX = {"env": "linux:p3b-host:default", "machine": "linux:p3b-host", "bridge": "bridge-p3b"}


class ADefinedAgentsDescriptionIsItsDefinitions(FastApiTestCase):
    DB_NAME = "aify-test-definition-dispositions.db"

    def setUp(self):
        super().setUp()
        beat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "win32", "kind": "win32", "bridgeId": A["bridge"],
            "cwdRoots": ["/work"], "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "terminal": True, "pty": True, "terminalRuntimes": ["claude-code"], "metadata": {}})
        self.assertEqual(beat.status_code, 200, beat.text)
        self.revision = 0

    def define(self, *entries):
        self.revision += 1
        pushed = self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "s1", "revision": self.revision,
            "snapshotDigest": snapshot_digest(list(entries)), "entries": list(entries)})
        self.assertEqual(pushed.status_code, 200, pushed.text)

    def rows(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def execute(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def described(self, agent_id):
        [row] = self.rows("SELECT role, name, instructions FROM agents WHERE id = ?", (agent_id,))
        return row

    def register(self, agent_id, **over):
        body = {"agentId": agent_id, "role": "coder", "name": agent_id, "instructions": "from the worker",
                "runtime": "codex", "sessionMode": "managed", "cwd": "/live"}
        body.update(over)
        response = self.client.post("/api/v1/agents", json=body)
        self.assertEqual(response.status_code, 200, response.text)

    def test_registration_keeps_the_definitions_description_and_writes_the_effective_fields(self):
        self.define(valid("lead", role="reviewer", name="The Lead", instructions="review everything"))
        self.register("lead")
        self.assertEqual(self.described("lead"), {"role": "reviewer", "name": "The Lead", "instructions": "review everything"})
        [effective] = self.rows("SELECT runtime, cwd FROM agents WHERE id = 'lead'")
        self.assertEqual(effective, {"runtime": "codex", "cwd": "/live"}, "the effective fields are the worker's")
        self.register("plain")
        self.register("plain", role="tester", name="Plain Tester")
        self.assertEqual(self.described("plain")["role"], "tester", "control: an undefined agent's registration writes")

    def test_a_console_adopting_registration_keeps_the_definitions_description(self):
        self.define(valid("lead", role="reviewer", name="The Lead"))
        for agent_id in ("lead", "plain"):
            self.register(agent_id, sessionMode="resident")
            self.execute(
                "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, workspace,"
                " command, output, status, requested_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"term-{agent_id}", f"sess-{agent_id}", agent_id, A["env"], A["bridge"], "codex", "/w", "bash", "",
                 "attached", "dashboard", "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
            self.register(agent_id, sessionMode="resident", terminalId=f"term-{agent_id}", role="adopted", name="Adopted")
        self.assertEqual({r["status_note"] for r in self.rows("SELECT status_note FROM agents WHERE id IN ('lead', 'plain')")},
                         {"Dashboard Console PTY attached."}, "control: both registrations took the adopt branch")
        self.assertEqual((self.described("lead")["role"], self.described("lead")["name"]), ("reviewer", "The Lead"))
        self.assertEqual((self.described("plain")["role"], self.described("plain")["name"]), ("adopted", "Adopted"),
                         "control: an undefined agent's adopt branch writes")

    def test_a_spawn_queued_before_the_definition_comes_up_as_the_definition_describes(self):
        """Since D8 a spawn request is only ever queued for a defined agent (a message's cold start), so the
        spawn that reaches running with a stale description is one built from an EARLIER revision: it comes up
        as the definition now describes, not as its request said. The control is an agent whose spawn was
        queued while defined and which is undefined by the time it runs, as one in flight across the D8
        upgrade is: it comes up as its request says."""
        self.define(valid("lead", role="from-request", name="From Request", instructions="from the request"),
                    valid("plain", incarnation=2, role="from-request", name="From Request",
                          instructions="from the request"))
        for agent_id in ("lead", "plain"):
            self.assertEqual(cold_start(self, agent_id), [], agent_id)
        undefine(self, "plain")
        self.register("lead")
        self.register("plain")
        self.define(valid("lead", revision=2, role="reviewer", name="The Lead", instructions="review everything"))
        for _ in range(2):
            claim = self.client.post("/api/v1/spawn-requests/claim", json={
                "environmentId": A["env"], "bridgeId": A["bridge"], "machineId": A["machine"]})
            spawn_id = claim.json()["spawnRequest"]["id"]
            running = self.client.patch(f"/api/v1/spawn-requests/{spawn_id}", json={"status": "running", "bridgeId": A["bridge"]})
            self.assertEqual(running.status_code, 200, running.text)
        self.assertEqual(self.described("lead"), {"role": "reviewer", "name": "The Lead", "instructions": "review everything"})
        self.assertEqual(self.described("plain"), {"role": "from-request", "name": "From Request", "instructions": "from the request"},
                         "control: an undefined agent comes up as its request says")

    def test_a_defined_agent_is_not_spawned_directly(self):
        self.define(valid("lead"))
        refused = self.client.post("/api/v1/spawn-requests", json={
            "agentId": "lead", "environmentId": A["env"], "runtime": "claude-code", "workspace": "/work"})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["detail"],
                         'Agent "lead" is defined in aify-env on win32:host-a; start it, and its spawn is built from that definition')
        self.assertEqual(self.rows("SELECT id FROM spawn_requests WHERE agent_id = 'lead'"), [])
        allowed = self.client.post("/api/v1/spawn-requests", json={
            "agentId": "plain", "environmentId": A["env"], "runtime": "claude-code", "workspace": "/work"})
        self.assertEqual(allowed.status_code, 200, "control: an undefined agent still spawns")

    def test_a_spawn_racing_a_definition_is_refused_once_the_definition_commits(self):
        """Review of b5ac1de3: the refusal reads `agent_definitions` inside the spawn's write transaction,
        so a push that commits while the spawn waits is seen, and nothing is queued."""
        import service.routers.spawn_requests as spawn_route
        committed, reads, real = threading.Event(), [], spawn_route.start_binding

        async def observed(db, agent_id):
            # Whether the definition had committed when the owner was read: True only if the read waited.
            reads.append(committed.is_set())
            return await real(db, agent_id)

        spawn_route.start_binding = observed
        self.addCleanup(setattr, spawn_route, "start_binding", real)
        holder = sqlite3.connect(str(self._db_path), isolation_level=None)
        # Closed explicitly after its commit, because tearDown deletes the database before cleanups run;
        # this cleanup only covers a failure before that line.
        self.addCleanup(lambda: holder.close())
        holder.execute("BEGIN IMMEDIATE")
        outcome = {}
        worker = threading.Thread(target=lambda: outcome.update(response=self.client.post("/api/v1/spawn-requests", json={
            "agentId": "racer", "environmentId": A["env"], "runtime": "claude-code", "workspace": "/work"})))
        worker.start()
        worker.join(0.6)
        self.assertTrue(worker.is_alive(), "control: the spawn is waiting on the write lock")
        holder.execute("INSERT INTO agent_definitions (agent_id, machine_id, store_id, incarnation, revision, "
                       "definition_digest, body, available, updated_at) VALUES ('racer', ?, 's1', 1, 1, 'd', ?, 1, 'now')",
                       (A["machine"], json.dumps(valid("racer")["definition"])))
        committed.set()
        holder.execute("COMMIT")
        holder.close()
        worker.join(10)
        response = outcome["response"]
        self.assertEqual(reads, [True], "the owner was read once, and only after the lock was taken")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('Agent "racer" is defined in aify-env on win32:host-a', response.json()["detail"])
        self.assertEqual(self.rows("SELECT id FROM spawn_requests WHERE agent_id = 'racer'"), [])
        self.assertEqual(self.rows("SELECT id FROM spawn_specs WHERE agent_id = 'racer'"), [], "no spec either")

    def test_a_spawn_that_commits_first_is_queued_and_the_definition_still_applies(self):
        """The opposite order: the spawn takes the lock first and queues; the push lands after it. Since D8 what
        a spawn of a new id queues is the request for its host to define it, so the store is published first."""
        self.define()
        created = self.client.post("/api/v1/spawn-requests", json={
            "agentId": "early", "environmentId": A["env"], "runtime": "claude-code", "workspace": "/work"})
        self.assertEqual(created.status_code, 200, created.text)
        self.define(valid("early", role="reviewer"))
        self.assertEqual(len(self.rows("SELECT id FROM definition_requests WHERE agent_id = 'early'")), 1)
        self.assertEqual(self.rows("SELECT machine_id FROM agent_definitions WHERE agent_id = 'early'"),
                         [{"machine_id": A["machine"]}])

    def test_applying_the_managed_defaults_skips_a_defined_agent_and_says_so(self):
        self.define(valid("lead", model="opus", effort="high"))
        for agent_id in ("lead", "plain"):
            self.register(agent_id, runtime="claude-code", model="opus", runtimeConfig={"effort": "max"})
            self.execute("INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, model, metadata, created_at, "
                         "updated_at) VALUES (?, ?, ?, 'claude-code', 'opus', '{}', 'now', 'now')",
                         (f"spec-{agent_id}", agent_id, A["env"]))
        applied = self.client.post("/api/v1/settings/apply-managed-defaults")
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["skippedDefined"], 1)
        agents = {r["id"]: (r["model"], json.loads(r["runtime_config"] or "{}").get("effort"))
                  for r in self.rows("SELECT id, model, runtime_config FROM agents WHERE id IN ('lead', 'plain')")}
        specs = {r["agent_id"]: (r["model"], json.loads(r["metadata"] or "{}").get("runtimeConfig", {}).get("effort"))
                 for r in self.rows("SELECT agent_id, model, metadata FROM spawn_specs")}
        self.assertEqual((agents["lead"], specs["lead"]), (("opus", "max"), ("opus", None)),
                         "a defined agent and its spec keep their model and effort")
        self.assertNotIn("opus", (agents["plain"][0], specs["plain"][0]), "control: an undefined agent takes the default")
        self.assertNotEqual(agents["plain"][1], "max", "control: and its effort")

    def test_a_defined_agents_live_updates_still_land(self):
        """The guard covers the descriptive columns only: liveness, handle, lease and quota are the worker's."""
        self.define(valid("lead"))
        self.register("lead", sessionMode="resident")
        before = self.rows("SELECT last_seen FROM agents WHERE id = 'lead'")[0]["last_seen"]
        self.execute("UPDATE agents SET last_seen = '2026-01-01T00:00:00Z' WHERE id = 'lead'")
        for method, path, body in (("POST", "heartbeat", {"status": "online"}),
                                   ("PATCH", "session-handle", {"sessionHandle": "sess-live"}),
                                   ("POST", "claimer-lease", {"action": "acquire", "bridgeId": "b1"}),
                                   ("PATCH", "usage-source", {"usageSource": "anthropic"})):
            response = self.client.request(method, f"/api/v1/agents/lead/{path}", json=body)
            self.assertEqual(response.status_code, 200, f"{path}: {response.text}")
        [row] = self.rows("SELECT last_seen, session_handle, runtime_config FROM agents WHERE id = 'lead'")
        self.assertNotEqual(row["last_seen"], "2026-01-01T00:00:00Z", f"the heartbeat landed (was {before})")
        self.assertEqual(row["session_handle"], "sess-live")
        self.assertEqual(json.loads(row["runtime_config"] or "{}").get("usageSource"), "anthropic")
        self.assertEqual(len(self.rows("SELECT agent_id FROM claimer_leases WHERE agent_id = 'lead'")), 1)
