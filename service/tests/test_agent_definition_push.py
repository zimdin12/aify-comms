"""A host's agent-definition snapshot, pushed to the service (P0 C3, C5's descriptive fields, C6).

Each witness drives the real route and then reads `agent_definitions` (membership and owner) and the
`agents` row, not only the HTTP status, as C3 requires. The canonical form is held to aify-env's own
golden vectors, read from its checkout.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path

from service.api_core.definition_snapshot import (
    canonical, definition_digest, push_order, snapshot_digest,
)
from service.tests._base import FastApiTestCase

ROOT = Path(__file__).resolve().parents[2]
ENV_REPOS = tuple(Path(p) for p in filter(None, [os.environ.get("AIFY_ENV_REPO")])) + (
    ROOT.parent / "aify-env-next", ROOT.parent / "aify-env",
    Path.home() / "projects" / "aify-env-next", Path.home() / "projects" / "aify-env")
FIXTURE = Path("tests") / "fixtures" / "agent-definitions" / "cases.json"

A = {"env": "win32:host-a:default", "machine": "win32:host-a", "bridge": "bridge-a"}
B = {"env": "win32:host-b:default", "machine": "win32:host-b", "bridge": "bridge-b"}


def agent(agent_id, **over):
    base = {"id": agent_id, "name": agent_id, "role": "coder", "harness": "claude", "mode": "managed",
            "workspace": "/work", "model": "", "effort": "", "instructions": "", "env": {}, "herdrSpace": True}
    base.update(over)
    return base


def valid(agent_id, incarnation=1, revision=1, available=True, **over):
    definition = agent(agent_id, **over)
    entry = {"id": agent_id, "state": "valid", "incarnation": incarnation, "revision": revision,
             "definitionDigest": definition_digest(definition), "definition": definition, "available": available}
    if not available:
        entry["unavailableReason"] = "harness-not-installed"
    return entry


def invalid(agent_id, *problems):
    return {"id": agent_id, "state": "invalid", "problems": list(problems) or ["agent: missing"]}


class TheCanonicalForm(unittest.TestCase):
    def test_aify_envs_golden_vectors(self):
        fixture = next((repo / FIXTURE for repo in ENV_REPOS if (repo / FIXTURE).is_file()), None)
        if fixture is None:
            self.skipTest("no aify-env checkout with the agent-definition fixture; set AIFY_ENV_REPO")
        cases = json.loads(fixture.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases["snapshotVectors"]), 3, "control: the vectors were read")
        for vector in cases["snapshotVectors"]:
            self.assertEqual(snapshot_digest(vector["entries"]), vector["sha256"], vector["name"])
        for vector in cases["agentVectors"]:
            self.assertEqual(definition_digest(vector["agent"]), vector["sha256"], vector["name"])

    def test_a_lone_surrogate_is_escaped_as_javascript_escapes_it(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not on PATH")
        text = "a\ud800b"
        out = subprocess.run([node, "-e", "process.stdout.write(JSON.stringify(JSON.parse(process.argv[1])))",
                              json.dumps(text)], capture_output=True, text=True, check=True).stdout
        self.assertEqual(canonical(text), out)

    def test_the_same_state_in_another_order_has_the_same_digest(self):
        entries = [valid("zeta"), invalid("Alpha", "b", "a"), valid("beta", available=False)]
        self.assertEqual(snapshot_digest(entries), snapshot_digest(list(reversed(entries))))

    def test_the_ordering_table(self):
        current = {"store_id": "s1", "revision": 2, "snapshot_digest": "d2"}
        self.assertEqual(push_order(None, set(), "s1", 1, "d"), "apply")
        self.assertEqual(push_order(current, set(), "s1", 3, "d3"), "apply")
        self.assertEqual(push_order(current, set(), "s1", 2, "d2"), "replay")
        self.assertEqual(push_order(current, set(), "s1", 2, "other"), "conflict")
        self.assertEqual(push_order(current, set(), "s1", 1, "d1"), "stale")
        self.assertEqual(push_order(current, set(), "s2", 1, "d"), "new-store")
        self.assertEqual(push_order(current, {"s0"}, "s0", 9, "d"), "retired")


class AHostPushesItsDefinitions(FastApiTestCase):
    DB_NAME = "aify-test-definition-push.db"

    def setUp(self):
        super().setUp()
        for host in (A, B):
            beat = self.client.post("/api/v1/environments/heartbeat", json={
                "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32",
                "bridgeId": host["bridge"], "cwdRoots": ["/work"], "runtimes": [], "metadata": {}})
            self.assertEqual(beat.status_code, 200, beat.text)

    def push(self, store, revision, entries, host=A, **override):
        body = {"bridgeId": host["bridge"], "machineId": host["machine"], "storeId": store, "revision": revision,
                "snapshotDigest": snapshot_digest(entries), "entries": entries}
        body.update(override)
        return self.client.put(f"/api/v1/environments/{host['env']}/agent-definitions", json=body)

    def rows(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def owners(self) -> dict:
        return {r["agent_id"]: r["machine_id"] for r in self.rows("SELECT agent_id, machine_id FROM agent_definitions")}

    def state(self, agent_id):
        return self.rows("SELECT definition_state, role, name FROM agents WHERE id = ?", (agent_id,))

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_new_id_is_defined_owned_and_listed(self):
        result = self.ok(self.push("s1", 1, [valid("coder", role="reviewer", name="The Coder")]))
        self.assertEqual(result["applied"], ["coder"])
        self.assertEqual(self.owners(), {"coder": A["machine"]})
        self.assertEqual(self.state("coder"), [{"definition_state": "defined", "role": "reviewer", "name": "The Coder"}])
        self.ok(self.push("s1", 2, [valid("coder"), valid("other", harness="codex")]))
        runtimes = {r["id"]: r["runtime"] for r in self.rows("SELECT id, runtime FROM agents WHERE id IN ('coder', 'other')")}
        self.assertEqual(runtimes, {"coder": "claude-code", "other": "codex"}, "filed under this service's runtime names")

    def test_reversed_delivery_keeps_the_newer_state(self):
        self.ok(self.push("s1", 2, [valid("coder"), valid("reviewer")]))
        late = self.push("s1", 1, [valid("coder")])
        self.assertEqual(late.status_code, 409, late.text)
        self.assertEqual(late.json()["detail"], "stale: store s1 is already at revision 2")
        self.assertEqual(set(self.owners()), {"coder", "reviewer"}, "the stale push withdrew nothing")

    def test_a_replay_changes_nothing_and_a_conflict_applies_nothing(self):
        self.ok(self.push("s1", 1, [valid("coder")]))
        self.assertEqual(self.ok(self.push("s1", 1, [valid("coder")]))["outcome"], "replay")
        conflict = self.push("s1", 1, [valid("coder"), valid("other")])
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["detail"],
                         "conflict: store s1 revision 1 was already applied with another digest; nothing applied")
        self.assertEqual(set(self.owners()), {"coder"})

    def test_a_stale_retry_after_a_removal_leaves_it_withdrawn(self):
        self.ok(self.push("s1", 1, [valid("coder"), valid("reviewer")]))
        self.assertEqual(self.ok(self.push("s1", 2, [valid("coder")]))["withdrawn"], ["reviewer"])
        self.assertEqual(self.push("s1", 1, [valid("coder"), valid("reviewer")]).status_code, 409)
        self.assertEqual(set(self.owners()), {"coder"})
        self.assertEqual(self.state("reviewer")[0]["definition_state"], "withdrawn", "the agent row stays, withdrawn")
        self.ok(self.push("s1", 3, [valid("coder"), valid("reviewer", incarnation=2)]))
        self.assertEqual(self.state("reviewer")[0]["definition_state"], "defined", "defined again")

    def test_a_replaced_store_is_retired_for_good_and_its_late_push_names_the_replacement(self):
        self.ok(self.push("store-a", 1, [valid("coder")]))
        self.ok(self.push("store-b", 1, [valid("coder"), valid("reviewer")]))
        late = self.push("store-a", 2, [valid("coder")])
        self.assertEqual(late.status_code, 409, late.text)
        self.assertIn("retired on win32:host-a by store store-b", late.json()["detail"])
        self.assertTrue(late.json()["detail"].endswith(": two stores claim machine win32:host-a"), late.text)
        self.assertEqual(set(self.owners()), {"coder", "reviewer"})
        [retired] = self.rows("SELECT store_id, retired_by, refused_count FROM definition_stores_retired")
        self.assertEqual(retired, {"store_id": "store-a", "retired_by": "store-b", "refused_count": 1})

    def test_a_replaced_publisher_is_refused_with_the_current_claimer(self):
        stale = self.push("s1", 1, [valid("coder")], bridgeId="bridge-old")
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"],
                         "definition push refused: not the current claimer of this environment (that is bridge-a)")
        self.assertEqual(self.owners(), {})

    def test_the_fence_fails_closed_and_checks_the_machine(self):
        wrong_machine = self.push("s1", 1, [valid("coder")], machineId="win32:host-b")
        self.assertEqual(wrong_machine.status_code, 409, wrong_machine.text)
        self.assertIn("machineId does not match this environment's machine (win32:host-a)", wrong_machine.text)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("UPDATE environments SET bridge_id = '' WHERE id = ?", (A["env"],))
        conn.commit()
        conn.close()
        unclaimed = self.push("s1", 1, [valid("coder")])
        self.assertEqual(unclaimed.status_code, 409, unclaimed.text)
        self.assertEqual(unclaimed.json()["detail"],
                         "definition push refused: this environment has no accepted claimer yet; beat first")
        self.assertEqual(self.owners(), {})

    def test_availability_changes_are_ordered_revisions_and_never_a_withdrawal(self):
        for revision, available in ((1, True), (2, False), (3, True)):
            result = self.ok(self.push("s1", revision, [valid("coder", available=available)]))
            self.assertEqual((result["applied"], result["withdrawn"]), (["coder"], []))
            [row] = self.rows("SELECT available, unavailable_reason FROM agent_definitions")
            self.assertEqual(bool(row["available"]), available)

    def test_an_invalid_file_keeps_the_last_good_definition_until_it_is_repaired(self):
        self.ok(self.push("s1", 1, [valid("coder", role="reviewer")]))
        result = self.ok(self.push("s1", 2, [invalid("coder", "agent.role: type")]))
        self.assertEqual((result["kept"], result["withdrawn"]), (["coder"], []))
        [row] = self.rows("SELECT host_state, host_problems, body FROM agent_definitions")
        self.assertEqual((row["host_state"], json.loads(row["host_problems"])), ("invalid", ["agent.role: type"]))
        self.assertEqual(json.loads(row["body"])["role"], "reviewer", "the last good body stays")
        self.ok(self.push("s1", 3, [valid("coder", revision=2, role="lead")]))
        [row] = self.rows("SELECT host_state, body FROM agent_definitions")
        self.assertEqual((row["host_state"], json.loads(row["body"])["role"]), ("valid", "lead"))

    def test_an_intentional_empty_snapshot_withdraws_everything_and_removes_nothing(self):
        self.ok(self.push("s1", 1, [valid("coder"), valid("reviewer")]))
        result = self.ok(self.push("s1", 2, []))
        self.assertEqual(sorted(result["withdrawn"]), ["coder", "reviewer"])
        self.assertEqual(self.owners(), {})
        self.assertEqual(len(self.rows("SELECT id FROM agents WHERE id IN ('coder', 'reviewer')")), 2)

    def test_an_id_another_machine_owns_is_refused_for_that_id_only(self):
        self.ok(self.push("s1", 1, [valid("coder")]))
        result = self.ok(self.push("t1", 1, [valid("coder"), valid("helper")], host=B))
        self.assertEqual(result["refused"], [{"id": "coder", "reason": "defined on win32:host-a"}])
        self.assertEqual(self.owners(), {"coder": A["machine"], "helper": B["machine"]})
        self.assertEqual(self.ok(self.push("t1", 2, [], host=B))["withdrawn"], ["helper"])
        self.assertEqual(self.owners(), {"coder": A["machine"]}, "a push withdraws only its own machine's ids")

    def test_an_existing_agents_effective_columns_are_left_to_the_paths_that_write_them(self):
        registered = self.client.post("/api/v1/agents", json={
            "agentId": "coder", "role": "coder", "runtime": "codex", "sessionMode": "managed", "cwd": "/live"})
        self.assertEqual(registered.status_code, 200, registered.text)
        self.ok(self.push("s1", 1, [valid("coder", role="reviewer", workspace="/desired", harness="claude")]))
        [row] = self.rows("SELECT role, runtime, cwd FROM agents WHERE id = 'coder'")
        self.assertEqual(row, {"role": "reviewer", "runtime": "codex", "cwd": "/live"},
                         "descriptive applied at once; effective is what the live run is")

    def test_a_malformed_push_is_refused_and_applies_nothing(self):
        bad_digest = self.push("s1", 1, [valid("coder")], snapshotDigest="0" * 64)
        self.assertEqual(bad_digest.status_code, 422, bad_digest.text)
        lying = valid("coder")
        lying["definition"]["role"] = "changed after digesting"
        self.assertEqual(self.push("s1", 1, [lying]).status_code, 422)
        self.assertEqual(self.push("s1", 1, [valid("coder"), valid("coder")]).status_code, 422)
        self.assertEqual(self.push("s1", 0, [valid("coder")]).status_code, 422)
        self.assertEqual(self.push("s1", True, [valid("coder")]).status_code, 422, "a boolean is not a revision")
        self.assertEqual(self.push("s1", 1, [valid("coder", revision=True)]).status_code, 422)
        no_problem = invalid("coder")
        no_problem["problems"] = []
        self.assertEqual(self.push("s1", 1, [no_problem]).status_code, 422, "an invalid entry names its problem")
        unexplained = valid("coder", available=False)
        unexplained["unavailableReason"] = "busy"
        self.assertEqual(self.push("s1", 1, [unexplained]).status_code, 422, "an unavailable entry says why, as the host words it")
        elsewhere = valid("coder")
        elsewhere["definition"]["id"] = "someone-else"
        elsewhere["definitionDigest"] = definition_digest(elsewhere["definition"])
        self.assertEqual(self.push("s1", 1, [elsewhere]).status_code, 422, "a definition names its own id")
        self.assertEqual(self.owners(), {})

    def test_release_and_reset_are_the_operators(self):
        self.ok(self.push("s1", 1, [valid("coder")]))
        refused = self.client.post("/api/v1/agent-definitions/coder/release", json={"requestedBy": "some-agent"})
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertIn("only the operator may release an agent definition", refused.text)
        ghost = self.client.post("/api/v1/agent-definitions/ghost/release", json={"requestedBy": "dashboard"})
        self.assertEqual((ghost.status_code, ghost.json()["detail"]), (404, '"ghost" has no definition to release'))
        self.assertEqual(self.owners(), {"coder": A["machine"]})
        released = self.client.post("/api/v1/agent-definitions/coder/release", json={"requestedBy": "dashboard"})
        self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(self.state("coder")[0]["definition_state"], "withdrawn", "released: no host defines it")
        self.assertEqual(self.ok(self.push("t1", 1, [valid("coder")], host=B))["applied"], ["coder"])
        self.assertEqual(self.owners(), {"coder": B["machine"]})

        self.ok(self.push("store-b", 1, []))
        self.assertEqual(self.push("s1", 2, []).status_code, 409, "control: s1 is retired")
        self.assertEqual(self.client.post(f"/api/v1/environments/{A['env']}/definition-store/reset",
                                          json={"requestedBy": "some-agent"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/environments/nowhere/definition-store/reset",
                                          json={"requestedBy": "dashboard"}).status_code, 404)
        reset = self.client.post(f"/api/v1/environments/{A['env']}/definition-store/reset", json={"requestedBy": "dashboard"})
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(self.ok(self.push("s1", 2, []))["outcome"], "applied", "returned to the older store")

    def test_the_agent_list_and_detail_carry_the_definition(self):
        """What dashboard-manager mirrors (D13): state, owner machine, store, lifetime and revision."""
        self.client.post("/api/v1/agents", json={"agentId": "plain", "role": "coder"}).raise_for_status()
        self.ok(self.push("s1", 1, [valid("coder", incarnation=2, revision=5), valid("gone")]))
        self.ok(self.push("s1", 2, [valid("coder", incarnation=2, revision=5)]))
        detail = self.ok(self.client.get("/api/v1/agents/coder"))
        agent = detail.get("agent") or detail
        self.assertEqual(agent["definitionState"], "defined")
        self.assertEqual(
            {k: agent["definition"][k] for k in ("state", "ownerMachineId", "storeId", "incarnation", "revision", "hostState", "available")},
            {"state": "defined", "ownerMachineId": A["machine"], "storeId": "s1", "incarnation": 2, "revision": 5,
             "hostState": "valid", "available": True})
        listed = self.ok(self.client.get("/api/v1/agents"))["agents"]
        self.assertEqual(listed["coder"]["definition"]["revision"], 5)
        self.assertEqual((listed["gone"]["definition"]["state"], listed["gone"]["definition"]["ownerMachineId"]),
                         ("withdrawn", None))
        self.assertEqual((listed["plain"]["definitionState"], listed["plain"]["definition"]["state"]), ("", ""),
                         "never defined: no state, nothing owned")

    def test_an_operator_name_or_a_removed_id_is_refused_for_that_id_only(self):
        """A push creates agent rows, so it refuses what registration refuses, entry by entry."""
        self.client.post("/api/v1/agents", json={"agentId": "retired-one", "role": "coder"}).raise_for_status()
        removed = self.client.request("DELETE", "/api/v1/agents/retired-one", json={"requestedBy": "dashboard"})
        self.assertEqual(removed.status_code, 200, removed.text)
        result = self.ok(self.push("s1", 1, [valid("dashboard"), valid("retired-one"), valid("coder")]))
        self.assertEqual(result["refused"], [
            {"id": "dashboard", "reason": "reserved for the operator and cannot be an agent id"},
            {"id": "retired-one", "reason": "was intentionally removed before; clear that ID before reusing it"}])
        self.assertEqual((result["applied"], self.owners()), (["coder"], {"coder": A["machine"]}))
        self.assertEqual(self.rows("SELECT id FROM agents WHERE id IN ('dashboard', 'retired-one')"), [])

    def test_a_defined_agent_is_renamed_on_its_host_not_here(self):
        self.ok(self.push("s1", 1, [valid("coder")]))
        renamed = self.client.post("/api/v1/agents/coder/rename", json={"newAgentId": "lead", "requestedBy": "dashboard"})
        self.assertEqual(renamed.status_code, 409, renamed.text)
        how = "; rename it there: `aify-env agents set` the new id, then `aify-env agents remove` this one"
        self.assertEqual(renamed.json()["detail"], 'Agent "coder" is defined on win32:host-a' + how)
        self.assertEqual([r["id"] for r in self.rows("SELECT id FROM agents WHERE id IN ('coder', 'lead')")], ["coder"])
        self.client.post("/api/v1/agents", json={"agentId": "plain", "role": "coder"}).raise_for_status()
        control = self.client.post("/api/v1/agents/plain/rename", json={"newAgentId": "plain2", "requestedBy": "dashboard"})
        self.assertEqual(control.status_code, 200, "control: an undefined agent still renames")
