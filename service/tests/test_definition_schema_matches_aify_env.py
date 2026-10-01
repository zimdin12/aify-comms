"""The service admits a definition by the same C1 rules as aify-env, case for case (P0 C1).

aify-env's fixture `tests/fixtures/agent-definitions/cases.json` lists each body with the problems its
validator reports. The service validates the `agent` object a snapshot publishes, so each case is
compared on its `id:` and `agent*` problems: aify-env computes those apart from the file-level fields
(`version`, the store's receipt fields, `appliedRequest`), which never reach the service.
"""
from __future__ import annotations

import json
import unittest

from service.api_core.definition_push import HARNESS_RUNTIME
from service.api_core.definition_schema import HARNESSES, MISSING, agent_problems
from service.tests.test_agent_definition_push import ENV_REPOS, FIXTURE


def _agent_level(problems: list[str]) -> list[str]:
    return sorted(p for p in problems if p.startswith(("id:", "agent")))


class TheServiceAdmitsAsAifyEnvDoes(unittest.TestCase):
    def test_every_fixture_body_gets_the_same_agent_problems(self):
        fixture = next((repo / FIXTURE for repo in ENV_REPOS if (repo / FIXTURE).is_file()), None)
        if fixture is None:
            self.skipTest("no aify-env checkout with the agent-definition fixture; set AIFY_ENV_REPO")
        cases = [c for c in json.loads(fixture.read_text(encoding="utf-8"))["cases"] if isinstance(c.get("body"), dict)]
        compared, refused = 0, set()
        for case in cases:
            with self.subTest(case=case["name"]):
                expected = _agent_level(case["problems"])
                self.assertEqual(agent_problems(case["body"].get("agent", MISSING), case["fileId"]), expected)
                compared += 1
                refused |= {p.split(": ")[1] for p in expected}
        # CONTROLS: the comparison ran over the fixture, and the fixture exercises refusals, not only
        # valid bodies (a validator that admitted everything would pass a fixture of valid cases).
        self.assertGreaterEqual(compared, 70)
        # Every definable harness has a runtime to be filed under, and nothing else does.
        self.assertEqual(set(HARNESS_RUNTIME), set(HARNESSES))
        self.assertTrue({"missing", "pattern", "reserved-name", "unsupported", "not-absolute", "reserved",
                         "unknown-field", "type", "malformed-unicode", "control", "mismatch"} <= refused, refused)
