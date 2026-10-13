"""A managed agent whose workspace its environment will not start reads misconfigured, not available (0.9.2 fix C).

Steven, 2026-10-13: "should not be available if env does not allow running". aify-env starts work only below the
folder it was started in and advertises that as `advertisedCwdRoots`; dashboard-manager's workspace was outside it,
read `available`, and every start was refused.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess

import pytest

from service.api_core.workspace import workspace_within_roots
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import ENV_REPOS

#: (workspace, roots, windows) specimens, judged by both implementations.
SPECIMENS = [
    ("C:/Docker/aify-dashboard", ["C:/Docker/aify-comms"], True),
    ("C:/Docker/aify-comms/sub", ["C:/Docker/aify-comms"], True),
    ("c:\\docker\\AIFY-COMMS", ["C:/Docker/aify-comms/"], True),
    ("C:/Docker", ["C:\\"], True),
    ("/home/Bob", ["/home/bob"], False),
    ("/home/bob", ["/home/bo"], False),
    ("/home/bob/x", ["/home/bob"], False),
    ("/anything", ["/"], False),
    ("", ["/"], False),
]


def test_the_containment_rule_is_aify_envs_specimen_for_specimen():
    repo = next((r for r in ENV_REPOS if (r / "lib" / "workspace-roots.mjs").is_file()), None)
    if repo is None or shutil.which("node") is None:
        pytest.skip("no aify-env checkout with lib/workspace-roots.mjs, or no node")
    module = (repo / "lib" / "workspace-roots.mjs").as_uri()
    script = (f"import({json.dumps(module)}).then(m => console.log(JSON.stringify(JSON.parse(process.argv[1])"
              ".map(([w, r, windows]) => m.workspaceWithinRoots(w, r, { windows })))))")
    run = subprocess.run(["node", "-e", script, json.dumps(SPECIMENS)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    theirs = json.loads(run.stdout)
    ours = [workspace_within_roots(w, r, windows=windows) for w, r, windows in SPECIMENS]
    assert ours == theirs
    assert True in ours and False in ours, "the specimens must exercise both answers"


class AnAgentItsEnvironmentCannotStartIsNotAvailable(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": "win32:host:default", "machineId": "win32:host", "os": "win32", "kind": "win32",
            "bridgeId": "env-bridge", "cwdRoots": ["C:/Docker/aify-comms"], "runtimes": [],
            "metadata": {}}).raise_for_status()  # recorded as metadata.advertisedCwdRoots, as aify-env's are
        db = sqlite3.connect(self._db_path)
        try:
            for agent_id, cwd in (("outside", "C:/Docker/aify-dashboard"), ("inside", "C:/Docker/aify-comms/x")):
                db.execute("INSERT INTO agents (id, role, name, runtime, session_mode, cwd, machine_id, runtime_state, "
                           "registered_at, last_seen) VALUES (?, 'coder', ?, 'claude-code', 'managed', ?, 'win32:host', "
                           "?, '2026-10-13T00:00:00Z', '2026-10-13T00:00:00Z')",
                           (agent_id, agent_id, cwd, json.dumps({"environmentId": "win32:host:default"})))
                # Defined on that host: aify-env starts only definitions, and judges the definition's workspace.
                db.execute("INSERT INTO agent_definitions (agent_id, machine_id, store_id, incarnation, revision, "
                           "definition_digest, body, available, unavailable_reason, host_state, host_problems, "
                           "updated_at) VALUES (?, 'win32:host', 's', 1, 1, 'd', ?, 1, '', 'valid', '[]', "
                           "'2026-10-13T00:00:00Z')", (agent_id, json.dumps({"mode": "managed", "workspace": cwd})))
            db.commit()
        finally:
            db.close()

    def status(self, agent_id):
        agent = self.client.get("/api/v1/agents").json()["agents"][agent_id]
        return agent["status"], agent.get("statusNote") or ""

    def test_a_workspace_outside_the_advertised_folders_is_not_available_and_says_why(self):
        # `misconfigured` is the engine's word for an identity no start can reach until a human fixes it.
        status, note = self.status("outside")
        self.assertEqual(status, "misconfigured")
        self.assertIn("outside the folder aify-env was started in (C:/Docker/aify-comms)", note)

    def test_an_undefined_agent_is_not_judged_by_its_record_cwd(self):
        # aify-env never starts it, for its own reason; its record's cwd is no definition's workspace.
        db = sqlite3.connect(self._db_path)
        try:
            db.execute("DELETE FROM agent_definitions WHERE agent_id = 'outside'")
            db.commit()
        finally:
            db.close()
        self.assertNotIn("outside the folder", self.status("outside")[1])

    def test_one_inside_them_stays_available(self):
        status, note = self.status("inside")
        self.assertEqual(status, "available")
        self.assertNotIn("outside the folder", note)
