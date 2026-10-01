"""No agent can be registered, renamed or spawned under an operator name.

THE DEFECT (external review of 0.7.6, MEDIUM). With `OPERATOR_KEY` set and no key presented,
`POST /agents/attacker/rename {"newAgentId": "dashboard"}` returned 200 and rewrote the attacker's old
messages and queued runs to read as from `dashboard`, which every recipient reads without the peer
trust rule. The operator gate checks the ACTOR fields and never saw it, because the name arrived as the
agent's new id. Registering an agent called `dashboard` was the same hole (LOW).

The names are refused as agent ids outright, key or not: `to=dashboard` is the operator's own inbox,
so an agent holding the name is wrong on any host (`refuse_a_reserved_agent_id`).

WHERE IDS ARE CREATED is derived, not listed from memory: the census below fails if a module that
writes `INSERT INTO agents` appears without being accounted for here.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from service.api_core.operator_authz import OPERATOR_KEY_HEADER
from service.tests._base import FastApiTestCase

SERVICE = Path(__file__).resolve().parents[1]
SECRET = "s3cret-operator-key"
RESERVED = ("dashboard", "Dashboard", "operator", "OPERATOR")
#: Every module that writes an agent row, and the route that must refuse a reserved id before it runs.
AGENT_ROW_WRITERS = {
    "api_core/agent_registration_writes.py": "POST /agents",
    "api_core/agent_rename_writes.py": "POST /agents/{id}/rename",
    "api_core/definition_push.py": "PUT /environments/{id}/agent-definitions (refuses that id, for that entry only)",
    "api_core/running_spawn.py": "POST /spawn-requests (the worker registers under the request's agentId)",
}


def _register_body(agent_id: str) -> dict:
    return {"agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "launchMode": "detached", "sessionHandle": f"{agent_id}-session", "machineId": "win32:host-a",
            "bridgeId": f"{agent_id}-bridge", "capabilities": ["resident-run"]}


class NoAgentCanHoldAnOperatorName(FastApiTestCase):
    DB_NAME = "aify-no-operator-agent-name.db"

    def _agent_ids(self) -> set:
        db = sqlite3.connect(self._db_path)
        try:
            return {row[0] for row in db.execute("SELECT id FROM agents")}
        finally:
            db.close()

    def _each_key_setting(self):
        """No key; a key not presented; a key presented. The refusal holds in all three."""
        yield "no key configured", "", {}
        yield "key configured, not presented", SECRET, {}
        yield "key presented", SECRET, {OPERATOR_KEY_HEADER: SECRET}

    def test_registration_refuses_an_operator_name(self):
        for label, key, headers in self._each_key_setting():
            self.client.app.state.config.operator_key = key
            for name in RESERVED:
                with self.subTest(setting=label, name=name):
                    response = self.client.post("/api/v1/agents", json=_register_body(name), headers=headers)
                    self.assertEqual(response.status_code, 400, response.text[:200])
                    self.assertIn(f"'{name}' is reserved for the operator and cannot be an agent id", response.text)
        self.assertEqual(self._agent_ids() & {n for n in RESERVED}, set(), "a refused registration wrote a row")

    def test_rename_refuses_an_operator_name_and_leaves_the_agent_as_it_was(self):
        self.assertEqual(self.client.post("/api/v1/agents", json=_register_body("attacker")).status_code, 200)
        for label, key, headers in self._each_key_setting():
            self.client.app.state.config.operator_key = key
            for name in RESERVED:
                with self.subTest(setting=label, name=name):
                    response = self.client.post("/api/v1/agents/attacker/rename",
                                                json={"newAgentId": name, "requestedBy": "attacker"}, headers=headers)
                    self.assertEqual(response.status_code, 400, response.text[:200])
        self.assertIn("attacker", self._agent_ids())
        self.assertEqual(self._agent_ids() & set(RESERVED), set())

    def test_a_spawn_request_refuses_an_operator_name(self):
        self.client.app.state.config.operator_key = ""
        for name in RESERVED:
            with self.subTest(name=name):
                response = self.client.post("/api/v1/spawn-requests", json={
                    "environmentId": "no-such-env", "agentId": name, "runtime": "claude-code"})
                self.assertEqual(response.status_code, 400, response.text[:200])
                self.assertIn(f"'{name}' is reserved for the operator and cannot be an agent id", response.text)

    def test_CONTROL_ordinary_names_still_register_and_rename(self):
        """A live agent is called `dashboard-manager`; only the exact operator names are reserved."""
        for name in ("dashboard-manager", "operators", "my-dashboard"):
            with self.subTest(name=name):
                self.assertEqual(self.client.post("/api/v1/agents", json=_register_body(name)).status_code, 200)
        response = self.client.post("/api/v1/agents/my-dashboard/rename",
                                    json={"newAgentId": "renamed-agent", "requestedBy": "my-dashboard"})
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertIn("renamed-agent", self._agent_ids())

    def test_every_agent_row_writer_is_accounted_for(self):
        writers = sorted(str(path.relative_to(SERVICE)).replace("\\", "/")
                         for path in SERVICE.rglob("*.py")
                         if "tests" not in path.parts
                         and re.search(r"INSERT INTO agents\s*\(", path.read_text(encoding="utf-8")))
        self.assertEqual(writers, sorted(AGENT_ROW_WRITERS),
                         "a module writes agent rows that no reserved-id check is known to guard")

    def test_CONTROL_the_writer_census_sees_a_writer(self):
        self.assertTrue(re.search(r"INSERT INTO agents\s*\(", "db.execute('INSERT INTO agents (id) VALUES (?)')"))
