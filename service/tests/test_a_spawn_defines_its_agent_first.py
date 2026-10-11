"""D8: a spawn of a new id asks its host to define it, and the agent is spawned from what the host defined.

Every obligation is checked against the rows, not only the HTTP answer: the creation the host is asked for,
the spawn queued once the definition is published here (whichever of the host's report and push comes
second), the brief delivered once its worker attaches, and the refusals for an agent no host defines.
"""
from __future__ import annotations

import json
import sqlite3

from service.api_core.definition_schema import agent_problems
from service.api_core.definition_start import StartRefused, insert_spawn_request
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, snapshot_digest, valid

HOST = {"bridgeId": A["bridge"], "machineId": A["machine"]}


class ASpawnDefinesItsAgentFirst(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "linux", "kind": "linux", "bridgeId": A["bridge"],
            "cwdRoots": ["/work"], "runtimes": [{"runtime": "claude-code", "available": True}],
            "metadata": {"terminal": True, "pty": True, "terminalRuntimes": ["claude-code"]}}).raise_for_status()
        self.push([])

    def sql(self, query, params=()):
        db = sqlite3.connect(self._db_path)
        try:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute(query, params)]
            db.commit()
            return rows
        finally:
            db.close()

    def push(self, entries, revision=1):
        self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            **HOST, "storeId": "s1", "revision": revision, "snapshotDigest": snapshot_digest(entries),
            "entries": entries}).raise_for_status()

    def spawn(self, agent_id="newbie", **over):
        return self.client.post("/api/v1/spawn-requests", json={
            "environmentId": A["env"], "agentId": agent_id, "runtime": "claude-code", "workspace": "/work/p",
            "role": "reviewer", "initialMessage": "review the index", "createdBy": "manager", **over})

    def created(self):
        answer = self.spawn()
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()["definitionRequest"]

    def claim(self):
        answer = self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/claim", json=HOST)
        answer.raise_for_status()
        return answer.json()["requests"]

    def report(self, request_id, incarnation=1):
        answer = self.client.post(f"/api/v1/environments/{A['env']}/definition-requests/{request_id}/result",
                                  json={**HOST, "status": "done", "resultIncarnation": incarnation, "resultRevision": 1})
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()["request"]

    def defined(self, incarnation=1, revision=2):
        self.push([valid("newbie", incarnation=incarnation, revision=1, role="reviewer", workspace="/work/p")],
                  revision=revision)

    def consequence(self, request_id):
        return self.sql("SELECT consequence FROM definition_requests WHERE id = ?", (request_id,))[0]["consequence"]

    def test_a_new_id_is_a_creation_of_the_whole_agent_and_nothing_starts_yet(self):
        request = self.created()
        self.assertEqual((request["expectedIncarnation"], request["expectedRevision"], request["status"]), (0, 0, "pending"))
        self.assertEqual(agent_problems({**request["patch"], "id": "newbie"}, "newbie"), [], "a whole C1 agent")
        self.assertEqual({k: request["patch"][k] for k in ("role", "harness", "mode", "workspace")},
                         {"role": "reviewer", "harness": "claude", "mode": "managed", "workspace": "/work/p"})
        self.assertEqual(request["requestedBy"], "manager")
        self.assertEqual([r["id"] for r in self.claim()], [request["id"]], "the host is handed the creation")
        for table in ("spawn_requests", "agent_lifecycle_requests"):
            self.assertEqual(self.sql(f"SELECT * FROM {table}"), [], table)
        self.assertEqual(self.sql("SELECT id FROM agents WHERE id = 'newbie'"), [], "no agent before its definition")

    def test_the_spawn_is_queued_once_the_host_has_defined_it_and_published_it(self):
        request = self.created()
        self.claim()
        self.report(request["id"])
        self.assertEqual(self.consequence(request["id"]), "spawn pending")
        self.assertEqual(self.sql("SELECT * FROM agent_lifecycle_requests"), [], "not before the definition is here")
        self.defined()
        spawned = self.sql("SELECT id, action, requested_by, expected_incarnation, brief FROM agent_lifecycle_requests")
        self.assertEqual(len(spawned), 1)
        self.assertEqual((spawned[0]["action"], spawned[0]["requested_by"], spawned[0]["expected_incarnation"]),
                         ("spawn", "manager", 1))
        self.assertEqual(json.loads(spawned[0]["brief"])["body"], "review the index")
        self.assertEqual(self.consequence(request["id"]), f"spawn queued {spawned[0]['id']}")
        self.defined(revision=3)
        self.report(request["id"])
        self.assertEqual(len(self.sql("SELECT id FROM agent_lifecycle_requests")), 1, "a repeat queues nothing more")

    def test_published_before_the_report_it_is_spawned_by_the_report(self):
        request = self.created()
        self.claim()
        self.defined()
        self.assertEqual(self.sql("SELECT * FROM agent_lifecycle_requests"), [], "not before the host says it is done")
        self.report(request["id"])
        self.assertEqual([r["action"] for r in self.sql("SELECT action FROM agent_lifecycle_requests")], ["spawn"])

    def test_a_definition_at_another_lifetime_spawns_nothing_and_says_so(self):
        request = self.created()
        self.claim()
        self.report(request["id"], incarnation=1)
        self.defined(incarnation=2)
        self.assertEqual(self.sql("SELECT * FROM agent_lifecycle_requests"), [])
        self.assertTrue(self.consequence(request["id"]).startswith("nothing spawned: newbie is now defined by"))

    def test_the_brief_is_its_first_message_once_the_worker_attaches_and_only_once(self):
        request = self.created()
        self.claim()
        self.report(request["id"])
        self.defined()
        self.client.post(f"/api/v1/environments/{A['env']}/lifecycle-requests/claim", json=HOST).raise_for_status()
        spawn_id = self.sql("SELECT id FROM agent_lifecycle_requests")[0]["id"]
        base = f"/api/v1/environments/{A['env']}/lifecycle-requests/{spawn_id}"
        launch = self.client.post(f"{base}/launch", json=HOST)
        self.assertEqual(launch.status_code, 200, launch.text)
        self.assertEqual(self.sql("SELECT * FROM messages"), [], "not before the worker is there")
        attachment = {**HOST, "terminalId": launch.json()["launch"]["terminalId"], "handle": "h", "processId": 7,
                      "lifetime": "life-1"}
        for _ in range(2):
            self.assertEqual(self.client.post(f"{base}/attachment", json=attachment).status_code, 200)
        sent = self.sql("SELECT from_agent, to_agent, body FROM messages")
        self.assertEqual(sent, [{"from_agent": "manager", "to_agent": "newbie", "body": "review the index"}])
        self.assertEqual(len(self.sql("SELECT id FROM dispatch_runs WHERE target_agent = 'newbie'")), 1)

    def test_an_existing_agent_no_host_defines_is_refused_and_pointed_at_import(self):
        self.client.post("/api/v1/agents", json={"agentId": "old", "role": "coder", "runtime": "claude-code",
                                                  "sessionMode": "managed"}).raise_for_status()
        answer = self.spawn("old")
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(answer.json()["detail"], 'Agent "old" already exists and no host defines it; define it on its '
                                                  'host (`aify-env agents import --write`), then start it')
        self.assertEqual(self.sql("SELECT * FROM definition_requests"), [])

    def test_what_a_creation_cannot_be_is_refused_before_anything_is_queued(self):
        bad = self.spawn(name="bad\x01name")
        self.assertEqual((bad.status_code, bad.json()["detail"]),
                         (422, '"newbie" cannot be defined as asked: agent.name: control'))
        self.assertEqual(self.sql("SELECT * FROM definition_requests"), [], "nothing queued for a refused body")
        uncarried = self.spawn(runtimeConfig={"effort": "high", "quietTimeoutMs": 0})
        self.assertEqual((uncarried.status_code, uncarried.json()["detail"]),
                         (422, "runtimeConfig: quietTimeoutMs cannot be carried; an agent definition holds a model and "
                               "an effort only"))
        self.assertEqual(self.sql("SELECT * FROM definition_requests"), [], "nothing queued for an option it would drop")
        self.created()
        again = self.spawn()
        self.assertEqual(again.status_code, 409, again.text)
        self.assertTrue(again.json()["detail"].startswith('"newbie" already has a request waiting for its host ('))

    def test_a_machine_whose_host_published_no_store_cannot_define_one(self):
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:bare:default", "machineId": "linux:bare", "os": "linux", "kind": "linux", "bridgeId": "bridge-bare",
            "cwdRoots": ["/work"], "runtimes": [{"runtime": "claude-code", "available": True}]}).raise_for_status()
        answer = self.spawn(environmentId="linux:bare:default")
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(answer.json()["detail"], 'the host on linux:bare has published no agent definitions, so it cannot '
                                                  'define "newbie"; check `aify-env doctor` there')

    def test_an_agent_no_host_defines_is_not_started(self):
        self.client.post("/api/v1/agents", json={"agentId": "old", "role": "coder", "runtime": "claude-code",
                                                  "sessionMode": "managed"}).raise_for_status()
        answer = self.client.post("/api/v1/agents/old/control", json={"action": "start", "from": "dashboard"})
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertTrue(answer.json()["detail"].startswith('no host defines "old", so it cannot be started'))
        self.assertEqual(self.sql("SELECT * FROM spawn_requests"), [])

    def test_the_guarded_insert_refuses_an_undefined_agent_whoever_calls_it(self):
        with self.assertRaisesRegex(StartRefused, 'no host defines "x"'):
            import asyncio
            asyncio.run(insert_spawn_request(None, {"agent_id": "x"}, None))
