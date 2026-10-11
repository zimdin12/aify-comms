"""aify-env's own reader, run against THIS service's real responses.

X-1 PROVED THE ADDRESS AND X-2 THE FIELDS IT SENDS. This is the other direction, and it fails just as
quietly: a reader looking for `ownerMachineId` on a payload that spells it `owner_machine_id` finds
`undefined`, takes its fallback, and decides something. Nothing raises. The symptom is a menu that
starts the wrong agent, or offers none.

IT IS THE PLUGIN'S OWN CODE, NOT A MODEL OF IT. `startable-agents.mjs` is imported and called with
what this service actually answered — a definition pushed through the real route, the roster fetched
through the real route, handed to node. A test that re-implemented those rules would agree with itself
for ever.

WHAT IT COVERS. The reading that gates "start available agent": whether a DEFINED agent is startable
from the host asking. Since D8 the service starts only an agent some host defines, so that is the only
agent the reader offers, and it reads three things off our row: the definition's `state`, its
`ownerMachineId`, and the agent's `status`. Misspell either definition field and every host reads
itself as the owner, so the agent is offered on a machine that is not the one the service starts it on.

WHAT IT DOES NOT COVER: every other response this plugin reads, and any live round trip. Stated so a
green run is not mistaken for the seam being closed.

IT SKIPS BY NAME rather than passing when the checkout is absent, like its sibling.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from service.api_core.definition_snapshot import definition_digest, snapshot_digest
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase
from service.tests.test_the_env_plugin_addresses_routes_this_service_serves import (
    PLUGIN_DIR, env_repo,
)

AGENT_ID = "ef-tester"
MACHINE_ID = "linux:test-host"
ENVIRONMENT_ID = f"{MACHINE_ID}:default"
BRIDGE_ID = "bridge-test-host"
#: A second host holding its own copy of the definition: the service's owner must still decide.
OTHER_MACHINE_ID = "linux:other-host"

#: Node, calling the plugin's OWN reader on what this service answered, once per host asking. Each host
#: passes its own reading of the definition, as `DefinitionStore.list()` gives it.
HARNESS = """
import { readFileSync } from 'node:fs';
import { startabilityOf } from '%(module)s';

const input = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const agent = (input.roster && input.roster.agents) ? input.roster.agents['%(agent)s'] : null;
const definition = { id: '%(agent)s', problems: [], agent: { id: '%(agent)s', mode: 'managed' } };
console.log(JSON.stringify({
  sawAgent: Boolean(agent),
  verdicts: Object.fromEntries(input.hosts.map((machineId) => [machineId, startabilityOf(agent, { machineId, definition })])),
}));
"""


class TheEnvPluginReadsWhatThisServiceAnswers(FastApiTestCase):
    DB_NAME = "aify-test-env-plugin-reads.db"

    def setUp(self):
        super().setUp()
        self.repo, reason = env_repo()
        if self.repo is None:
            self.skipTest(f"{reason}, so the plugin's reader was NOT run against this service's "
                          "answers")
        self._seed()

    def _seed(self) -> None:
        """A MANAGED agent defined by MACHINE_ID, through the routes a host uses to publish it."""
        beat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT_ID, "machineId": MACHINE_ID, "os": "linux", "kind": "linux",
            "bridgeId": BRIDGE_ID, "cwdRoots": ["/work"], "runtimes": [], "metadata": {}})
        self.assertEqual(beat.status_code, 200, beat.text)
        definition = {"id": AGENT_ID, "name": AGENT_ID, "role": "coder", "harness": "claude",
                      "mode": "managed", "workspace": "/work", "model": "", "effort": "",
                      "instructions": "", "env": {}, "herdrSpace": True}
        entries = [{"id": AGENT_ID, "state": "valid", "incarnation": 1, "revision": 1,
                    "definitionDigest": definition_digest(definition), "definition": definition,
                    "available": True}]
        pushed = self.client.put(f"/api/v1/environments/{ENVIRONMENT_ID}/agent-definitions", json={
            "bridgeId": BRIDGE_ID, "machineId": MACHINE_ID, "storeId": "s1", "revision": 1,
            "snapshotDigest": snapshot_digest(entries), "entries": entries})
        self.assertEqual(pushed.status_code, 200, pushed.text)

    # ── the round trip ───────────────────────────────────────────────────────────────────────

    def _roster(self) -> dict:
        roster = self.client.get("/api/v1/agents")
        self.assertEqual(roster.status_code, 200, roster.text)
        return roster.json()

    def _read_by_the_plugin(self, roster: dict, hosts: list[str]) -> dict:
        module = (self.repo / PLUGIN_DIR.parent.parent / "startable-agents.mjs")
        script = Path(tempfile.mkdtemp()) / "read-the-answers.mjs"
        payload = script.with_name("answers.json")
        payload.write_text(json.dumps({"roster": roster, "hosts": hosts}), encoding="utf-8")
        script.write_text(HARNESS % {"module": module.as_uri(), "agent": AGENT_ID}, encoding="utf-8")
        done = subprocess.run(["node", str(script), str(payload)],
                              cwd=self.repo, capture_output=True, text=True)
        if done.returncode != 0:
            raise AssertionError(
                f"the plugin's reader could not be run (exit {done.returncode}): "
                f"{done.stdout[-400:]}{done.stderr[-400:]}")
        lines = [l for l in done.stdout.splitlines() if l.startswith("{")]
        if not lines:
            raise AssertionError(f"the reader printed nothing: {done.stdout[-400:]}")
        read = json.loads(lines[-1])
        self.assertTrue(read["sawAgent"], "the plugin could not find the agent in our roster")
        return read["verdicts"]

    def test_the_service_answered_something_to_read(self):
        """The control. A roster without the defined agent satisfies nothing below for the right reason."""
        roster = self._roster()
        self.assertIn(AGENT_ID, roster.get("agents") or {}, roster)

    def test_the_owner_finds_this_agent_startable_from_our_answer(self):
        """The host that defines it, asking with its own definition, is offered the start.

        `status` is read off our row: a status the reader does not know refuses, so a renamed or
        re-spelled status would leave the menu silently empty.
        """
        verdict = self._read_by_the_plugin(self._roster(), [MACHINE_ID])[MACHINE_ID]
        self.assertTrue(verdict["startable"],
                        f"the plugin refused, on its owner, an agent we serve as startable: {verdict}")

    def test_another_host_is_told_the_owner_we_answered_with(self):
        """A host holding its own copy is refused, by the owner this service names.

        This is what makes `definition.state` and `definition.ownerMachineId` load-bearing: misspell
        either and the reader skips the owner check, so this host's copy decides and the agent is
        offered here too.
        """
        verdict = self._read_by_the_plugin(self._roster(), [OTHER_MACHINE_ID])[OTHER_MACHINE_ID]
        self.assertEqual(verdict, {"startable": False, "reason": f"defined on {MACHINE_ID}: it is started there"},
                         "the plugin did not read the owner we answered with")


if __name__ == "__main__":
    unittest.main()
