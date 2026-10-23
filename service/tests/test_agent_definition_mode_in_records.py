"""The agent API carries requested mode without rewriting the registered running mode.

Existing definition-push tests prove storage and ownership. These regressions cover
what the list and detail actually return when those two modes disagree.
"""
import sqlite3
from contextlib import closing

from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define


ENV = {"environment_id": "win32:task9-test:default", "machine_id": "win32:task9-test", "bridge_id": "task9-test-bridge"}


class DefinedModeInAgentRecords(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV["environment_id"], "machineId": ENV["machine_id"],
            "os": "win32", "kind": "win32", "bridgeId": ENV["bridge_id"],
            "cwdRoots": ["/workspace"], "runtimes": [], "metadata": {},
        }).raise_for_status()

    def assert_modes(self, requested, registered):
        define(self, "mode-test", mode=requested, **ENV)
        # An existing resident need not re-register when its definition changes.
        with closing(sqlite3.connect(self._db_path)) as db:
            db.execute("UPDATE agents SET session_mode = ? WHERE id = 'mode-test'", (registered,))
            db.commit()
        detail = self.client.get("/api/v1/agents/mode-test")
        self.assertEqual(detail.status_code, 200, detail.text)
        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        for agent in [detail.json()["agent"], listed.json()["agents"]["mode-test"]]:
            self.assertEqual(agent["definition"]["state"], "defined", "the real push established ownership")
            self.assertEqual(agent["sessionMode"], registered, "a read must not rewrite the running mode")
            self.assertEqual(agent["definition"].get("mode"), requested, "the definition mode was lost at serialization")
        with closing(sqlite3.connect(self._db_path)) as db:
            stored = db.execute("SELECT session_mode FROM agents WHERE id = 'mode-test'").fetchone()[0]
        self.assertEqual(stored, registered, "projecting the next start changed the registered mode")

    def test_requested_managed_mode_survives_a_resident_registration(self):
        self.assert_modes("managed", "resident")

    def test_requested_resident_mode_survives_a_managed_registration(self):
        self.assert_modes("resident", "managed")
