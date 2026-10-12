"""0.9.1: a handoff into the agent's own id is its host's fresh-context restart, carrying the handoff brief.

The spawn route used to refuse it for a defined agent (0.9 D8). Each witness drives the route both callers post
to and reads the rows: the lifecycle request queued, nothing spawned or defined beside it, and the brief sent once
the restarted worker attaches.
"""
from __future__ import annotations

import json
import sqlite3

from service.api_core.handoff_restart import same_id_handoff
from service.tests._base import FastApiTestCase
from service.tests.published_state import publish
from service.tests.test_agent_definition_push import A, snapshot_digest, valid

HOST = {"bridgeId": A["bridge"], "machineId": A["machine"]}
#: What `comms_compact` sends, and what the dashboard's Compact form sends.
COMPACT_TOOL = {"compactMode": "handoff", "compactedFromAgentId": "coder", "compactedFromSessionId": "sess-1",
                "sameAgentId": True}
DASHBOARD = {"compactMode": "handoff", "continuedFromAgentId": "coder", "continuedFromSessionId": "sess-1",
             "sameAgentId": True, "splitIdentity": False}


class ASameIdHandoffRestartsFresh(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": A["env"], "machineId": A["machine"], "os": "linux", "kind": "linux", "bridgeId": A["bridge"],
            "cwdRoots": ["/work"], "runtimes": [{"runtime": "claude-code", "available": True}],
            "metadata": {"terminal": True, "pty": True, "terminalRuntimes": ["claude-code"]}}).raise_for_status()
        entries = [valid("coder")]
        self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json={
            **HOST, "storeId": "s1", "revision": 1, "snapshotDigest": snapshot_digest(entries),
            "entries": entries}).raise_for_status()
        publish(self, A["machine"], {"coder": ("L1", "idle")})

    def sql(self, query, params=()):
        db = sqlite3.connect(self._db_path)
        try:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute(query, params)]
            db.commit()
            return rows
        finally:
            db.close()

    def handoff(self, metadata, agent_id="coder", **over):
        return self.client.post("/api/v1/spawn-requests", json={
            "environmentId": A["env"], "agentId": agent_id, "runtime": "claude-code", "workspace": "/work",
            "initialMessage": "read your last 25 messages", "subject": "Handoff compact from coder",
            "createdBy": "manager", "resumePolicy": "fresh_context", "metadata": metadata, **over})

    def nothing_spawned_or_defined(self):
        self.assertEqual((self.sql("SELECT id FROM spawn_requests"), self.sql("SELECT id FROM definition_requests")),
                         ([], []))

    def test_either_callers_handoff_queues_a_fresh_restart_carrying_the_brief(self):
        for caller, metadata in (("comms_compact", COMPACT_TOOL), ("dashboard", DASHBOARD)):
            with self.subTest(caller=caller):
                self.sql("DELETE FROM agent_lifecycle_requests")
                answer = self.handoff(metadata)
                self.assertEqual(answer.status_code, 200, answer.text)
                self.assertEqual(answer.json()["lifecycleRequest"]["action"], "restart")
                [row] = self.sql("SELECT action, fresh_context, expected_lifetime, requested_by, brief "
                                 "FROM agent_lifecycle_requests")
                self.assertEqual({k: row[k] for k in ("action", "fresh_context", "expected_lifetime", "requested_by")},
                                 {"action": "restart", "fresh_context": 1, "expected_lifetime": "L1",
                                  "requested_by": "manager"})
                self.assertEqual(json.loads(row["brief"]), {"subject": "Handoff compact from coder",
                                                           "body": "read your last 25 messages", "priority": "normal"})
                self.nothing_spawned_or_defined()

    def test_the_brief_is_the_restarted_workers_first_message(self):
        self.assertEqual(self.handoff(COMPACT_TOOL).status_code, 200)
        self.client.post(f"/api/v1/environments/{A['env']}/lifecycle-requests/claim", json=HOST).raise_for_status()
        request_id = self.sql("SELECT id FROM agent_lifecycle_requests")[0]["id"]
        base = f"/api/v1/environments/{A['env']}/lifecycle-requests/{request_id}"
        launch = self.client.post(f"{base}/launch", json=HOST)
        self.assertEqual(launch.status_code, 200, launch.text)
        self.assertEqual(self.sql("SELECT * FROM messages"), [], "not before the worker is there")
        attachment = {**HOST, "terminalId": launch.json()["launch"]["terminalId"], "handle": "h", "processId": 7,
                      "lifetime": "L2"}
        self.assertEqual(self.client.post(f"{base}/attachment", json=attachment).status_code, 200)
        self.assertEqual(self.sql("SELECT from_agent, to_agent, body FROM messages"),
                         [{"from_agent": "manager", "to_agent": "coder", "body": "read your last 25 messages"}])

    def test_a_handoff_asking_for_another_runtime_is_refused_and_nothing_is_queued(self):
        answer = self.handoff(COMPACT_TOOL, runtime="codex")
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertIn("; a handoff restarts it from that definition, so change the runtime there first", answer.json()["detail"])
        self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])
        self.nothing_spawned_or_defined()

    def test_a_handoff_asking_for_another_workspace_or_environment_is_refused_and_nothing_is_queued(self):
        # Review of 0.9.1 R5: these answered 200 and restarted the agent in its definition's place anyway.
        for over, says in (({"workspace": "/elsewhere"}, "change the workspace there first"),
                           ({"environmentId": "nowhere"}, "; a handoff restarts it there, not in environment nowhere")):
            with self.subTest(over=over):
                answer = self.handoff(COMPACT_TOOL, **over)
                self.assertEqual(answer.status_code, 409, answer.text)
                self.assertIn(says, answer.json()["detail"])
                self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])
        self.assertEqual(self.handoff(COMPACT_TOOL, workspace="/WORK/").status_code, 200, "the same path spelled otherwise")

    def test_a_workspace_is_compared_the_way_its_host_reads_paths(self):
        # Review of 0.9.1 R5, second pass: `/WORK` passed for a Linux host, where it is another folder than `/work`.
        from service.api_core.handoff_restart import _same_path
        self.assertFalse(_same_path("/WORK", "/work", windows=False))
        self.assertTrue(_same_path("/work/", "/work", windows=False))
        self.assertTrue(_same_path("C:\\Work\\", "c:/work", windows=True))

    def test_a_handoff_behind_a_waiting_restart_is_refused_not_merged(self):
        """The waiting request would never deliver this brief, so it is not answered as that request."""
        waiting = self.client.post("/api/v1/agents/coder/lifecycle-requests", json={
            "requestId": "r1", "action": "restart", "requestedBy": "manager", "expectedLifetime": "L1",
            "freshContext": True})
        self.assertEqual(waiting.status_code, 200, waiting.text)
        answer = self.handoff(COMPACT_TOOL)
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual([r["id"] for r in self.sql("SELECT id FROM agent_lifecycle_requests")], ["r1"])

    def test_a_successor_with_a_new_id_is_still_defined_on_its_host(self):
        answer = self.handoff(COMPACT_TOOL, agent_id="coder-2")
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["definitionRequest"]["agentId"], "coder-2")
        self.assertEqual(self.sql("SELECT id FROM agent_lifecycle_requests"), [])

    def test_only_the_named_source_makes_it_the_same_id(self):
        self.assertFalse(same_id_handoff("coder", {"compactMode": "handoff", "sameAgentId": True}),
                         "the caller's sameAgentId claim is not read")
        self.assertFalse(same_id_handoff("coder", {"compactedFromAgentId": "coder"}), "not a handoff")
        self.assertFalse(same_id_handoff("coder", None))
