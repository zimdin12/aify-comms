"""Two values aify-env and this service must spell alike, read from both (P0 C3, C7).

- `HARNESS_RUNTIME`: the service builds a defined agent's launch with the runtime its harness runs as,
  and the host refuses a launch whose runtime is not that one. Two tables that disagree refuse every
  start of the harness they disagree on.
- `FREE_SINCE`: the service's reason for an id refused at a revision it has since freed; the host
  publishes a fresh revision on seeing exactly this reason. A rewording here would leave the host
  waiting for its next unrelated change.

aify-env's side is imported by node from its checkout. Both modules are pure: importing them starts
nothing.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from service.api_core.definition_push import FREE_SINCE, HARNESS_RUNTIME
from service.tests.test_agent_definition_push import ENV_REPOS

REQUESTS_MODULE = Path("lib") / "agent-definition-requests.mjs"
SYNC_MODULE = Path("lib") / "plugins" / "aify-comms" / "definition-sync.mjs"


class TheHostAndServiceShareTheDefinitionVocabulary(unittest.TestCase):
    def host_values(self) -> dict:
        repo = next((candidate for candidate in ENV_REPOS if (candidate / SYNC_MODULE).is_file()), None)
        if repo is None:
            self.skipTest("no aify-env checkout with the definition sync; set AIFY_ENV_REPO")
        if shutil.which("node") is None:
            self.skipTest("node is not on PATH")
        script = ("const [requests, sync] = await Promise.all(process.argv.slice(1).map((p) => import(p)));"
                  "console.log(JSON.stringify({harnessRuntime: requests.HARNESS_RUNTIME, freeSince: sync.FREE_SINCE}));")
        run = subprocess.run(["node", "--input-type=module", "-e", script,
                              (repo / REQUESTS_MODULE).as_uri(), (repo / SYNC_MODULE).as_uri()],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_both_tiers_map_each_harness_to_the_same_runtime(self):
        host = self.host_values()
        self.assertTrue(host["harnessRuntime"], "control: the host's table was read")
        self.assertEqual(host["harnessRuntime"], HARNESS_RUNTIME)

    def test_the_host_recognises_the_services_free_since_reason(self):
        self.assertEqual(self.host_values()["freeSince"], FREE_SINCE)
