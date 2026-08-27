"""The spawn-request LIST does not carry standing instructions; the CLAIM path still does.

MEASURED against the live service on 2026-08-27, by fetching every endpoint in the dashboard's
refresh bundle and weighing each field on the wire with the compact separators a JSON response uses:

    one refresh bundle          424,152 bytes
      /spawn-requests           247,680   58.4%
      /sessions                  99,229   23.4%
      /agents                    68,041   16.0%
      the other four              9,202    2.2%

    inside /spawn-requests (100 rows, capped)
      spawnSpec.instructions     89,790   34.2% of the endpoint, 21.2% of the whole bundle
      spawnSpec.metadata          9,652    3.7%
      spawnSpec.systemPrompt      1,800    0.7%

NOTHING ON THAT PATH READS IT. The only consumer of a spawnSpec in the dashboard is
`spawnRecordLineage` in inspector-forms.mjs, which reads `metadata`. A repo-wide search for
`.instructions` in service/new_dashboard returns nothing at all.

THE BRIDGE DOES read it -- `spawn-loop.mjs:113` builds the agent's prompt from
`spawnRequest.spawnSpec?.instructions` -- but from `POST /spawn-requests/claim`, a different
endpoint with a different call into the same serialiser. That is what makes this scopeable rather
than a removal.

`systemPrompt` stays. At 0.7% it does not justify a contract change on its own, and each omitted
field is one.

WHY OMITTED AND NOT BLANKED: sending "" would state that an agent has no standing instructions,
which is false. An absent key says the list did not carry them. Same rule the quota tool follows for
a missing percentage -- no evidence is not a zero.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.api_core.spawn_requests_io import _spawn_spec_to_dict
from service.tests._base import FastApiTestCase

#: A spec row as the table holds one. Only the columns the serialiser reads need to be here.
SPEC_ROW = {
    "id": "spec-1",
    "agent_id": "npo-agent",
    "environment_id": "linux:test-host:default",
    "runtime": "claude-code",
    "workspace": "/workspace",
    "model": "",
    "profile": "",
    "mode": "managed-warm",
    "system_prompt": "you are a helpful agent",
    "standing_instructions": "STANDING INSTRUCTIONS BODY " * 40,
    "env_vars": "{}",
    "channel_ids": "[]",
    "budget_policy": "{}",
    "context_policy": "{}",
    "restart_policy": "{}",
    "metadata": '{"compactMode": "handoff"}',
    "created_at": "2026-08-27T00:00:00Z",
    "updated_at": "2026-08-27T00:00:00Z",
}


class _Row(dict):
    """A stand-in for an sqlite3.Row: indexable by column name, which is all the serialiser uses."""


class TheSpawnListDoesNotShipPrompts(unittest.TestCase):
    def test_the_default_still_carries_everything(self):
        """Every existing caller is unaffected. The claim and update paths take this branch, and the
        bridge builds the agent's prompt from what they return."""
        spec = _spawn_spec_to_dict(_Row(SPEC_ROW))
        self.assertIn("instructions", spec)
        self.assertEqual(spec["instructions"], SPEC_ROW["standing_instructions"])

    def test_the_list_form_OMITS_it_rather_than_blanking_it(self):
        spec = _spawn_spec_to_dict(_Row(SPEC_ROW), include_instructions=False)
        self.assertNotIn(
            "instructions", spec,
            'the key must be ABSENT, not "" -- an empty string states that the agent has no standing '
            "instructions, which is a different and false claim",
        )

    def test_EVERYTHING_ELSE_survives_the_slim_form(self):
        """The saving must come from one field. A slim form that dropped `metadata` would break the
        one thing the dashboard actually reads off a spawnSpec."""
        full = _spawn_spec_to_dict(_Row(SPEC_ROW))
        slim = _spawn_spec_to_dict(_Row(SPEC_ROW), include_instructions=False)
        self.assertEqual(set(full) - set(slim), {"instructions"})
        for key in slim:
            self.assertEqual(slim[key], full[key], f"{key} changed in the slim form")
        self.assertEqual(slim["metadata"], {"compactMode": "handoff"},
                         "metadata is the only spawnSpec field the dashboard reads")

    def test_the_saving_is_REAL_and_not_a_rounding_error(self):
        """ANTI-VACUITY. If the omitted field were tiny, this whole change would be cost without
        benefit -- so the test asserts the shape of the measurement, not just the absence."""
        import json

        full = len(json.dumps(_spawn_spec_to_dict(_Row(SPEC_ROW)), separators=(",", ":")))
        slim = len(json.dumps(_spawn_spec_to_dict(_Row(SPEC_ROW), include_instructions=False),
                              separators=(",", ":")))
        self.assertLess(slim, full)
        self.assertGreater(
            (full - slim) / full, 0.5,
            "on a spec with real standing instructions the body dominates the row; if it does not, "
            "re-measure before keeping this change",
        )


class TheListEndpointIsSlim(FastApiTestCase):
    """Driven through the app, because the serialiser having the option proves nothing about the
    route using it. A helper suite green while the call site passes the default is the exact shape
    that has bitten this repo repeatedly."""

    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    def setUp(self):
        super().setUp()
        # The spawn-request route refuses an unknown environment, so one has to exist before a
        # request can be created. This is the same heartbeat every environment-aware suite sends.
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:test-host:default", "machineId": "linux:test-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-a", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)

    def _create(self):
        response = self.client.post("/api/v1/spawn-requests", json={
            "agentId": "slim-agent",
            "role": "coder",
            "runtime": "claude-code",
            "environmentId": "linux:test-host:default",
            "workspace": "/workspace",
            "instructions": "STANDING INSTRUCTIONS BODY " * 40,
            "createdBy": "dashboard",
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_list_route_does_not_carry_the_instructions_body(self):
        self._create()
        listed = self.client.get("/api/v1/spawn-requests")
        self.assertEqual(listed.status_code, 200, listed.text)
        rows = listed.json()["spawnRequests"]
        # POSITIVE CONTROL: a row with a spec, or the assertion below passes on an empty list.
        specs = [r["spawnSpec"] for r in rows if r.get("spawnSpec")]
        self.assertTrue(specs, "no spawnSpec came back, so this proves nothing")
        for spec in specs:
            self.assertNotIn("instructions", spec)
            self.assertIn("metadata", spec, "the field the dashboard reads must survive")


if __name__ == "__main__":
    unittest.main()
